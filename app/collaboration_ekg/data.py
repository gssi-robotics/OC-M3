from __future__ import annotations

from collections import OrderedDict
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from collaboration.collaboration_data import load_collaboration_patterns_module, run_query


PATTERN_COLORS = {
    "Robot handover": "#B22222",
    "Objective switch": "#E76F51",
    "Capability-driven return": "#264653",
    "Parallel collaboration": "#2A9D8F",
}
PATTERN_OPTIONS = list(PATTERN_COLORS)
OBJECTIVE_OPTIONS = ["Mission", "Segment"]
OBS_REL = "OBS"


def _matched_class_projection(source_var: str, target_var: str) -> str:
    return (
        f"{source_var}.Event_Id AS matched_source_class_id,\n"
        f"               {source_var}.activity AS matched_source_class_activity,\n"
        f"               properties({source_var}) AS matched_source_class_details,\n"
        f"               {source_var}.Count AS matched_source_class_count,\n"
        f"               {target_var}.Event_Id AS matched_target_class_id,\n"
        f"               {target_var}.activity AS matched_target_class_activity,\n"
        f"               properties({target_var}) AS matched_target_class_details,\n"
        f"               {target_var}.Count AS matched_target_class_count"
    )


@lru_cache(maxsize=1)
def _canonical_factory() -> Any:
    """Load the authoritative pattern-query factory used by every Inspector query."""
    module = load_collaboration_patterns_module()
    return module.CollaborationPatternCypher()


def _canonical_occurrence_query(pattern: str, objective_type: str) -> str:
    if objective_type not in OBJECTIVE_OPTIONS:
        raise ValueError(f"Unsupported objective type: {objective_type}")
    factory = _canonical_factory()
    if pattern == "Robot handover":
        return factory.robot_handover(objective_type)
    if pattern == "Objective switch":
        return factory.objective_switch(objective_type)
    if pattern == "Capability-driven return":
        return factory.capability_driven_return(objective_type)
    if pattern == "Parallel collaboration":
        method = (
            factory.parallel_collaboration_mission
            if objective_type == "Mission"
            else factory.parallel_collaboration_segment
        )
        return method()
    raise ValueError(f"Unsupported collaboration pattern: {pattern}")


def _canonical_subquery(pattern: str, objective_type: str) -> str:
    query = _canonical_occurrence_query(pattern, objective_type)
    return "CALL () {\n" + "\n".join(f"  {line}" for line in query.splitlines()) + "\n}"


def fetch_base_graph(driver: Any, database: Optional[str], min_frequency: int, limit: int) -> List[Dict[str, Any]]:
    query = """
    MATCH (c1:Class)-[r:DF_C]->(c2:Class)
    WHERE coalesce(r.edge_weight, 0) >= $min_frequency
    RETURN c1.Event_Id AS source_id,
           c1.activity AS source_activity,
           properties(c1) AS source_details,
           c2.Event_Id AS target_id,
           c2.activity AS target_activity,
           c2.Count AS target_count,
           properties(c2) AS target_details,
           c1.Count AS source_count,
           coalesce(r.Type, r.type, r.perspective_type, 'DF') AS perspective,
           coalesce(r.edge_weight, 0) AS frequency,
           r.avg_transition_seconds AS avg_seconds
    ORDER BY frequency DESC
    LIMIT $limit
    """
    return run_query(
        driver,
        database,
        query,
        {"min_frequency": min_frequency, "limit": limit},
    )


def pattern_occurrence_query(pattern: str, objective_type: str) -> str:
    canonical = _canonical_subquery(pattern, objective_type)
    if pattern == "Robot handover":
        return f"""
        {canonical}
        MATCH (e_i)-[:{OBS_REL}]->(c1:Class)
        MATCH (e_j)-[:{OBS_REL}]->(c2:Class)
        OPTIONAL MATCH (e_i)-[:CORR]->(s1:Entity {{type: 'Segment'}})
        OPTIONAL MATCH (e_j)-[:CORR]->(s2:Entity {{type: 'Segment'}})
        RETURN toString(objective.id) AS objective_id, toString(fromRobot.id) AS from_robot,
               {_matched_class_projection('c1', 'c2')},
               toString(toRobot.id) AS to_robot, toString(s1.id) AS from_segment,
               toString(s2.id) AS to_segment,
               coalesce(e_i.Event_Id,e_i.event_id,e_i.id,elementId(e_i)) AS from_event,
               coalesce(e_j.Event_Id,e_j.event_id,e_j.id,elementId(e_j)) AS to_event,
               e_i.activity AS from_activity, e_j.activity AS to_activity,
               e_i.start AS from_start, e_i.end AS from_end,
               e_j.start AS to_start, e_j.end AS to_end,
               [ce IN fromRobotControlEvents |
                 {{event_id: coalesce(ce.event_id, ce.id), activity: ce.activity,
                   start: ce.start, end: ce.end}}] AS from_robot_control_events,
               [ce IN toRobotControlEvents |
                 {{event_id: coalesce(ce.event_id, ce.id), activity: ce.activity,
                   start: ce.start, end: ce.end}}] AS to_robot_control_events,
               transitionTime AS duration_seconds
        ORDER BY from_start
        """
    if pattern == "Objective switch":
        return f"""
        {canonical}
        MATCH (e_i)-[:{OBS_REL}]->(c1:Class)
        MATCH (e_j)-[:{OBS_REL}]->(c2:Class)
        RETURN toString(robot.id) AS robot_id, toString(fromObjective.id) AS from_objective,
               {_matched_class_projection('c1', 'c2')},
               toString(toObjective.id) AS to_objective,
               coalesce(e_i.Event_Id,e_i.event_id,e_i.id,elementId(e_i)) AS from_event,
               coalesce(e_j.Event_Id,e_j.event_id,e_j.id,elementId(e_j)) AS to_event,
               e_i.activity AS from_activity, e_j.activity AS to_activity,
               e_i.start AS from_start, e_i.end AS from_end,
               e_j.start AS to_start, e_j.end AS to_end,
               [ce IN controlEvents |
                 {{event_id: coalesce(ce.event_id, ce.id), activity: ce.activity,
                   start: ce.start, end: ce.end}}] AS control_events,
               switchTime AS duration_seconds
        ORDER BY from_start
        """
    if pattern == "Capability-driven return":
        return f"""
        {canonical}
        MATCH (e_i)-[:{OBS_REL}]->(c1:Class)
        MATCH (e_k)-[:{OBS_REL}]->(c3:Class)
        RETURN toString(objective.id) AS objective_id,
               toString(returningRobot.id) AS returning_robot,
               {_matched_class_projection('c1', 'c3')},
               toString(intermediateRobot.id) AS intermediate_robot,
               [cap IN capabilities | coalesce(cap.id, cap.name, elementId(cap))] AS capabilities,
               coalesce(e_i.Event_Id,e_i.event_id,e_i.id,elementId(e_i)) AS from_event,
               coalesce(e_j.Event_Id,e_j.event_id,e_j.id,elementId(e_j)) AS intermediate_event,
               coalesce(e_k.Event_Id,e_k.event_id,e_k.id,elementId(e_k)) AS return_event,
               e_i.activity AS from_activity, e_j.activity AS intermediate_activity,
               e_k.activity AS return_activity,
               e_i.start AS from_start, e_j.start AS intermediate_start,
               e_k.start AS return_start, returnTime AS duration_seconds
        ORDER BY from_start
        """
    left_objective = "mission1" if objective_type == "Mission" else "segment1"
    right_objective = "mission2" if objective_type == "Mission" else "segment2"
    return f"""
    {canonical}
    CALL ({left_objective}, start1) {{
      MATCH ({left_objective})<-[:CORR]-(leftEvent:Event)-[:{OBS_REL}]->(leftClass:Class)
      WHERE leftEvent.Type = 'Task' AND leftEvent.start = start1
      RETURN head(collect(DISTINCT leftClass)) AS c1
    }}
    CALL ({right_objective}, start2) {{
      MATCH ({right_objective})<-[:CORR]-(rightEvent:Event)-[:{OBS_REL}]->(rightClass:Class)
      WHERE rightEvent.Type = 'Task' AND rightEvent.start = start2
      RETURN head(collect(DISTINCT rightClass)) AS c2
    }}
    RETURN toString({left_objective}.id) AS left_objective,
           toString({right_objective}.id) AS right_objective,
           {_matched_class_projection('c1', 'c2')},
           [r IN team1 | toString(r.id)] AS left_team,
           [r IN team2 | toString(r.id)] AS right_team,
           [r IN sharedRobots | toString(r.id)] AS shared_robots,
           [cap IN sharedRequiredCapabilities | coalesce(cap.id, cap.name, elementId(cap))]
             AS shared_required_capabilities,
           [item IN sharedCapabilityProviders |
             {{capability: coalesce(item.capability.id, item.capability.name,
                                    elementId(item.capability)),
               providers: [r IN item.providers | toString(r.id)]}}]
             AS shared_capability_providers,
           start1,end1,start2,end2,overlapStart AS overlap_start,
           overlapEnd AS overlap_end, overlapDuration AS duration_seconds
    ORDER BY overlap_start
    """


def fetch_pattern_occurrence_rows(
    driver: Any,
    database: Optional[str],
    pattern: str,
    objective_type: str,
) -> List[Dict[str, Any]]:
    rows = run_query(
        driver,
        database,
        pattern_occurrence_query(pattern, objective_type),
        {
            "objective_type": objective_type,
        },
    )
    if pattern != "Capability-driven return":
        return rows

    # A return is a three-event A-B-A structure. Never expose a partial row as
    # an occurrence even if malformed legacy data reaches this view.
    required_fields = (
        "from_event",
        "intermediate_event",
        "return_event",
        "returning_robot",
        "intermediate_robot",
    )
    return [
        row
        for row in rows
        if all(row.get(field) is not None and str(row.get(field)).strip() for field in required_fields)
        and str(row["returning_robot"]) != str(row["intermediate_robot"])
    ]


def aggregate_occurrence_rows(
    pattern: str,
    objective_type: str,
    rows: List[Dict[str, Any]],
    min_frequency: int,
) -> List[Dict[str, Any]]:
    grouped: "OrderedDict[Tuple[str, str], Dict[str, Any]]" = OrderedDict()
    for row in rows:
        source_class_id = str(row.get("matched_source_class_id") or "")
        target_class_id = str(row.get("matched_target_class_id") or "")
        if not source_class_id or not target_class_id:
            continue
        key = (source_class_id, target_class_id)
        item = grouped.setdefault(
            key,
            {
                "pattern": pattern,
                "objective_type": objective_type,
                "source_class_id": source_class_id,
                "source_activity": row.get("matched_source_class_activity"),
                "source_details": row.get("matched_source_class_details"),
                "source_count": row.get("matched_source_class_count"),
                "target_class_id": target_class_id,
                "target_activity": row.get("matched_target_class_activity"),
                "target_details": row.get("matched_target_class_details"),
                "target_count": row.get("matched_target_class_count"),
                "frequency": 0,
                "duration_values": [],
            },
        )
        item["frequency"] += 1
        duration_value = row.get("duration_seconds")
        if isinstance(duration_value, (int, float)):
            item["duration_values"].append(float(duration_value))

    aggregates: List[Dict[str, Any]] = []
    for item in grouped.values():
        if item["frequency"] < min_frequency:
            continue
        duration_values = item.pop("duration_values")
        item["avg_duration_seconds"] = (
            sum(duration_values) / len(duration_values) if duration_values else None
        )
        aggregates.append(item)
    return aggregates


def filter_occurrences_for_aggregate(
    rows: List[Dict[str, Any]],
    pattern: str,
    source_class_id: str,
    target_class_id: str,
    limit: int,
) -> List[Dict[str, Any]]:
    filtered = [
        row
        for row in rows
        if str(row.get("pattern") or "") == str(pattern)
        and str(row.get("matched_source_class_id") or "") == str(source_class_id)
        and str(row.get("matched_target_class_id") or "") == str(target_class_id)
    ]
    if limit > 0:
        return filtered[:limit]
    return filtered


def combine_pattern_occurrence_rows(
    rows_by_pattern: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    combined: List[Dict[str, Any]] = []
    for pattern, rows in rows_by_pattern.items():
        for row in rows:
            combined_row = dict(row)
            combined_row["pattern"] = pattern
            combined.append(combined_row)
    return combined


def aggregate_occurrence_rows_by_patterns(
    rows_by_pattern: Dict[str, List[Dict[str, Any]]],
    objective_type: str,
    min_frequency: int,
) -> List[Dict[str, Any]]:
    combined_summary: List[Dict[str, Any]] = []
    for pattern, rows in rows_by_pattern.items():
        combined_summary.extend(
            aggregate_occurrence_rows(pattern, objective_type, rows, min_frequency)
        )
    combined_summary.sort(
        key=lambda row: (
            -int(row.get("frequency") or 0),
            str(row.get("pattern") or ""),
            str(row.get("source_activity") or ""),
            str(row.get("target_activity") or ""),
        )
    )
    return combined_summary


def summary_rows_to_graph_rows(summary_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "source_id": row["source_class_id"],
            "source_activity": row["source_activity"],
            "source_details": row.get("source_details") or {},
            "source_count": row.get("source_count"),
            "target_id": row["target_class_id"],
            "target_activity": row["target_activity"],
            "target_details": row.get("target_details") or {},
            "target_count": row.get("target_count"),
            "perspective": f"{row.get('pattern')} [{row.get('objective_type')}]",
            "frequency": row["frequency"],
            "avg_transition_seconds": row.get("avg_duration_seconds"),
        }
        for row in summary_rows
    ]
