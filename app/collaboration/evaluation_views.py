from __future__ import annotations

import io
import json
import zipfile
from numbers import Number
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
import streamlit as st

from .collaboration_data import run_pattern_query, run_query
from .collaboration_utils import (
    event_id_from_mapping,
    filter_rows_by_log,
    format_seconds,
    humanize_name,
    node_id,
    normalize_value,
    table_safe_rows,
)


OCCURRENCE_LABELS = {
    "handover": "Robot handover",
    "objective_switch": "Objective switch",
    "capability_driven_return": "Capability-driven return",
    "parallel_collaboration": "Parallel collaboration",
}

CONTROL_CONTEXT_KEYS = (
    "fromTaskPreparationEvents",
    "toTaskPreparationEvents",
    "firstTaskPreparationEvents",
    "intermediateTaskPreparationEvents",
    "returnTaskPreparationEvents",
    "fromRobotControlEvents",
    "toRobotControlEvents",
    "controlEvents",
)

PREPARATION_CONTEXT_KEYS = (
    "fromTaskPreparationEvents",
    "toTaskPreparationEvents",
    "firstTaskPreparationEvents",
    "intermediateTaskPreparationEvents",
    "returnTaskPreparationEvents",
)

TIME_METRICS = {
    "transitionTime",
    "switchTime",
    "returnTime",
    "transitionToIntermediate",
    "transitionBack",
    "intermediateDuration",
    "overlapDuration",
    "avgTransitionTime",
    "avgSwitchTime",
    "avgReturnTime",
    "robotEffortSeconds",
    "totalRobotEffortSeconds",
    "throughputSeconds",
    "taskEffortSeconds",
    "syncDelay",
    "branchWait",
    "branchWait1",
    "branchWait2",
    "totalBranchWait",
}

EVALUATION_DATASET_VERSION = 3

EVALUATION_TABLE_SPECS = (
    ("strategy_summary.csv", "Strategy summary", "summary", "Paper-facing comparison by strategy and objective perspective."),
    ("occurrence_counts.csv", "Occurrence counts", "counts", "Counts by strategy, collaboration structure, and objective perspective."),
    ("occurrences.csv", "Concrete occurrences", "occurrences", "Event-level collaboration occurrences with temporal and Control-event context."),
    ("indicators_long.csv", "Indicators (long format)", "indicators", "One analytical metric per strategy and entity-level observation."),
    ("collaboration_variants.csv", "Collaboration variants", "collaboration_variants", "Mission-level collaboration signatures with continuity and performance measures."),
    ("robot_handover_network.csv", "Robot handover network", "robot_network", "Directed robot-to-robot handover edges with frequency and temporal context."),
    ("entity_duration_summary.csv", "Entity durations", "entity_durations", "Elapsed Mission, Robot, and Segment duration statistics by strategy."),
    ("activity_duration_summary.csv", "Activity durations", "activity_durations", "Task and Control activity execution-duration statistics by strategy."),
    ("activity_transition_summary.csv", "Activity transitions", "activity_transitions", "Activity-to-activity transition-time statistics by strategy and process perspective."),
)


def _structure_name(query_name: str) -> str:
    for prefix, label in OCCURRENCE_LABELS.items():
        if query_name.startswith(prefix) or prefix in query_name:
            return label
    return humanize_name(query_name)


def _perspective(query_name: str) -> str:
    if query_name.endswith("_mission") or "_mission_" in query_name:
        return "Mission"
    if query_name.endswith("_segment") or "_segment_" in query_name or "segments" in query_name:
        return "Segment"
    if "by_robot" in query_name:
        return "Robot"
    if "capability" in query_name:
        return "Capability"
    return "Cross-perspective"


def _json(value: Any) -> str:
    return json.dumps(normalize_value(value), ensure_ascii=True, default=str, sort_keys=True)


def _id(value: Any) -> str:
    return node_id(value) or ""


def _event_id(value: Any) -> str:
    return event_id_from_mapping(value) or ""


def _list_ids(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [item for item in (_id(entry) for entry in value) if item]


def _events_from_context_keys(
    row: Dict[str, Any],
    context_keys: Iterable[str],
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for key in context_keys:
        value = row.get(key)
        if not isinstance(value, list):
            continue
        for event in value:
            if not isinstance(event, dict):
                continue
            event_key = _event_id(event) or _json(event)
            if event_key in seen:
                continue
            seen.add(event_key)
            events.append(event)
    return events


def _control_events(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    return _events_from_context_keys(row, CONTROL_CONTEXT_KEYS)


def _preparation_events(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    return _events_from_context_keys(row, PREPARATION_CONTEXT_KEYS)


def _event_duration_seconds(event: Dict[str, Any]) -> float:
    start = pd.to_datetime(event.get("start"), errors="coerce", utc=True)
    end = pd.to_datetime(event.get("end"), errors="coerce", utc=True)
    if pd.isna(start) or pd.isna(end) or end < start:
        return 0.0
    return float((end - start).total_seconds())


def _occurrence_record(
    strategy: str,
    query_name: str,
    index: int,
    row: Dict[str, Any],
) -> Dict[str, Any]:
    controls = _control_events(row)
    preparation_events = _preparation_events(row)
    capabilities = _list_ids(row.get("capabilities")) or _list_ids(row.get("sharedRequiredCapabilities"))
    target_required_capabilities = _list_ids(row.get("targetRequiredCapabilities"))
    source_missing_capabilities = _list_ids(row.get("sourceMissingTargetCapabilities"))
    is_handover = _structure_name(query_name) == "Robot handover"
    return {
        "strategy": strategy,
        "structure": _structure_name(query_name),
        "perspective": _perspective(query_name),
        "occurrence_number": index,
        "objective_id": _id(row.get("objective")),
        "from_objective_id": _id(row.get("fromObjective")),
        "to_objective_id": _id(row.get("toObjective")),
        "mission_id": _id(row.get("mission")) or _id(row.get("mission1")),
        "second_mission_id": _id(row.get("mission2")),
        "segment_1_id": _id(row.get("segment1")),
        "segment_2_id": _id(row.get("segment2")),
        "robot_id": _id(row.get("robot")),
        "from_robot_id": _id(row.get("fromRobot")),
        "to_robot_id": _id(row.get("toRobot")),
        "returning_robot_id": _id(row.get("returningRobot")),
        "intermediate_robot_id": _id(row.get("intermediateRobot")),
        "event_i": _event_id(row.get("e_i")),
        "event_j": _event_id(row.get("e_j")),
        "event_k": _event_id(row.get("e_k")),
        "transition_seconds": row.get("transitionTime", row.get("switchTime")),
        "return_seconds": row.get("returnTime"),
        "overlap_seconds": row.get("overlapDuration"),
        "control_event_count": len(controls),
        "control_activities": " | ".join(str(event.get("activity") or "") for event in controls),
        "preparation_event_count": len(preparation_events),
        "preparation_effort_seconds": sum(_event_duration_seconds(event) for event in preparation_events),
        "preparation_activities": " | ".join(
            str(event.get("activity") or "") for event in preparation_events
        ),
        "capabilities": " | ".join(capabilities),
        "target_required_capabilities": " | ".join(target_required_capabilities),
        "source_missing_target_capabilities": " | ".join(source_missing_capabilities),
        "capability_driven_handover": bool(
            is_handover
            and target_required_capabilities
            and source_missing_capabilities
        ),
        "details_json": _json(row),
    }


def _row_dimensions(row: Dict[str, Any]) -> Dict[str, str]:
    return {
        "objective_id": _id(row.get("objective")),
        "robot_id": _id(row.get("robot")),
        "capability_id": _id(row.get("capability")),
        "mission_id": _id(row.get("mission")),
        "segment_1_id": _id(row.get("segment1")),
        "segment_2_id": _id(row.get("segment2")),
        "secondary_entity_id": _id(row.get("provider")),
        "from_robot_id": _id(row.get("fromRobot")),
        "to_robot_id": _id(row.get("toRobot")),
    }


def _metric_unit(metric: str) -> str:
    if metric in TIME_METRICS or metric.lower().endswith(("time", "duration", "seconds", "wait")):
        return "seconds"
    if metric.lower().endswith(("ratio", "share", "intensity")):
        return "ratio"
    return "count" if "count" in metric.lower() or metric in {"teamSize", "availability", "providerCount"} else "value"


def _indicator_records(
    strategy: str,
    query_name: str,
    rows: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for observation, row in enumerate(rows, start=1):
        dimensions = _row_dimensions(row)
        for metric, value in row.items():
            if isinstance(value, bool) or not isinstance(value, Number):
                continue
            records.append(
                {
                    "strategy": strategy,
                    "indicator": humanize_name(query_name),
                    "perspective": _perspective(query_name),
                    "observation": observation,
                    **dimensions,
                    "metric": metric,
                    "value": float(value),
                    "unit": _metric_unit(metric),
                    "details_json": _json(row),
                }
            )
    return records


def _entity_duration_records(
    driver: Any,
    database: Optional[str],
    strategies: List[str],
) -> List[Dict[str, Any]]:
    query = """
    MATCH (entity:Entity {type: $entity_type})<-[:CORR]-(event:Event)
    WHERE toString(event.Log) IN $strategies
      AND event.start IS NOT NULL AND event.end IS NOT NULL
    WITH toString(event.Log) AS strategy, entity,
         min(event.start) AS entity_start,
         max(event.end) AS entity_end
    WITH strategy,
         toFloat(duration.inSeconds(entity_start, entity_end).seconds) AS duration_seconds
    RETURN strategy,
           $entity_type AS entity_type,
           count(*) AS entity_count,
           avg(duration_seconds) AS avg_duration_seconds,
           percentileCont(duration_seconds, 0.5) AS median_duration_seconds,
           min(duration_seconds) AS min_duration_seconds,
           max(duration_seconds) AS max_duration_seconds
    ORDER BY strategy
    """
    records: List[Dict[str, Any]] = []
    for entity_type in ("Mission", "Robot", "Segment"):
        records.extend(
            run_query(
                driver,
                database,
                query,
                {"entity_type": entity_type, "strategies": strategies},
            )
        )
    return records


def _activity_duration_records(
    driver: Any,
    database: Optional[str],
    strategies: List[str],
) -> List[Dict[str, Any]]:
    query = """
    MATCH (event:Event)
    WHERE toString(event.Log) IN $strategies
      AND event.activity IS NOT NULL
      AND event.start IS NOT NULL AND event.end IS NOT NULL
    WITH toString(event.Log) AS strategy,
         coalesce(event.Type, 'Unknown') AS event_type,
         toString(event.activity) AS activity,
         toFloat(duration.inSeconds(event.start, event.end).seconds) AS duration_seconds
    RETURN strategy, event_type, activity,
           count(*) AS event_count,
           avg(duration_seconds) AS avg_duration_seconds,
           percentileCont(duration_seconds, 0.5) AS median_duration_seconds,
           min(duration_seconds) AS min_duration_seconds,
           max(duration_seconds) AS max_duration_seconds
    ORDER BY strategy, event_type, activity
    """
    return run_query(driver, database, query, {"strategies": strategies})


def _activity_transition_records(
    driver: Any,
    database: Optional[str],
    strategies: List[str],
) -> List[Dict[str, Any]]:
    query = """
    MATCH (source:Event)-[transition:DF]->(target:Event)
    WHERE toString(source.Log) IN $strategies
      AND source.Log = target.Log
      AND source.activity IS NOT NULL AND target.activity IS NOT NULL
    WITH toString(source.Log) AS strategy,
         'Task DF' AS sequence,
         coalesce(transition.type, transition.perspective_type, transition.Type, 'DF') AS perspective,
         toString(source.activity) AS source_activity,
         toString(target.activity) AS target_activity,
         coalesce(
           toFloat(transition.transitionTimeSeconds),
           CASE WHEN source.end IS NOT NULL AND target.start IS NOT NULL
                THEN toFloat(duration.inSeconds(source.end, target.start).seconds)
                ELSE null END
         ) AS transition_seconds
    WHERE transition_seconds IS NOT NULL
    RETURN strategy, sequence, perspective, source_activity, target_activity,
           count(*) AS transition_count,
           avg(transition_seconds) AS avg_transition_seconds,
           percentileCont(transition_seconds, 0.5) AS median_transition_seconds,
           min(transition_seconds) AS min_transition_seconds,
           max(transition_seconds) AS max_transition_seconds

    UNION ALL

    MATCH (source:Event)-[transition:DF_Control]->(target:Event)
    WHERE toString(source.Log) IN $strategies
      AND source.Log = target.Log
      AND source.activity IS NOT NULL AND target.activity IS NOT NULL
    WITH toString(source.Log) AS strategy,
         'Robot all-events DF_Control' AS sequence,
         'Robot' AS perspective,
         toString(source.activity) AS source_activity,
         toString(target.activity) AS target_activity,
         coalesce(
           toFloat(transition.transitionTimeSeconds),
           CASE WHEN source.end IS NOT NULL AND target.start IS NOT NULL
                THEN toFloat(duration.inSeconds(source.end, target.start).seconds)
                ELSE null END
         ) AS transition_seconds
    WHERE transition_seconds IS NOT NULL
    RETURN strategy, sequence, perspective, source_activity, target_activity,
           count(*) AS transition_count,
           avg(transition_seconds) AS avg_transition_seconds,
           percentileCont(transition_seconds, 0.5) AS median_transition_seconds,
           min(transition_seconds) AS min_transition_seconds,
           max(transition_seconds) AS max_transition_seconds
    ORDER BY strategy, sequence, perspective, source_activity, target_activity
    """
    return run_query(driver, database, query, {"strategies": strategies})


def build_evaluation_dataset(
    driver: Any,
    database: Optional[str],
    catalog: Dict[str, Dict[str, str]],
    strategies: List[str],
) -> Dict[str, List[Dict[str, Any]]]:
    counts: List[Dict[str, Any]] = []
    occurrences: List[Dict[str, Any]] = []
    indicators: List[Dict[str, Any]] = []

    for query_name, query in catalog.get("Occurrences", {}).items():
        all_rows = run_pattern_query(driver, database, query, None)
        for strategy in strategies:
            rows = filter_rows_by_log(all_rows, strategy)
            counts.append(
                {
                    "strategy": strategy,
                    "structure": _structure_name(query_name),
                    "perspective": _perspective(query_name),
                    "occurrence_count": len(rows),
                }
            )
            occurrences.extend(
                _occurrence_record(strategy, query_name, index, row)
                for index, row in enumerate(rows, start=1)
            )

    for query_name, query in catalog.get("Diagnostics", {}).items():
        all_rows = run_pattern_query(driver, database, query, None)
        for strategy in strategies:
            indicators.extend(
                _indicator_records(
                    strategy,
                    query_name,
                    filter_rows_by_log(all_rows, strategy),
                )
            )

    payload = {"counts": counts, "occurrences": occurrences, "indicators": indicators}
    payload["entity_durations"] = _entity_duration_records(driver, database, strategies)
    payload["activity_durations"] = _activity_duration_records(driver, database, strategies)
    payload["activity_transitions"] = _activity_transition_records(driver, database, strategies)
    payload["robot_network"] = _robot_network_records(occurrences)
    payload["collaboration_variants"] = _collaboration_variant_records(payload)
    payload["summary"] = _strategy_summary(payload)
    return payload



def _robot_network_records(occurrences: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Aggregate formal handover occurrences into a directed organizational network."""
    grouped: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for row in occurrences:
        if row.get("structure") != "Robot handover":
            continue
        source = str(row.get("from_robot_id") or "")
        target = str(row.get("to_robot_id") or "")
        if not source or not target:
            continue
        key = (str(row.get("strategy") or ""), source, target)
        bucket = grouped.setdefault(
            key,
            {
                "strategy": key[0],
                "from_robot_id": source,
                "to_robot_id": target,
                "handover_count": 0,
                "transition_seconds": [],
                "preparation_effort_seconds": [],
            },
        )
        bucket["handover_count"] += 1
        if isinstance(row.get("transition_seconds"), Number):
            bucket["transition_seconds"].append(float(row["transition_seconds"]))
        if isinstance(row.get("preparation_effort_seconds"), Number):
            bucket["preparation_effort_seconds"].append(float(row["preparation_effort_seconds"]))

    records: List[Dict[str, Any]] = []
    for bucket in grouped.values():
        records.append(
            {
                "strategy": bucket["strategy"],
                "from_robot_id": bucket["from_robot_id"],
                "to_robot_id": bucket["to_robot_id"],
                "handover_count": bucket["handover_count"],
                "avg_transition_seconds": _mean(bucket["transition_seconds"]),
                "avg_preparation_effort_seconds": _mean(bucket["preparation_effort_seconds"]),
            }
        )
    return sorted(records, key=lambda row: (row["strategy"], -row["handover_count"], row["from_robot_id"], row["to_robot_id"]))


def _indicator_lookup(
    indicators: List[Dict[str, Any]],
    *,
    strategy: str,
    perspective: str,
    objective_id: str,
    metric: str,
    indicator_prefix: Optional[str] = None,
) -> Optional[float]:
    values = [
        float(row["value"])
        for row in indicators
        if row.get("strategy") == strategy
        and row.get("perspective") == perspective
        and row.get("objective_id") == objective_id
        and row.get("metric") == metric
        and (indicator_prefix is None or str(row.get("indicator", "")).startswith(indicator_prefix))
        and isinstance(row.get("value"), Number)
    ]
    return values[0] if values else None


def _collaboration_variant_records(payload: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Build mission-level collaboration signatures for PM-style variant analysis.

    A switch or mission-level parallel relation is counted for every mission it
    involves. The signature is descriptive, not a causal or normative label.
    """
    indicators = payload["indicators"]
    occurrences = payload["occurrences"]
    mission_ids: set[Tuple[str, str]] = set()

    for row in indicators:
        if row.get("perspective") == "Mission" and row.get("objective_id"):
            mission_ids.add((str(row["strategy"]), str(row["objective_id"])))
    for row in occurrences:
        strategy = str(row.get("strategy") or "")
        for field in ("objective_id", "mission_id", "second_mission_id", "from_objective_id", "to_objective_id"):
            value = str(row.get(field) or "")
            if value:
                mission_ids.add((strategy, value))

    records: List[Dict[str, Any]] = []
    for strategy, mission_id in sorted(mission_ids):
        relevant = [row for row in occurrences if row.get("strategy") == strategy]
        handovers = sum(
            1 for row in relevant
            if row.get("structure") == "Robot handover"
            and row.get("perspective") == "Mission"
            and row.get("objective_id") == mission_id
        )
        returns = sum(
            1 for row in relevant
            if row.get("structure") == "Capability-driven return"
            and row.get("perspective") == "Mission"
            and row.get("objective_id") == mission_id
        )
        switches = sum(
            1 for row in relevant
            if row.get("structure") == "Objective switch"
            and row.get("perspective") == "Mission"
            and mission_id in {str(row.get("from_objective_id") or ""), str(row.get("to_objective_id") or "")}
        )
        parallel = sum(
            1 for row in relevant
            if row.get("structure") == "Parallel collaboration"
            and row.get("perspective") == "Mission"
            and mission_id in {str(row.get("mission_id") or ""), str(row.get("second_mission_id") or "")}
        )
        active = [name for name, count in (("HO", handovers), ("SW", switches), ("CR", returns), ("PC", parallel)) if count > 0]
        records.append(
            {
                "strategy": strategy,
                "mission_id": mission_id,
                "collaboration_signature": f"HO{handovers}|SW{switches}|CR{returns}|PC{parallel}",
                "collaboration_variant": "+".join(active) if active else "Stable/no detected structure",
                "handover_count": handovers,
                "switch_involvement_count": switches,
                "capability_return_count": returns,
                "parallel_involvement_count": parallel,
                "handover_intensity": _indicator_lookup(
                    indicators, strategy=strategy, perspective="Mission", objective_id=mission_id,
                    metric="handoverIntensity", indicator_prefix="Handover Diagnostics"
                ),
                "allocation_continuity": _indicator_lookup(
                    indicators, strategy=strategy, perspective="Mission", objective_id=mission_id,
                    metric="retentionRatio", indicator_prefix="Allocation Continuity"
                ),
                "throughput_seconds": _indicator_lookup(
                    indicators, strategy=strategy, perspective="Mission", objective_id=mission_id,
                    metric="throughputSeconds", indicator_prefix="Objective Performance"
                ),
                "task_effort_seconds": _indicator_lookup(
                    indicators, strategy=strategy, perspective="Mission", objective_id=mission_id,
                    metric="taskEffortSeconds", indicator_prefix="Objective Performance"
                ),
                "team_size": _indicator_lookup(
                    indicators, strategy=strategy, perspective="Mission", objective_id=mission_id,
                    metric="teamSize", indicator_prefix="Objective Performance"
                ),
            }
        )
    return records


def _mean(values: Iterable[Any]) -> Optional[float]:
    numeric = [float(value) for value in values if isinstance(value, Number) and not isinstance(value, bool)]
    return sum(numeric) / len(numeric) if numeric else None


def _strategy_summary(payload: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    strategies = sorted({str(row["strategy"]) for row in payload["counts"]})
    summary: List[Dict[str, Any]] = []
    for strategy in strategies:
        for perspective in ("Mission", "Segment"):
            counts = {
                str(row["structure"]): int(row["occurrence_count"])
                for row in payload["counts"]
                if row["strategy"] == strategy and row["perspective"] == perspective
            }
            occurrences = [
                row for row in payload["occurrences"]
                if row["strategy"] == strategy and row["perspective"] == perspective
            ]
            indicators = [
                row for row in payload["indicators"]
                if row["strategy"] == strategy and row["perspective"] == perspective
            ]
            transition_count = sum(
                row["value"] for row in indicators
                if row["metric"] == "transitionCount" and str(row["indicator"]).startswith("Allocation Continuity")
            )
            retained_count = sum(
                row["value"] for row in indicators
                if row["metric"] == "retainedTransitionCount" and str(row["indicator"]).startswith("Allocation Continuity")
            )
            handover_opportunities = sum(
                row["value"] for row in indicators
                if row["metric"] == "transitionOpportunities" and str(row["indicator"]).startswith("Handover Diagnostics")
            )
            handover_count_diag = sum(
                row["value"] for row in indicators
                if row["metric"] == "handoverCount" and str(row["indicator"]).startswith("Handover Diagnostics")
            )
            performance = [
                row for row in indicators if str(row["indicator"]).startswith("Objective Performance")
            ]
            sequential = [row for row in occurrences if row["structure"] != "Parallel collaboration"]
            handovers = [row for row in occurrences if row["structure"] == "Robot handover"]
            capability_driven_handovers = [
                row for row in handovers if row.get("capability_driven_handover") is True
            ]
            switches = [row for row in occurrences if row["structure"] == "Objective switch"]
            returns = [row for row in occurrences if row["structure"] == "Capability-driven return"]
            summary.append(
                {
                    "strategy": strategy,
                    "objective_perspective": perspective,
                    "handovers": counts.get("Robot handover", 0),
                    "capability_driven_handovers": len(capability_driven_handovers),
                    "capability_driven_handover_share": (
                        len(capability_driven_handovers) / len(handovers) if handovers else None
                    ),
                    "objective_switches": counts.get("Objective switch", 0),
                    "capability_driven_returns": counts.get("Capability-driven return", 0),
                    "parallel_collaborations": counts.get("Parallel collaboration", 0),
                    "handover_intensity": (
                        handover_count_diag / handover_opportunities if handover_opportunities else None
                    ),
                    "allocation_continuity": retained_count / transition_count if transition_count else None,
                    "avg_handover_transition_seconds": _mean(row.get("transition_seconds") for row in handovers),
                    "avg_switch_transition_seconds": _mean(row.get("transition_seconds") for row in switches),
                    "avg_capability_return_seconds": _mean(row.get("return_seconds") for row in returns),
                    "avg_objective_throughput_seconds": _mean(
                        row["value"] for row in performance if row["metric"] == "throughputSeconds"
                    ),
                    "avg_team_size": _mean(
                        row["value"] for row in performance if row["metric"] == "teamSize"
                    ),
                    "preparation_event_count": sum(int(row.get("preparation_event_count") or 0) for row in sequential),
                    "preparation_effort_seconds": sum(float(row.get("preparation_effort_seconds") or 0.0) for row in sequential),
                    "avg_preparation_effort_per_occurrence_seconds": _mean(
                        row.get("preparation_effort_seconds") for row in sequential
                    ),
                }
            )
    return summary

def _evaluation_zip(payload: Dict[str, List[Dict[str, Any]]]) -> bytes:
    readme = """OC-M3 collaboration evaluation export

occurrence_counts.csv
  One row per strategy, collaboration structure, and objective perspective.

occurrences.csv
  One row per concrete EKG occurrence, including event/entity identifiers,
  temporal values, capability evidence, and Control-event context.

indicators_long.csv
  Tidy long-format analytical indicators. Each row is one metric value for
  one strategy and entity-level observation, ready for statistical analysis.

strategy_summary.csv
  Paper-facing comparison table by strategy and objective perspective. Structural
  intensities use behavioral opportunities as denominators; temporal values are
  kept separate as performance/context measures.

collaboration_variants.csv
  Mission-level collaboration signatures (handover, switch involvement, capability
  return, parallel involvement) joined with continuity and performance measures.
  This supports PM-style variant and performance comparison.

robot_handover_network.csv
  Directed robot-to-robot organizational network induced only by formal handover
  occurrences, with edge frequency and temporal/control context.

entity_duration_summary.csv
  Mission, Robot, and Segment elapsed-duration distributions by strategy.

activity_duration_summary.csv
  Task and Control activity execution-duration distributions by strategy.

activity_transition_summary.csv
  Activity-to-activity transition distributions for Task DF and robot DF_Control.

The strategy column corresponds to the EKG Log property selected in the UI.
Time values are expressed in seconds.
"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.txt", readme)
        for file_name, _, payload_key, _ in EVALUATION_TABLE_SPECS:
            archive.writestr(
                file_name,
                pd.DataFrame(payload.get(payload_key, [])).to_csv(index=False),
            )
    return buffer.getvalue()


def _render_evaluation_tables(payload: Dict[str, List[Dict[str, Any]]]) -> None:
    st.markdown("#### Evaluation tables")
    st.caption(
        f"These are the same {len(EVALUATION_TABLE_SPECS)} CSV tables included in the evaluation ZIP."
    )

    selected_label = st.selectbox(
        "Table to inspect",
        [label for _, label, _, _ in EVALUATION_TABLE_SPECS],
        key="evaluation_table_to_inspect",
    )
    file_name, _, payload_key, description = next(
        spec for spec in EVALUATION_TABLE_SPECS if spec[1] == selected_label
    )
    dataframe = pd.DataFrame(payload.get(payload_key, []))

    st.caption(
        f"`{file_name}` | {len(dataframe):,} rows x {len(dataframe.columns):,} columns. "
        f"{description}"
    )
    if dataframe.empty:
        st.info("This table has no rows for the current strategy selection.")
    else:
        st.dataframe(dataframe, width="stretch", height=520, hide_index=True)


def _weighted_summary_average(
    rows: Iterable[Dict[str, Any]],
    value_key: str,
    weight_key: str,
) -> Tuple[Optional[float], int]:
    weighted_total = 0.0
    total_weight = 0
    for row in rows:
        value = row.get(value_key)
        weight = row.get(weight_key)
        if not isinstance(value, Number) or not isinstance(weight, Number):
            continue
        numeric_weight = int(weight)
        if numeric_weight <= 0:
            continue
        weighted_total += float(value) * numeric_weight
        total_weight += numeric_weight
    return (
        weighted_total / total_weight if total_weight else None,
        total_weight,
    )


def _render_performance_context(payload: Dict[str, List[Dict[str, Any]]]) -> None:
    entity_rows = payload.get("entity_durations", [])
    activity_rows = payload.get("activity_durations", [])
    transition_rows = payload.get("activity_transitions", [])

    st.markdown("### Execution Performance Context")
    st.caption(
        "Descriptive execution context for interpreting collaboration differences. "
        "These values characterize the executions; they do not score the allocation algorithm."
    )

    entity_columns = st.columns(3)
    for column, entity_type in zip(entity_columns, ("Mission", "Robot", "Segment")):
        value, count = _weighted_summary_average(
            (row for row in entity_rows if row.get("entity_type") == entity_type),
            "avg_duration_seconds",
            "entity_count",
        )
        with column:
            st.metric(f"Avg {entity_type} duration", format_seconds(value) or "n/a")
            st.caption(f"Across {count:,} {entity_type.lower()} instances")

    event_columns = st.columns(3)
    for column, event_type in zip(event_columns[:2], ("Task", "Control")):
        value, count = _weighted_summary_average(
            (row for row in activity_rows if row.get("event_type") == event_type),
            "avg_duration_seconds",
            "event_count",
        )
        with column:
            st.metric(f"Avg {event_type} activity", format_seconds(value) or "n/a")
            st.caption(f"Across {count:,} {event_type.lower()} events")

    transition_value, transition_count = _weighted_summary_average(
        (
            row for row in transition_rows
            if row.get("sequence") == "Robot all-events DF_Control"
        ),
        "avg_transition_seconds",
        "transition_count",
    )
    with event_columns[2]:
        st.metric("Avg Robot DF_Control transition", format_seconds(transition_value) or "n/a")
        st.caption(f"Across {transition_count:,} DF_Control transitions")

    df_columns = st.columns(3)
    for column, perspective in zip(df_columns, ("Mission", "Segment", "Robot")):
        value, count = _weighted_summary_average(
            (
                row for row in transition_rows
                if row.get("sequence") == "Task DF"
                and row.get("perspective") == perspective
            ),
            "avg_transition_seconds",
            "transition_count",
        )
        with column:
            st.metric(
                f"Avg {perspective} DF transition",
                format_seconds(value) or "n/a",
            )
            st.caption(f"Across {count:,} Task DF transitions")

    entity_tab, activity_tab, transition_tab = st.tabs(
        ["Entity durations", "Activity durations", "Activity transitions"]
    )
    with entity_tab:
        st.dataframe(pd.DataFrame(entity_rows), width="stretch", hide_index=True)
    with activity_tab:
        st.dataframe(pd.DataFrame(activity_rows), width="stretch", hide_index=True)
    with transition_tab:
        st.caption(
            "Task DF is reported by process perspective; DF_Control reports the complete Task/Control robot sequence."
        )
        st.dataframe(pd.DataFrame(transition_rows), width="stretch", hide_index=True)


def render_evaluation_workspace(
    driver: Any,
    database: Optional[str],
    catalog: Dict[str, Dict[str, str]],
    logs: List[str],
) -> None:
    st.subheader("1. Evaluation dataset")
    st.caption(
        "Compare task-allocation strategies through collaboration behavior. "
        "Each selected EKG Log is treated as one strategy or experimental condition."
    )
    strategies = st.multiselect(
        "Strategies / logs",
        logs,
        default=logs,
        key="evaluation_logs",
    )
    if not strategies:
        st.info("Select at least one strategy/log.")
        return

    signature = (EVALUATION_DATASET_VERSION, tuple(strategies))
    if st.button("Generate evaluation dataset", type="primary", key="generate_evaluation_dataset"):
        with st.spinner("Computing collaboration occurrences and indicators..."):
            st.session_state["evaluation_payload"] = build_evaluation_dataset(
                driver, database, catalog, strategies
            )
            st.session_state["evaluation_signature"] = signature

    if st.session_state.get("evaluation_signature") != signature:
        st.info("Generate the dataset for the current strategy selection.")
        return

    payload = st.session_state.get("evaluation_payload")
    if not payload:
        return

    _render_performance_context(payload)
    _render_evaluation_tables(payload)

    st.download_button(
        "Download evaluation package (.zip)",
        data=_evaluation_zip(payload),
        file_name="oc_m3_collaboration_evaluation.zip",
        mime="application/zip",
        key="download_evaluation_package",
    )
    st.caption(
        f"Export contains {len(payload['occurrences']):,} occurrences and "
        f"{len(payload['indicators']):,} tidy indicator values."
    )


def _preparation_contexts(query_name: str, row: Dict[str, Any]) -> List[Tuple[str, str, Any, Any]]:
    if query_name.startswith("handover"):
        return [
            ("source task", "fromTaskPreparationEvents", row.get("e_i"), row.get("fromRobot")),
            ("target task", "toTaskPreparationEvents", row.get("e_j"), row.get("toRobot")),
        ]
    if query_name.startswith("objective_switch"):
        return [
            ("task before switch", "fromTaskPreparationEvents", row.get("e_i"), row.get("robot")),
            ("task after switch", "toTaskPreparationEvents", row.get("e_j"), row.get("robot")),
        ]
    if query_name.startswith("capability_driven_return"):
        return [
            ("first task", "firstTaskPreparationEvents", row.get("e_i"), row.get("returningRobot")),
            ("intermediate task", "intermediateTaskPreparationEvents", row.get("e_j"), row.get("intermediateRobot")),
            ("return task", "returnTaskPreparationEvents", row.get("e_k"), row.get("returningRobot")),
        ]
    return []


def _explanation_rows(query_name: str, row: Dict[str, Any]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen: set[Tuple[str, str]] = set()
    for task_role, context_key, task, robot in _preparation_contexts(query_name, row):
        robot_id = _id(robot)
        controls = row.get(context_key) if isinstance(row.get(context_key), list) else []
        for control in controls:
            if not isinstance(control, dict):
                continue
            control_id = _event_id(control) or _json(control)
            dedupe_key = (task_role, control_id)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            result.append(
                {
                    "context": f"prepares {task_role}",
                    "event_type": "Control",
                    "robot": robot_id,
                    "event_id": _event_id(control),
                    "activity": control.get("activity"),
                    "start": control.get("start"),
                    "end": control.get("end"),
                }
            )
        if isinstance(task, dict):
            result.append(
                {
                    "context": task_role,
                    "event_type": "Task",
                    "robot": robot_id,
                    "event_id": _event_id(task),
                    "activity": task.get("activity"),
                    "start": task.get("start"),
                    "end": task.get("end"),
                }
            )
    result.sort(key=lambda item: (str(item.get("start") or ""), item["event_type"] == "Task"))
    for index, item in enumerate(result, start=1):
        item["sequence"] = index
    return result


def _sequence_graph(rows: List[Dict[str, Any]]) -> str:
    lines = ["digraph explanation {", "rankdir=LR;", 'graph [bgcolor="white", pad="0.2"];']
    lines.append('node [shape=box, style="rounded,filled", fontname="Helvetica"];')
    for index, row in enumerate(rows):
        color = "#DCEAF7" if row["event_type"] == "Control" else "#FAD7C8"
        label = f"{row['event_type']}\\n{row.get('activity') or '?'}\\n{row.get('robot') or '?'}"
        lines.append(f"n{index} [label={json.dumps(label)}, fillcolor=\"{color}\"];")
        if index:
            lines.append(f"n{index - 1} -> n{index};")
    lines.append("}")
    return "\n".join(lines)
