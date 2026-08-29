from __future__ import annotations

from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from collaboration.collaboration_data import run_query


PATTERN_COLORS = {
    "Robot handover": "#B22222",
    "Objective switch": "#E76F51",
    "Capability-driven return": "#264653",
    "Parallel collaboration": "#2A9D8F",
}
PATTERN_OPTIONS = list(PATTERN_COLORS)
OBJECTIVE_OPTIONS = ["Mission", "Segment"]
OBS_REL = "OBS"
CORR_REL = "CORR"
DF_REL = "DF"


def _df_type_expr(df_var: str) -> str:
    return f"coalesce({df_var}.type, {df_var}.perspective_type, {df_var}.Type)"


def _transition_expr(df_var: str, left_event: str, right_event: str) -> str:
    return (
        f"coalesce({df_var}.transitionTimeSeconds, "
        f"CASE WHEN {left_event}.end IS NOT NULL AND {right_event}.start IS NOT NULL "
        f"THEN duration.inSeconds({left_event}.end, {right_event}.start).seconds END)"
    )


def _class_projection(prefix: str, class_var: str) -> str:
    return (
        f"{class_var}.Event_Id AS {prefix}_class_id,\n"
        f"               {class_var}.activity AS {prefix}_activity,\n"
        f"               properties({class_var}) AS {prefix}_details,\n"
        f"               {class_var}.Count AS {prefix}_count"
    )


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


def _robot_handover_match_fragment() -> str:
    return f"""
        MATCH (c1:Class)<-[:{OBS_REL}]-(e1:Event)-[df:{DF_REL}]->(e2:Event)-[:{OBS_REL}]->(c2:Class)
        MATCH (e1)-[:{CORR_REL}]->(o:Entity {{type: $objective_type}})<-[:{CORR_REL}]-(e2)
        MATCH (e1)-[:{CORR_REL}]->(r1:Entity {{type: 'Robot'}})
        MATCH (e2)-[:{CORR_REL}]->(r2:Entity {{type: 'Robot'}})
        OPTIONAL MATCH (e1)-[:{CORR_REL}]->(s1:Entity {{type:'Segment'}})
        OPTIONAL MATCH (e2)-[:{CORR_REL}]->(s2:Entity {{type:'Segment'}})
        WHERE r1 <> r2
          AND e1.Type = 'Task' AND e2.Type = 'Task'
          AND {_df_type_expr('df')} = $objective_type
          AND toString(df.perspective_id) = toString(o.id)
          AND NOT EXISTS {{
              MATCH (e1)-[df_r1:{DF_REL}]->(ek:Event)
              MATCH (ek)-[:{CORR_REL}]->(o)
              MATCH (ek)-[:{CORR_REL}]->(r1)
              WHERE {_df_type_expr('df_r1')} = 'Robot'
                AND toString(df_r1.perspective_id) = toString(r1.id)
                AND ek.start IS NOT NULL
                AND ek.end IS NOT NULL
                AND e2.start IS NOT NULL
                AND e2.end IS NOT NULL
                AND ek.start < e2.end
                AND e2.start < ek.end
          }}
          AND NOT EXISTS {{
              MATCH (el:Event)-[df_r2:{DF_REL}]->(e2)
              MATCH (el)-[:{CORR_REL}]->(o)
              MATCH (el)-[:{CORR_REL}]->(r2)
              WHERE {_df_type_expr('df_r2')} = 'Robot'
                AND toString(df_r2.perspective_id) = toString(r2.id)
                AND el.start IS NOT NULL
                AND el.end IS NOT NULL
                AND e1.start IS NOT NULL
                AND e1.end IS NOT NULL
                AND el.start < e1.end
                AND e1.start < el.end
          }}
    """.strip()


def _objective_switch_match_fragment() -> str:
    return f"""
        MATCH (c1:Class)<-[:{OBS_REL}]-(e1:Event)-[df:{DF_REL}]->(e2:Event)-[:{OBS_REL}]->(c2:Class)
        MATCH (e1)-[:{CORR_REL}]->(robot:Entity {{type: 'Robot'}})<-[:{CORR_REL}]-(e2)
        MATCH (e1)-[:{CORR_REL}]->(o1:Entity {{type: $objective_type}})
        MATCH (e2)-[:{CORR_REL}]->(o2:Entity {{type: $objective_type}})
        WHERE o1 <> o2
          AND e1.Type = 'Task' AND e2.Type = 'Task'
          AND {_df_type_expr('df')} = 'Robot'
          AND toString(df.perspective_id) = toString(robot.id)
    """.strip()


def _capability_return_match_fragment(objective_type: str) -> str:
    segment_consistency = ""
    if objective_type == "Mission":
        segment_consistency = """
          AND (
            NOT (
              EXISTS { MATCH (e1)-[:CORR]->(:Entity {type: 'Segment'}) }
              AND EXISTS { MATCH (e2)-[:CORR]->(:Entity {type: 'Segment'}) }
              AND EXISTS { MATCH (e3)-[:CORR]->(:Entity {type: 'Segment'}) }
            )
            OR EXISTS {
              MATCH (e1)-[:CORR]->(shared_segment:Entity {type: 'Segment'})
              MATCH (e2)-[:CORR]->(shared_segment)
              MATCH (e3)-[:CORR]->(shared_segment)
            }
          )
        """
    return f"""
        MATCH (c1:Class)<-[:{OBS_REL}]-(e1:Event)-[df1:{DF_REL}]->(e2:Event)-[df2:{DF_REL}]->(e3:Event)-[:{OBS_REL}]->(c3:Class)
        MATCH (e1)-[:{CORR_REL}]->(o:Entity {{type: $objective_type}})<-[:{CORR_REL}]-(e2)
        MATCH (e3)-[:{CORR_REL}]->(o)
        MATCH (e1)-[:{CORR_REL}]->(returning:Entity {{type: 'Robot'}})<-[:{CORR_REL}]-(e3)
        MATCH (e2)-[:{CORR_REL}]->(intermediate:Entity {{type: 'Robot'}})
        MATCH (e2)-[:REQ]->(cap)<-[:HAS]-(intermediate)
        WHERE returning <> intermediate
          AND e1.Type = 'Task' AND e2.Type = 'Task' AND e3.Type = 'Task'
          AND NOT (returning)-[:HAS]->(cap)
          AND {_df_type_expr('df1')} = $objective_type
          AND {_df_type_expr('df2')} = $objective_type
          AND toString(df1.perspective_id) = toString(o.id)
          AND toString(df2.perspective_id) = toString(o.id)
          {segment_consistency}
    """.strip()


def _parallel_collaboration_match_fragment(objective_type: str) -> str:
    same_mission_match = ""
    if objective_type == "Segment":
        same_mission_match = "MATCH (o1)-[:PART_OF]->(mission:Entity {type: 'Mission'})<-[:PART_OF]-(o2)"
    return f"""
        MATCH (o1:Entity {{type: $objective_type}}), (o2:Entity {{type: $objective_type}})
        WHERE toString(o1.id) < toString(o2.id)
        {same_mission_match}
        MATCH (o1)<-[:{CORR_REL}]-(e1:Event)-[:{OBS_REL}]->(c1:Class)
        MATCH (o2)<-[:{CORR_REL}]-(e2:Event)-[:{OBS_REL}]->(c2:Class)
        WHERE e1.Type = 'Task' AND e2.Type = 'Task'
        WITH o1, o2,
             min(e1.start) AS start1, max(e1.end) AS end1,
             min(e2.start) AS start2, max(e2.end) AS end2,
             collect(DISTINCT {{event:e1,class:c1}}) AS left_items,
             collect(DISTINCT {{event:e2,class:c2}}) AS right_items
        WHERE start1 < end2 AND start2 < end1
        WITH o1, o2, start1, end1, start2, end2,
             head([x IN left_items WHERE x.event.start = start1 | x.class]) AS c1,
             head([x IN right_items WHERE x.event.start = start2 | x.class]) AS c2,
             CASE WHEN start1 >= start2 THEN start1 ELSE start2 END AS overlap_start,
             CASE WHEN end1 <= end2 THEN end1 ELSE end2 END AS overlap_end
        MATCH (o1)<-[:{CORR_REL}]-(teamEvent1:Event)-[:{CORR_REL}]->(r1:Entity {{type: 'Robot'}})
        WHERE teamEvent1.Type = 'Task'
        WITH o1, o2, c1, c2, start1, end1, start2, end2, overlap_start, overlap_end,
             collect(DISTINCT r1) AS team1
        MATCH (o2)<-[:{CORR_REL}]-(teamEvent2:Event)-[:{CORR_REL}]->(r2:Entity {{type: 'Robot'}})
        WHERE teamEvent2.Type = 'Task'
        WITH o1, o2, c1, c2, start1, end1, start2, end2, overlap_start, overlap_end,
             team1, collect(DISTINCT r2) AS team2
        OPTIONAL MATCH (o1)<-[:{CORR_REL}]-(reqEvent1:Event)-[:REQ]->(cap1:Capability)
        WHERE reqEvent1.Type = 'Task'
        WITH o1, o2, c1, c2, start1, end1, start2, end2, overlap_start, overlap_end,
             team1, team2, collect(DISTINCT cap1) AS req1
        OPTIONAL MATCH (o2)<-[:{CORR_REL}]-(reqEvent2:Event)-[:REQ]->(cap2:Capability)
        WHERE reqEvent2.Type = 'Task'
        WITH o1, o2, c1, c2, start1, end1, start2, end2, overlap_start, overlap_end,
             team1, team2, req1, collect(DISTINCT cap2) AS req2,
             [r IN team1 WHERE r IN team2] AS shared_robots
        WITH o1, o2, c1, c2, start1, end1, start2, end2, overlap_start, overlap_end,
             team1, team2, shared_robots, [cap IN req1 WHERE cap IN req2] AS shared_required_capabilities
        WHERE size(team1) + size([r IN team2 WHERE NOT r IN team1]) > 1
    """.strip()


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
    if pattern == "Robot handover":
        return f"""
        {_robot_handover_match_fragment()}
        RETURN toString(o.id) AS objective_id, toString(r1.id) AS from_robot,
               {_matched_class_projection('c1', 'c2')},
               toString(r2.id) AS to_robot, toString(s1.id) AS from_segment,
               toString(s2.id) AS to_segment, coalesce(e1.Event_Id,e1.event_id,e1.id,elementId(e1)) AS from_event,
               coalesce(e2.Event_Id,e2.event_id,e2.id,elementId(e2)) AS to_event,
               e1.activity AS from_activity, e2.activity AS to_activity,
               e1.start AS from_start, e1.end AS from_end, e2.start AS to_start, e2.end AS to_end,
               [(r1)<-[:CORR]-(ce:Event)
                 WHERE ce.Type = 'Control' AND ce.start <= e2.start AND e1.end <= ce.end |
                 {{event_id: coalesce(ce.event_id, ce.id), activity: ce.activity, start: ce.start, end: ce.end}}]
                 AS from_robot_control_events,
               [(r2)<-[:CORR]-(ce:Event)
                 WHERE ce.Type = 'Control' AND ce.start <= e2.start AND e1.end <= ce.end |
                 {{event_id: coalesce(ce.event_id, ce.id), activity: ce.activity, start: ce.start, end: ce.end}}]
                 AS to_robot_control_events,
               {_transition_expr('df', 'e1', 'e2')} AS duration_seconds
        ORDER BY from_start
        """
    if pattern == "Objective switch":
        return f"""
        {_objective_switch_match_fragment()}
        RETURN toString(robot.id) AS robot_id, toString(o1.id) AS from_objective,
               {_matched_class_projection('c1', 'c2')},
               toString(o2.id) AS to_objective, coalesce(e1.Event_Id,e1.event_id,e1.id,elementId(e1)) AS from_event,
               coalesce(e2.Event_Id,e2.event_id,e2.id,elementId(e2)) AS to_event,
               e1.activity AS from_activity, e2.activity AS to_activity,
               e1.start AS from_start, e1.end AS from_end, e2.start AS to_start, e2.end AS to_end,
               [(robot)<-[:CORR]-(ce:Event)
                 WHERE ce.Type = 'Control' AND ce.start <= e2.start AND e1.end <= ce.end |
                 {{event_id: coalesce(ce.event_id, ce.id), activity: ce.activity, start: ce.start, end: ce.end}}]
                 AS control_events,
               {_transition_expr('df', 'e1', 'e2')} AS duration_seconds
        ORDER BY from_start
        """
    if pattern == "Capability-driven return":
        return f"""
        {_capability_return_match_fragment(objective_type)}
        WITH o, returning, intermediate, c1, c3, e1, e2, e3, collect(DISTINCT cap) AS capabilities
        RETURN toString(o.id) AS objective_id, toString(returning.id) AS returning_robot,
               {_matched_class_projection('c1', 'c3')},
               toString(intermediate.id) AS intermediate_robot,
               [cap IN capabilities | coalesce(cap.id, cap.name, elementId(cap))] AS capabilities,
               coalesce(e1.Event_Id,e1.event_id,e1.id,elementId(e1)) AS from_event,
               coalesce(e2.Event_Id,e2.event_id,e2.id,elementId(e2)) AS intermediate_event,
               coalesce(e3.Event_Id,e3.event_id,e3.id,elementId(e3)) AS return_event,
               e1.activity AS from_activity, e2.activity AS intermediate_activity, e3.activity AS return_activity,
               e1.start AS from_start, e2.start AS intermediate_start, e3.start AS return_start,
               duration.inSeconds(e1.end, e3.start).seconds AS duration_seconds
        ORDER BY from_start
        """
    return f"""
    {_parallel_collaboration_match_fragment(objective_type)}
    RETURN toString(o1.id) AS left_objective, toString(o2.id) AS right_objective,
           {_matched_class_projection('c1', 'c2')},
           [r IN team1 | toString(r.id)] AS left_team,
           [r IN team2 | toString(r.id)] AS right_team,
           [r IN shared_robots | toString(r.id)] AS shared_robots,
           [cap IN shared_required_capabilities | coalesce(cap.id, cap.name, elementId(cap))]
             AS shared_required_capabilities,
           [cap IN shared_required_capabilities | {{capability: coalesce(cap.id, cap.name, elementId(cap)),
             providers: [r IN shared_robots WHERE EXISTS {{ MATCH (r)-[:HAS]->(cap) }} | toString(r.id)]}}]
             AS shared_capability_providers,
           start1,end1,start2,end2,overlap_start,overlap_end,
           duration.inSeconds(overlap_start,overlap_end).seconds AS duration_seconds
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
