from __future__ import annotations

from collections import defaultdict
from statistics import mean, median, pstdev
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .collaboration_data import run_pattern_query, run_query
from .collaboration_utils import node_id


OBJECTIVE_TYPES = ("Mission", "Segment")


def objective_key(log_name: str, objective_type: str, objective_id: str) -> str:
    """Return a stable key for an objective instance within one execution log."""
    return f"{log_name}\x1f{objective_type}\x1f{objective_id}"


def fetch_objective_references(
    driver: Any,
    database: Optional[str],
    log_names: Sequence[str],
    objective_type: str,
) -> List[Dict[str, str]]:
    """Return objective instances belonging to selected execution logs."""
    if objective_type not in OBJECTIVE_TYPES:
        raise ValueError(f"Unsupported objective type: {objective_type}")
    if not log_names:
        return []
    query = """
    UNWIND $log_names AS log_name
    MATCH (objective:Entity {type: $objective_type})
    WHERE objective.Log = log_name
       OR EXISTS {
         MATCH (event:Event {Log: log_name})-[:CORR]->(objective)
       }
    RETURN DISTINCT toString(log_name) AS log_name,
           toString(objective.id) AS objective_id
    ORDER BY log_name, objective_id
    """
    return [
        {
            "log_name": str(row["log_name"]),
            "objective_type": objective_type,
            "objective_id": str(row["objective_id"]),
        }
        for row in run_query(
            driver,
            database,
            query,
            {"log_names": list(log_names), "objective_type": objective_type},
        )
        if row.get("log_name") is not None and row.get("objective_id") is not None
    ]


def fetch_objective_ids(
    driver: Any,
    database: Optional[str],
    log_name: str,
    objective_type: str,
) -> List[str]:
    """Return non-empty objective instances represented by Task events in one log."""
    if objective_type not in OBJECTIVE_TYPES:
        raise ValueError(f"Unsupported objective type: {objective_type}")
    return [
        row["objective_id"]
        for row in fetch_objective_references(
            driver, database, [log_name], objective_type
        )
    ]


def fetch_selected_objective_task_rows(
    driver: Any,
    database: Optional[str],
    objectives: Sequence[Mapping[str, str]],
) -> List[Dict[str, Any]]:
    """Fetch Task, robot, duration, and requirement rows for resolved objectives."""
    if not objectives:
        return []
    objective_types = {str(item["objective_type"]) for item in objectives}
    if not objective_types.issubset(OBJECTIVE_TYPES):
        raise ValueError(f"Unsupported objective types: {sorted(objective_types)}")
    parameters = {
        "objectives": [
            {
                "log_name": str(item["log_name"]),
                "objective_type": str(item["objective_type"]),
                "objective_id": str(item["objective_id"]),
            }
            for item in objectives
        ]
    }
    query = """
    UNWIND $objectives AS selected
    MATCH (event:Event {Type: 'Task'})-[:CORR]->(objective:Entity {type: selected.objective_type})
    WHERE event.Log = selected.log_name
      AND toString(objective.id) = selected.objective_id
    OPTIONAL MATCH (event)-[:CORR]->(robot:Entity {type: 'Robot'})
    OPTIONAL MATCH (event)-[:REQ]->(capability:Capability)
    WITH selected, event, objective, robot,
         collect(DISTINCT coalesce(toString(capability.name), toString(capability.id))) AS capabilities
    RETURN toString(selected.log_name) AS log_name,
           toString(objective.type) AS objective_type,
           toString(objective.id) AS objective_id,
           toString(event.event_id) AS event_id,
           coalesce(toString(event.activity), toString(event.event_id)) AS activity,
           CASE WHEN robot IS NULL THEN null ELSE toString(robot.id) END AS robot_id,
           CASE
             WHEN event.start IS NOT NULL AND event.end IS NOT NULL AND event.end >= event.start
             THEN toFloat(event.end.epochMillis - event.start.epochMillis) / 1000.0
             ELSE null
           END AS duration_seconds,
           [capability IN capabilities WHERE capability IS NOT NULL] AS capabilities
    ORDER BY log_name, objective_id, event.start, event.end, event_id, robot_id
    """
    return run_query(driver, database, query, parameters)


def fetch_objective_task_rows(
    driver: Any,
    database: Optional[str],
    log_name: str,
    objective_type: str,
    objective_id: str,
) -> List[Dict[str, Any]]:
    """Fetch Task executions, assigned robots, durations, and requirements for one objective."""
    if objective_type not in OBJECTIVE_TYPES:
        raise ValueError(f"Unsupported objective type: {objective_type}")
    return fetch_selected_objective_task_rows(
        driver,
        database,
        [{
            "log_name": log_name,
            "objective_type": objective_type,
            "objective_id": str(objective_id),
        }],
    )


def fetch_robot_capability_rows_for_logs(
    driver: Any,
    database: Optional[str],
    log_names: Sequence[str],
) -> List[Dict[str, Any]]:
    """Fetch the complete robot roster with optional declared capabilities."""
    if not log_names:
        return []
    query = """
    UNWIND $log_names AS log_name
    MATCH (robot:Entity {type: 'Robot'})
    WHERE robot.Log = log_name
       OR EXISTS {
         MATCH (event:Event {Log: log_name})-[:CORR]->(robot)
       }
    OPTIONAL MATCH (robot)-[:HAS]->(capability:Capability)
    RETURN DISTINCT toString(log_name) AS log_name,
           toString(robot.id) AS robot_id,
           CASE WHEN capability IS NULL THEN null
                ELSE coalesce(toString(capability.name), toString(capability.id)) END AS capability
    ORDER BY log_name, capability, robot_id
    """
    return run_query(driver, database, query, {"log_names": list(log_names)})


def fetch_robot_capability_rows(
    driver: Any,
    database: Optional[str],
    log_name: str,
) -> List[Dict[str, Any]]:
    """Fetch declared capability providers belonging to the selected execution log."""
    return fetch_robot_capability_rows_for_logs(driver, database, [log_name])


def filter_objective_events(
    rows: Iterable[Mapping[str, Any]],
    objective_type: str,
    objective_id: str,
) -> List[Dict[str, Any]]:
    """Filter normalized event rows to one exact objective instance."""
    return [
        dict(row)
        for row in rows
        if str(row.get("objective_type") or "") == objective_type
        and str(row.get("objective_id") or "") == str(objective_id)
    ]


def _unique_capabilities(value: Any) -> List[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return sorted({str(item) for item in value if item is not None and str(item).strip()})


def _event_identity(row: Mapping[str, Any], index: int) -> str:
    value = row.get("event_id")
    return str(value) if value is not None else f"__row_{index}"


def _provider_map(provider_rows: Iterable[Mapping[str, Any]]) -> Dict[str, Set[str]]:
    providers: Dict[str, Set[str]] = defaultdict(set)
    for row in provider_rows:
        capability = row.get("capability")
        robot_id = row.get("robot_id")
        if capability is not None and robot_id is not None:
            providers[str(capability)].add(str(robot_id))
    return providers


def compute_resource_metrics(
    task_rows: Sequence[Mapping[str, Any]],
    provider_rows: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Compute contribution and capability diagnostics for one objective instance."""
    events: Dict[str, Dict[str, Any]] = {}
    robot_event_ids: Dict[str, Set[str]] = defaultdict(set)
    robot_duration_by_event: Dict[str, Dict[str, float]] = defaultdict(dict)
    utilization: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
    used_by_capability: Dict[str, Set[str]] = defaultdict(set)

    for index, raw_row in enumerate(task_rows):
        row = dict(raw_row)
        event_id = _event_identity(row, index)
        capabilities = _unique_capabilities(row.get("capabilities"))
        duration = row.get("duration_seconds")
        numeric_duration = (
            float(duration)
            if isinstance(duration, (int, float)) and not isinstance(duration, bool) and float(duration) >= 0
            else None
        )
        event = events.setdefault(
            event_id,
            {
                "event_id": event_id,
                "activity": row.get("activity"),
                "duration_seconds": numeric_duration,
                "capabilities": set(),
            },
        )
        event["capabilities"].update(capabilities)
        if event.get("duration_seconds") is None and numeric_duration is not None:
            event["duration_seconds"] = numeric_duration

        robot_id = row.get("robot_id")
        if robot_id is None or not str(robot_id).strip():
            continue
        robot = str(robot_id)
        robot_event_ids[robot].add(event_id)
        if numeric_duration is not None:
            robot_duration_by_event[robot][event_id] = numeric_duration
        for capability in capabilities:
            utilization[(robot, capability)].add(event_id)
            used_by_capability[capability].add(robot)

    total_tasks = len(events)
    timed_events = {
        event_id: float(event["duration_seconds"])
        for event_id, event in events.items()
        if isinstance(event.get("duration_seconds"), (int, float))
    }
    total_execution_seconds = sum(timed_events.values())
    duration_complete = total_tasks > 0 and len(timed_events) == total_tasks
    duration_share_available = duration_complete and total_execution_seconds > 0

    roster_robots = {
        str(row["robot_id"])
        for row in provider_rows
        if row.get("robot_id") is not None and str(row["robot_id"]).strip()
    }
    involved_robots = set(robot_event_ids)
    all_robots = involved_robots | roster_robots
    contribution_rows: List[Dict[str, Any]] = []
    for robot in sorted(all_robots):
        task_count = len(robot_event_ids[robot])
        execution_seconds = sum(robot_duration_by_event.get(robot, {}).values())
        task_share = task_count / total_tasks if total_tasks else 0.0
        time_share = (
            execution_seconds / total_execution_seconds
            if duration_share_available
            else None
        )
        contribution_rows.append({
            "robot_id": robot,
            "task_count": task_count,
            "total_task_count": total_tasks,
            "task_share": task_share,
            "task_percentage": task_share * 100.0,
            "execution_seconds": execution_seconds if duration_complete else None,
            "time_share": time_share,
            "time_percentage": time_share * 100.0 if time_share is not None else None,
        })
    contribution_rows.sort(key=lambda row: (-row["task_count"], row["robot_id"]))

    demand_event_ids: Dict[str, Set[str]] = defaultdict(set)
    for event_id, event in events.items():
        for capability in event["capabilities"]:
            demand_event_ids[str(capability)].add(event_id)

    capability_demand = [
        {
            "capability": capability,
            "required_task_executions": len(event_ids),
            "total_task_count": total_tasks,
            "task_share": len(event_ids) / total_tasks if total_tasks else 0.0,
            "task_percentage": (len(event_ids) / total_tasks * 100.0) if total_tasks else 0.0,
        }
        for capability, event_ids in demand_event_ids.items()
    ]
    capability_demand.sort(key=lambda row: (-row["required_task_executions"], row["capability"]))

    providers = _provider_map(provider_rows)
    availability_rows: List[Dict[str, Any]] = []
    capability_workload_rows: List[Dict[str, Any]] = []
    for demand in capability_demand:
        capability = str(demand["capability"])
        capable = providers.get(capability, set())
        involved_capable = capable & involved_robots
        actually_used = used_by_capability.get(capability, set())
        required_count = int(demand["required_task_executions"])
        provider_count = len(capable)
        team_size = len(all_robots)
        availability_rows.append({
            "capability": capability,
            "required_task_executions": required_count,
            "required_task_percentage": demand["task_percentage"],
            "availability": provider_count,
            "team_size": team_size,
            "availability_rate": (
                provider_count / team_size if team_size else None
            ),
            "availability_percentage": (
                provider_count / team_size * 100.0 if team_size else None
            ),
            "demand_availability": (
                required_count / provider_count if provider_count else None
            ),
            "capable_robots": sorted(capable),
            "involved_capable_robots": sorted(involved_capable),
            "actually_used_robots": sorted(actually_used),
        })
        for robot in sorted(actually_used):
            used_count = len(utilization[(robot, capability)])
            share = used_count / required_count if required_count else 0.0
            capability_workload_rows.append({
                "capability": capability,
                "robot_id": robot,
                "task_count": used_count,
                "capability_task_count": required_count,
                "workload_share": share,
                "workload_percentage": share * 100.0,
                "declared_capable": robot in capable,
            })

    utilization_rows = [
        {
            "robot_id": robot,
            "capability": capability,
            "task_count": len(utilization.get((robot, capability), set())),
        }
        for robot in sorted(all_robots)
        for capability in sorted(demand_event_ids)
    ]

    dominance = (
        contribution_rows[0]["task_share"]
        if contribution_rows and total_tasks and involved_robots
        else None
    )
    dominant_robots = [
        row["robot_id"]
        for row in contribution_rows
        if dominance is not None
        and row["task_count"] > 0
        and row["task_share"] == dominance
    ]
    return {
        "summary": {
            "robot_count": len(involved_robots),
            "fleet_robot_count": len(all_robots),
            "task_count": total_tasks,
            "dominant_robot": ", ".join(dominant_robots) if dominant_robots else None,
            "dominant_robots": dominant_robots,
            "dominance": dominance,
            "dominance_percentage": dominance * 100.0 if dominance is not None else None,
            "distinct_capability_count": len(demand_event_ids),
            "timed_task_count": len(timed_events),
            "total_execution_seconds": total_execution_seconds if duration_complete else None,
            "duration_complete": duration_complete,
            "duration_share_available": duration_share_available,
        },
        "robot_contributions": contribution_rows,
        "capability_demand": capability_demand,
        "capability_availability": availability_rows,
        "capability_utilization": utilization_rows,
        "capability_workload": capability_workload_rows,
    }


def _distribution_statistics(values: Sequence[float]) -> Dict[str, Optional[float]]:
    """Return descriptive statistics with safe empty-distribution handling."""
    numeric = [float(value) for value in values]
    if not numeric:
        return {
            "mean": None,
            "median": None,
            "minimum": None,
            "maximum": None,
            "standard_deviation": None,
        }
    return {
        "mean": mean(numeric),
        "median": median(numeric),
        "minimum": min(numeric),
        "maximum": max(numeric),
        "standard_deviation": pstdev(numeric),
    }


def _normalized_objectives(
    selected_objectives: Sequence[Mapping[str, str]],
) -> List[Dict[str, str]]:
    seen: Set[str] = set()
    result: List[Dict[str, str]] = []
    for item in selected_objectives:
        normalized = {
            "log_name": str(item["log_name"]),
            "objective_type": str(item["objective_type"]),
            "objective_id": str(item["objective_id"]),
        }
        key = objective_key(**normalized)
        if key not in seen:
            seen.add(key)
            result.append(normalized)
    return result


def _instance_resource_metrics(
    task_rows: Sequence[Mapping[str, Any]],
    provider_rows: Sequence[Mapping[str, Any]],
    selected_objectives: Sequence[Mapping[str, str]],
) -> List[Dict[str, Any]]:
    """Reuse instance metrics for every resolved objective, including empty instances."""
    rows_by_objective: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for row in task_rows:
        key = objective_key(
            str(row.get("log_name") or ""),
            str(row.get("objective_type") or ""),
            str(row.get("objective_id") or ""),
        )
        rows_by_objective[key].append(row)

    providers_by_log: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    unscoped_providers: List[Mapping[str, Any]] = []
    for row in provider_rows:
        if row.get("log_name") is None:
            unscoped_providers.append(row)
        else:
            providers_by_log[str(row["log_name"])].append(row)

    instances: List[Dict[str, Any]] = []
    for objective in _normalized_objectives(selected_objectives):
        key = objective_key(**objective)
        scoped_providers = [
            *unscoped_providers,
            *providers_by_log.get(objective["log_name"], []),
        ]
        metrics = compute_resource_metrics(rows_by_objective.get(key, []), scoped_providers)
        metrics["objective"] = {**objective, "key": key}
        instances.append(metrics)
    return instances


def compute_robot_participation(
    instance_metrics: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Compute objective participation frequency and pooled workload per robot."""
    objective_count = len(instance_metrics)
    total_tasks = sum(int(item.get("summary", {}).get("task_count", 0)) for item in instance_metrics)
    robot_tasks: Dict[str, int] = defaultdict(int)
    robot_objectives: Dict[str, Set[str]] = defaultdict(set)
    for item in instance_metrics:
        objective = item.get("objective", {})
        key = str(objective.get("key") or "")
        for row in item.get("robot_contributions", []):
            robot = str(row["robot_id"])
            robot_tasks[robot] += int(row["task_count"])
            if int(row["task_count"]) > 0:
                robot_objectives[robot].add(key)

    return [
        {
            "robot_id": robot,
            "objective_count": len(robot_objectives[robot]),
            "selected_objective_count": objective_count,
            "participation_frequency": (
                len(robot_objectives[robot]) / objective_count if objective_count else 0.0
            ),
            "participation_percentage": (
                len(robot_objectives[robot]) / objective_count * 100.0 if objective_count else 0.0
            ),
            "total_task_count": robot_tasks[robot],
            "all_task_count": total_tasks,
            "pooled_workload_share": robot_tasks[robot] / total_tasks if total_tasks else 0.0,
            "pooled_workload_percentage": (
                robot_tasks[robot] / total_tasks * 100.0 if total_tasks else 0.0
            ),
        }
        for robot in sorted(robot_tasks, key=lambda item: (-robot_tasks[item], item))
    ]


def compute_robot_contribution_distribution(
    instance_metrics: Sequence[Mapping[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Describe per-objective robot contribution, including zero non-participation values."""
    robots = sorted({
        str(row["robot_id"])
        for item in instance_metrics
        for row in item.get("robot_contributions", [])
    })
    distribution_rows: List[Dict[str, Any]] = []
    summary_rows: List[Dict[str, Any]] = []
    for robot in robots:
        values: List[float] = []
        participated = 0
        for item in instance_metrics:
            contributions = {
                str(row["robot_id"]): float(row["task_share"])
                for row in item.get("robot_contributions", [])
            }
            value = contributions.get(robot, 0.0)
            if value > 0:
                participated += 1
            values.append(value)
            objective = item.get("objective", {})
            distribution_rows.append({
                "robot_id": robot,
                "log_name": objective.get("log_name"),
                "objective_type": objective.get("objective_type"),
                "objective_id": objective.get("objective_id"),
                "contribution": value,
                "contribution_percentage": value * 100.0,
                "participated": value > 0,
            })
        stats = _distribution_statistics(values)
        summary_rows.append({
            "robot_id": robot,
            "objectives_participated": participated,
            "selected_objective_count": len(instance_metrics),
            "mean_contribution": stats["mean"],
            "median_contribution": stats["median"],
            "minimum_contribution": stats["minimum"],
            "maximum_contribution": stats["maximum"],
            "standard_deviation": stats["standard_deviation"],
        })
    summary_rows.sort(
        key=lambda row: (-(row["mean_contribution"] or 0.0), row["robot_id"])
    )
    return summary_rows, distribution_rows


def compute_objective_distributions(
    instance_metrics: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Compute objective dominance and participating-robot distributions."""
    objective_rows: List[Dict[str, Any]] = []
    dominance_values: List[float] = []
    robot_count_values: List[float] = []
    for item in instance_metrics:
        objective = dict(item.get("objective", {}))
        summary = dict(item.get("summary", {}))
        dominance = summary.get("dominance")
        robot_count = int(summary.get("robot_count", 0))
        robot_count_values.append(float(robot_count))
        if isinstance(dominance, (int, float)):
            dominance_values.append(float(dominance))
        objective_rows.append({
            **objective,
            "task_count": int(summary.get("task_count", 0)),
            "robot_count": robot_count,
            "dominance": float(dominance) if isinstance(dominance, (int, float)) else None,
            "dominance_percentage": (
                float(dominance) * 100.0 if isinstance(dominance, (int, float)) else None
            ),
            "dominant_robots": list(summary.get("dominant_robots", [])),
            "capability_count": int(summary.get("distinct_capability_count", 0)),
            "empty": int(summary.get("task_count", 0)) == 0,
        })

    dominance_stats = _distribution_statistics(dominance_values)
    dominance_thresholds = {
        str(threshold): (
            sum(value > threshold for value in dominance_values) / len(dominance_values)
            if dominance_values else None
        )
        for threshold in (0.5, 0.75, 0.9)
    }
    return {
        "objective_rows": objective_rows,
        "dominance_statistics": {
            **dominance_stats,
            "valid_objective_count": len(dominance_values),
            "threshold_shares": dominance_thresholds,
        },
        "robot_count_statistics": {
            **_distribution_statistics(robot_count_values),
            "objective_count": len(robot_count_values),
        },
    }


def compute_aggregated_capability_metrics(
    instance_metrics: Sequence[Mapping[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Aggregate capability demand, use frequency, workload, and concentration."""
    selected_count = len(instance_metrics)
    demand_tasks: Dict[str, int] = defaultdict(int)
    demand_objectives: Dict[str, Set[str]] = defaultdict(set)
    providers: Dict[str, Set[str]] = defaultdict(set)
    used_tasks: Dict[Tuple[str, str], int] = defaultdict(int)
    used_objectives: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
    all_robots: Set[str] = set()

    for item in instance_metrics:
        key = str(item.get("objective", {}).get("key") or "")
        all_robots.update(
            str(row["robot_id"])
            for row in item.get("robot_contributions", [])
        )
        for row in item.get("capability_demand", []):
            capability = str(row["capability"])
            demand_tasks[capability] += int(row["required_task_executions"])
            demand_objectives[capability].add(key)
        for row in item.get("capability_availability", []):
            capability = str(row["capability"])
            providers[capability].update(str(robot) for robot in row["capable_robots"])
        for row in item.get("capability_utilization", []):
            count = int(row["task_count"])
            if count <= 0:
                continue
            pair = (str(row["robot_id"]), str(row["capability"]))
            used_tasks[pair] += count
            used_objectives[pair].add(key)

    demand_rows: List[Dict[str, Any]] = []
    utilization_rows: List[Dict[str, Any]] = []
    concentration_rows: List[Dict[str, Any]] = []
    for capability in sorted(demand_tasks, key=lambda item: (-demand_tasks[item], item)):
        relevant_objectives = len(demand_objectives[capability])
        total_capability_tasks = demand_tasks[capability]
        used_robots = sorted({robot for robot, cap in used_tasks if cap == capability})
        demand_rows.append({
            "capability": capability,
            "total_task_executions": total_capability_tasks,
            "objective_count": relevant_objectives,
            "selected_objective_count": selected_count,
            "objective_frequency": relevant_objectives / selected_count if selected_count else 0.0,
            "objective_percentage": (
                relevant_objectives / selected_count * 100.0 if selected_count else 0.0
            ),
            "capable_robots": sorted(providers.get(capability, set())),
            "actually_used_robots": used_robots,
        })
        workload_shares: List[float] = []
        for robot in sorted(all_robots):
            pair = (robot, capability)
            task_count = used_tasks.get(pair, 0)
            objective_use_count = len(used_objectives.get(pair, set()))
            workload_share = task_count / total_capability_tasks if total_capability_tasks else 0.0
            workload_shares.append(workload_share)
            utilization_rows.append({
                "robot_id": robot,
                "capability": capability,
                "total_task_executions": task_count,
                "capability_workload_share": workload_share,
                "capability_workload_percentage": workload_share * 100.0,
                "objective_count": objective_use_count,
                "relevant_objective_count": relevant_objectives,
                "capability_use_frequency": (
                    objective_use_count / relevant_objectives if relevant_objectives else 0.0
                ),
                "capability_use_percentage": (
                    objective_use_count / relevant_objectives * 100.0
                    if relevant_objectives else 0.0
                ),
                "declared_capable": robot in providers.get(capability, set()),
            })
        concentration_rows.append({
            "capability": capability,
            "capability_dominance": max(workload_shares, default=0.0),
            "capability_dominance_percentage": max(workload_shares, default=0.0) * 100.0,
            "used_robot_count": len(used_robots),
            "capable_robot_count": len(providers.get(capability, set())),
            "total_task_executions": total_capability_tasks,
        })

    return {
        "capability_demand": demand_rows,
        "capability_utilization": utilization_rows,
        "capability_concentration": concentration_rows,
    }


def compute_aggregated_resource_metrics(
    task_rows: Sequence[Mapping[str, Any]],
    provider_rows: Sequence[Mapping[str, Any]],
    selected_objectives: Sequence[Mapping[str, str]],
    *,
    include_groups: bool = True,
) -> Dict[str, Any]:
    """Aggregate shared instance-level resource metrics over explicit objectives."""
    objectives = _normalized_objectives(selected_objectives)
    instances = _instance_resource_metrics(task_rows, provider_rows, objectives)
    participation = compute_robot_participation(instances)
    contribution_summary, contribution_distribution = compute_robot_contribution_distribution(instances)
    objective_distributions = compute_objective_distributions(instances)
    capabilities = compute_aggregated_capability_metrics(instances)
    objective_rows = objective_distributions["objective_rows"]
    valid_dominance = [
        float(row["dominance"])
        for row in objective_rows
        if isinstance(row.get("dominance"), (int, float))
    ]
    robot_counts = [float(row["robot_count"]) for row in objective_rows]
    demand_availability_rows = [
        {
            "log_name": str(item.get("objective", {}).get("log_name") or ""),
            "objective_type": str(item.get("objective", {}).get("objective_type") or ""),
            "objective_id": str(item.get("objective", {}).get("objective_id") or ""),
            "capability": str(row["capability"]),
            "req_count": int(row["required_task_executions"]),
            "availability": int(row["availability"]),
            "team_size": int(row["team_size"]),
            "availability_rate": row.get("availability_rate"),
            "availability_percentage": row.get("availability_percentage"),
            "demand_availability": row.get("demand_availability"),
        }
        for item in instances
        for row in item.get("capability_availability", [])
    ]

    payload: Dict[str, Any] = {
        "summary": {
            "objective_count": len(objectives),
            "nonempty_objective_count": sum(not row["empty"] for row in objective_rows),
            "empty_objective_count": sum(row["empty"] for row in objective_rows),
            "total_task_count": sum(int(row["task_count"]) for row in objective_rows),
            "distinct_robot_count": len(participation),
            "mean_robots_per_objective": mean(robot_counts) if robot_counts else None,
            "mean_dominance": mean(valid_dominance) if valid_dominance else None,
            "distinct_capability_count": len(capabilities["capability_demand"]),
        },
        "instances": instances,
        "robot_participation": participation,
        "robot_contribution_summary": contribution_summary,
        "robot_contribution_distribution": contribution_distribution,
        "capability_demand_availability": demand_availability_rows,
        **objective_distributions,
        **capabilities,
    }

    if include_groups:
        groups: List[Dict[str, Any]] = []
        for log_name in sorted({item["log_name"] for item in objectives}):
            group_objectives = [item for item in objectives if item["log_name"] == log_name]
            group_keys = {objective_key(**item) for item in group_objectives}
            group_tasks = [
                row
                for row in task_rows
                if objective_key(
                    str(row.get("log_name") or ""),
                    str(row.get("objective_type") or ""),
                    str(row.get("objective_id") or ""),
                ) in group_keys
            ]
            group_providers = [
                row
                for row in provider_rows
                if row.get("log_name") is None or str(row.get("log_name")) == log_name
            ]
            group_payload = compute_aggregated_resource_metrics(
                group_tasks,
                group_providers,
                group_objectives,
                include_groups=False,
            )
            capability_dominance = [
                float(row["capability_dominance"])
                for row in group_payload["capability_concentration"]
            ]
            robot_contribution_means = [
                float(row["mean_contribution"])
                for row in group_payload["robot_contribution_summary"]
                if isinstance(row.get("mean_contribution"), (int, float))
            ]
            groups.append({
                "group": log_name,
                **group_payload["summary"],
                "mean_robot_contribution": (
                    mean(robot_contribution_means) if robot_contribution_means else None
                ),
                "mean_capability_dominance": (
                    mean(capability_dominance) if capability_dominance else None
                ),
            })
        payload["group_comparison"] = groups
    else:
        payload["group_comparison"] = []
    return payload


def _row_involves_objective(
    query_name: str,
    row: Mapping[str, Any],
    objective_type: str,
    objective_id: str,
) -> bool:
    target = str(objective_id)
    if query_name.startswith(("handover_", "capability_driven_return_")):
        return node_id(row.get("objective")) == target
    if query_name.startswith("objective_switch_"):
        return target in {
            node_id(row.get("fromObjective")),
            node_id(row.get("toObjective")),
        }
    if query_name == "parallel_collaboration_mission" and objective_type == "Mission":
        return target in {node_id(row.get("mission1")), node_id(row.get("mission2"))}
    if query_name == "parallel_collaboration_segment" and objective_type == "Segment":
        return target in {node_id(row.get("segment1")), node_id(row.get("segment2"))}
    return False


def _involved_objective_ids(
    query_name: str,
    row: Mapping[str, Any],
    objective_type: str,
) -> Set[str]:
    """Return all objective IDs involved in one formal occurrence row."""
    if query_name.startswith(("handover_", "capability_driven_return_")):
        value = node_id(row.get("objective"))
        return {value} if value else set()
    if query_name.startswith("objective_switch_"):
        return {
            value
            for value in (
                node_id(row.get("fromObjective")),
                node_id(row.get("toObjective")),
            )
            if value
        }
    if query_name == "parallel_collaboration_mission" and objective_type == "Mission":
        return {
            value
            for value in (node_id(row.get("mission1")), node_id(row.get("mission2")))
            if value
        }
    if query_name == "parallel_collaboration_segment" and objective_type == "Segment":
        return {
            value
            for value in (node_id(row.get("segment1")), node_id(row.get("segment2")))
            if value
        }
    return set()


def aggregate_collaboration_patterns(
    selected_objectives: Sequence[Mapping[str, str]],
    occurrence_rows: Mapping[Tuple[str, str], Sequence[Mapping[str, Any]]],
) -> Dict[str, Any]:
    """Aggregate reused formal occurrences over explicit objective instances."""
    objectives = _normalized_objectives(selected_objectives)
    objective_keys = {objective_key(**item) for item in objectives}
    objective_count = len(objectives)
    per_objective: Dict[str, Dict[str, int]] = {
        key: defaultdict(int) for key in objective_keys
    }
    labels = {
        "handover": "Robot handover",
        "objective_switch": "Objective switch",
        "capability_driven_return": "Capability-driven return",
        "parallel_collaboration": "Parallel collaboration",
    }
    structure_rows: List[Dict[str, Any]] = []
    group_totals: Dict[Tuple[str, str], int] = defaultdict(int)

    objective_lookup: Dict[Tuple[str, str], str] = {
        (item["log_name"], item["objective_id"]): objective_key(**item)
        for item in objectives
    }
    for (log_name, query_name), rows in occurrence_rows.items():
        base_name = next(
            (name for name in labels if query_name.startswith(name)),
            query_name,
        )
        unique_occurrences = 0
        involved_keys: Set[str] = set()
        incidence_count = 0
        log_objective_count = sum(
            item["log_name"] == log_name for item in objectives
        )
        for row in rows:
            involved_ids = _involved_objective_ids(
                query_name,
                row,
                str(next((item["objective_type"] for item in objectives), "Mission")),
            )
            selected_keys = {
                objective_lookup[(log_name, objective_id)]
                for objective_id in involved_ids
                if (log_name, objective_id) in objective_lookup
            }
            if not selected_keys:
                continue
            unique_occurrences += 1
            group_totals[(log_name, base_name)] += 1
            for key in selected_keys:
                per_objective[key][base_name] += 1
                involved_keys.add(key)
                incidence_count += 1
        structure_rows.append({
            "structure": labels.get(base_name, base_name),
            "structure_key": base_name,
            "log_name": log_name,
            "total_occurrences": unique_occurrences,
            "objective_incidence_count": incidence_count,
            "average_per_objective": (
                incidence_count / log_objective_count if log_objective_count else 0.0
            ),
            "objectives_with_occurrence": len(involved_keys),
            "objective_coverage": (
                len(involved_keys) / log_objective_count if log_objective_count else 0.0
            ),
            "objective_coverage_percentage": (
                len(involved_keys) / log_objective_count * 100.0
                if log_objective_count else 0.0
            ),
        })

    combined: Dict[str, Dict[str, Any]] = {}
    for row in structure_rows:
        key = str(row["structure_key"])
        item = combined.setdefault(key, {
            "structure": row["structure"],
            "structure_key": key,
            "total_occurrences": 0,
            "objective_incidence_count": 0,
            "objective_keys": set(),
        })
        item["total_occurrences"] += int(row["total_occurrences"])
        item["objective_incidence_count"] += int(row["objective_incidence_count"])
    for key, counts in per_objective.items():
        for structure, count in counts.items():
            if count > 0 and structure in combined:
                combined[structure]["objective_keys"].add(key)

    combined_rows: List[Dict[str, Any]] = []
    for item in combined.values():
        objectives_with = len(item.pop("objective_keys"))
        incidence_count = int(item["objective_incidence_count"])
        combined_rows.append({
            **item,
            "average_per_objective": incidence_count / objective_count if objective_count else 0.0,
            "objectives_with_occurrence": objectives_with,
            "objective_coverage": objectives_with / objective_count if objective_count else 0.0,
            "objective_coverage_percentage": (
                objectives_with / objective_count * 100.0 if objective_count else 0.0
            ),
        })
    combined_rows.sort(key=lambda row: row["structure"])
    return {
        "collaboration_structures": combined_rows,
        "collaboration_by_log": structure_rows,
        "collaboration_per_objective": {
            key: dict(counts) for key, counts in per_objective.items()
        },
        "collaboration_group_totals": {
            f"{log_name}\x1f{structure}": count
            for (log_name, structure), count in group_totals.items()
        },
    }


def fetch_aggregated_collaboration_patterns(
    driver: Any,
    database: Optional[str],
    catalog: Mapping[str, Mapping[str, str]],
    selected_objectives: Sequence[Mapping[str, str]],
) -> Dict[str, Any]:
    """Execute each existing formal pattern query once per selected log."""
    objectives = _normalized_objectives(selected_objectives)
    if not objectives:
        return aggregate_collaboration_patterns([], {})
    objective_type = objectives[0]["objective_type"]
    suffix = objective_type.lower()
    query_names = (
        f"handover_{suffix}",
        f"objective_switch_{suffix}",
        f"capability_driven_return_{suffix}",
        f"parallel_collaboration_{suffix}",
    )
    occurrence_rows: Dict[Tuple[str, str], Sequence[Mapping[str, Any]]] = {}
    for log_name in sorted({item["log_name"] for item in objectives}):
        for query_name in query_names:
            query = catalog.get("Occurrences", {}).get(query_name)
            occurrence_rows[(log_name, query_name)] = (
                run_pattern_query(driver, database, query, log_name) if query else []
            )
    return aggregate_collaboration_patterns(objectives, occurrence_rows)


def _pearson_correlation(pairs: Sequence[Tuple[float, float]]) -> Optional[float]:
    if len(pairs) < 2:
        return None
    xs = [pair[0] for pair in pairs]
    ys = [pair[1] for pair in pairs]
    x_mean = mean(xs)
    y_mean = mean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in pairs)
    x_spread = sum((x - x_mean) ** 2 for x in xs)
    y_spread = sum((y - y_mean) ** 2 for y in ys)
    denominator = (x_spread * y_spread) ** 0.5
    return numerator / denominator if denominator else None


def compute_resource_collaboration_relationships(
    objective_rows: Sequence[Mapping[str, Any]],
    collaboration_per_objective: Mapping[str, Mapping[str, int]],
) -> Dict[str, Any]:
    """Build objective-level rows and descriptive resource-pattern correlations."""
    rows: List[Dict[str, Any]] = []
    for objective in objective_rows:
        key = str(objective.get("key") or "")
        counts = collaboration_per_objective.get(key, {})
        rows.append({
            **dict(objective),
            "handovers": int(counts.get("handover", 0)),
            "objective_switches": int(counts.get("objective_switch", 0)),
            "capability_driven_returns": int(counts.get("capability_driven_return", 0)),
            "parallel_collaborations": int(counts.get("parallel_collaboration", 0)),
        })

    relationship_specs = (
        ("robot_count", "handovers", "Robots involved vs handovers"),
        ("dominance", "handovers", "Dominance vs handovers"),
        ("dominance", "capability_driven_returns", "Dominance vs capability-driven returns"),
        ("robot_count", "parallel_collaborations", "Robots involved vs parallel work"),
        ("capability_count", "capability_driven_returns", "Capability diversity vs capability-driven returns"),
    )
    correlations: List[Dict[str, Any]] = []
    for x_key, y_key, label in relationship_specs:
        pairs = [
            (float(row[x_key]), float(row[y_key]))
            for row in rows
            if isinstance(row.get(x_key), (int, float))
            and isinstance(row.get(y_key), (int, float))
        ]
        correlations.append({
            "relationship": label,
            "x_key": x_key,
            "y_key": y_key,
            "correlation": _pearson_correlation(pairs),
            "objective_count": len(pairs),
        })
    return {"resource_collaboration_rows": rows, "resource_collaboration_correlations": correlations}


def fetch_objective_collaboration_counts(
    driver: Any,
    database: Optional[str],
    catalog: Mapping[str, Mapping[str, str]],
    log_name: str,
    objective_type: str,
    objective_id: str,
) -> Dict[str, int]:
    """Reuse formal pattern queries and count occurrences involving one objective."""
    suffix = objective_type.lower()
    query_names = (
        f"handover_{suffix}",
        f"objective_switch_{suffix}",
        f"capability_driven_return_{suffix}",
        f"parallel_collaboration_{suffix}",
    )
    counts: Dict[str, int] = {}
    occurrences = catalog.get("Occurrences", {})
    for query_name in query_names:
        query = occurrences.get(query_name)
        if not query:
            counts[query_name] = 0
            continue
        rows = run_pattern_query(driver, database, query, log_name)
        counts[query_name] = sum(
            1
            for row in rows
            if _row_involves_objective(query_name, row, objective_type, objective_id)
        )
    return counts


def build_resource_perspective(
    driver: Any,
    database: Optional[str],
    catalog: Mapping[str, Mapping[str, str]],
    log_name: str,
    objective_type: str,
    objective_id: str,
) -> Dict[str, Any]:
    """Build the complete resource payload for one selected objective instance."""
    task_rows = fetch_objective_task_rows(
        driver, database, log_name, objective_type, objective_id
    )
    provider_rows = fetch_robot_capability_rows(driver, database, log_name)
    payload = compute_resource_metrics(task_rows, provider_rows)
    payload["objective"] = {
        "type": objective_type,
        "id": str(objective_id),
        "log": log_name,
    }
    payload["collaboration_counts"] = fetch_objective_collaboration_counts(
        driver,
        database,
        catalog,
        log_name,
        objective_type,
        objective_id,
    )
    return payload


def build_aggregated_resource_perspective(
    driver: Any,
    database: Optional[str],
    catalog: Mapping[str, Mapping[str, str]],
    selected_objectives: Sequence[Mapping[str, str]],
) -> Dict[str, Any]:
    """Build resource, capability, collaboration, and grouped aggregate metrics."""
    objectives = _normalized_objectives(selected_objectives)
    task_rows = fetch_selected_objective_task_rows(driver, database, objectives)
    log_names = sorted({item["log_name"] for item in objectives})
    provider_rows = fetch_robot_capability_rows_for_logs(driver, database, log_names)
    payload = compute_aggregated_resource_metrics(task_rows, provider_rows, objectives)
    collaboration = fetch_aggregated_collaboration_patterns(
        driver, database, catalog, objectives
    )
    payload.update(collaboration)
    payload.update(compute_resource_collaboration_relationships(
        payload["objective_rows"],
        collaboration["collaboration_per_objective"],
    ))
    group_totals = collaboration["collaboration_group_totals"]
    collaboration_by_log = {
        (str(row["log_name"]), str(row["structure_key"])): row
        for row in collaboration["collaboration_by_log"]
    }
    for group in payload.get("group_comparison", []):
        log_name = str(group["group"])
        group["handovers"] = int(group_totals.get(f"{log_name}\x1fhandover", 0))
        group["objective_switches"] = int(
            group_totals.get(f"{log_name}\x1fobjective_switch", 0)
        )
        group["capability_driven_returns"] = int(
            group_totals.get(f"{log_name}\x1fcapability_driven_return", 0)
        )
        group["parallel_collaborations"] = int(
            group_totals.get(f"{log_name}\x1fparallel_collaboration", 0)
        )
        for structure in (
            "handover",
            "objective_switch",
            "capability_driven_return",
            "parallel_collaboration",
        ):
            row = collaboration_by_log.get((log_name, structure), {})
            group[f"{structure}_average_per_objective"] = float(
                row.get("average_per_objective", 0.0)
            )
            group[f"{structure}_objective_coverage"] = float(
                row.get("objective_coverage", 0.0)
            )
    payload["selected_objectives"] = objectives
    return payload
