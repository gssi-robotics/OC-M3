from __future__ import annotations

from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from collaboration.collaboration_data import run_query


PATTERN_COLORS = {
    "Robot handover": "#B22222",
    "Objective switch": "#E76F51",
    "Capability-driven return": "#264653",
    "Co-participation": "#7B2CBF",
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
          AND {_df_type_expr('df')} = $objective_type
          AND toString(df.perspective_id) = toString(o.id)
    """.strip()


def _objective_switch_match_fragment() -> str:
    return f"""
        MATCH (c1:Class)<-[:{OBS_REL}]-(e1:Event)-[df:{DF_REL}]->(e2:Event)-[:{OBS_REL}]->(c2:Class)
        MATCH (e1)-[:{CORR_REL}]->(robot:Entity {{type: 'Robot'}})<-[:{CORR_REL}]-(e2)
        MATCH (e1)-[:{CORR_REL}]->(o1:Entity {{type: $objective_type}})
        MATCH (e2)-[:{CORR_REL}]->(o2:Entity {{type: $objective_type}})
        WHERE o1 <> o2
          AND {_df_type_expr('df')} = 'Robot'
          AND toString(df.perspective_id) = toString(robot.id)
    """.strip()


def _capability_return_match_fragment(objective_type: str) -> str:
    segment_clause = ""
    if objective_type == "Mission":
        segment_clause = f"""
        MATCH (e1)-[:{CORR_REL}]->(seg:Entity {{type: 'Segment'}})<-[:{CORR_REL}]-(e2)
        MATCH (e3)-[:{CORR_REL}]->(seg)
        """
    return f"""
        MATCH (c1:Class)<-[:{OBS_REL}]-(e1:Event)-[df1:{DF_REL}]->(e2:Event)-[df2:{DF_REL}]->(e3:Event)-[:{OBS_REL}]->(c3:Class)
        MATCH (e1)-[:{CORR_REL}]->(o:Entity {{type: $objective_type}})<-[:{CORR_REL}]-(e2)
        MATCH (e3)-[:{CORR_REL}]->(o)
        {segment_clause}
        MATCH (e1)-[:{CORR_REL}]->(returning:Entity {{type: 'Robot'}})<-[:{CORR_REL}]-(e3)
        MATCH (e2)-[:{CORR_REL}]->(intermediate:Entity {{type: 'Robot'}})
        MATCH (e2)-[:REQ]->(cap)<-[:HAS]-(intermediate)
        WHERE returning <> intermediate
          AND NOT (returning)-[:HAS]->(cap)
          AND {_df_type_expr('df1')} = $objective_type
          AND {_df_type_expr('df2')} = $objective_type
    """.strip()


def _co_participation_match_fragment() -> str:
    return f"""
        MATCH (o:Entity {{type: $objective_type}})<-[:{CORR_REL}]-(e:Event)-[:{CORR_REL}]->(robot:Entity {{type: 'Robot'}})
        MATCH (e)-[:{OBS_REL}]->(c:Class)
        WITH o, collect(DISTINCT robot) AS team, collect(DISTINCT {{event:e, class:c}}) AS items
        WHERE size(team) > 1
        UNWIND items AS item
        WITH o, team, item ORDER BY item.event.start, item.event.end
        WITH o, team, collect(item) AS ordered
        WITH o, team, ordered,
             head(ordered).class AS c1,
             last(ordered).class AS c2,
             CASE WHEN head(ordered).event.start IS NOT NULL AND last(ordered).event.end IS NOT NULL
                  THEN duration.inSeconds(head(ordered).event.start, last(ordered).event.end).seconds END AS duration_seconds
    """.strip()


def _parallel_collaboration_match_fragment() -> str:
    return f"""
        MATCH (o1:Entity {{type: $objective_type}}), (o2:Entity {{type: $objective_type}})
        WHERE toString(o1.id) < toString(o2.id)
        MATCH (o1)<-[:{CORR_REL}]-(e1:Event)-[:{OBS_REL}]->(c1:Class)
        MATCH (o2)<-[:{CORR_REL}]-(e2:Event)-[:{OBS_REL}]->(c2:Class)
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
               {_transition_expr('df', 'e1', 'e2')} AS duration_seconds
        ORDER BY from_start
        """
    if pattern == "Capability-driven return":
        return f"""
        {_capability_return_match_fragment(objective_type)}
        RETURN toString(o.id) AS objective_id, toString(returning.id) AS returning_robot,
               {_matched_class_projection('c1', 'c3')},
               toString(intermediate.id) AS intermediate_robot,
               coalesce(cap.id,cap.name,elementId(cap)) AS capability,
               coalesce(e1.Event_Id,e1.event_id,e1.id,elementId(e1)) AS from_event,
               coalesce(e2.Event_Id,e2.event_id,e2.id,elementId(e2)) AS intermediate_event,
               coalesce(e3.Event_Id,e3.event_id,e3.id,elementId(e3)) AS return_event,
               e1.activity AS from_activity, e2.activity AS intermediate_activity, e3.activity AS return_activity,
               e1.start AS from_start, e2.start AS intermediate_start, e3.start AS return_start,
               coalesce(df1.transitionTimeSeconds,0) +
               CASE WHEN e2.start IS NOT NULL AND e2.end IS NOT NULL THEN duration.inSeconds(e2.start,e2.end).seconds ELSE 0 END +
               coalesce(df2.transitionTimeSeconds,0) AS duration_seconds
        ORDER BY from_start
        """
    if pattern == "Co-participation":
        return f"""
        {_co_participation_match_fragment()}
        RETURN toString(o.id) AS objective_id,
               {_matched_class_projection('c1', 'c2')},
               [r IN team | toString(r.id)] AS robots,
               size(team) AS team_size,
               [x IN ordered | coalesce(x.event.Event_Id,x.event.event_id,x.event.id,elementId(x.event))] AS events,
               [x IN ordered | x.event.activity] AS activities,
               head(ordered).event.start AS start,
               last(ordered).event.end AS end,
               duration_seconds
        ORDER BY start
        """
    return f"""
    {_parallel_collaboration_match_fragment()}
    RETURN toString(o1.id) AS left_objective, toString(o2.id) AS right_objective,
           {_matched_class_projection('c1', 'c2')},
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
    return run_query(
        driver,
        database,
        pattern_occurrence_query(pattern, objective_type),
        {
            "objective_type": objective_type,
        },
    )


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
    source_class_id: str,
    target_class_id: str,
    limit: int,
) -> List[Dict[str, Any]]:
    filtered = [
        row
        for row in rows
        if str(row.get("matched_source_class_id") or "") == str(source_class_id)
        and str(row.get("matched_target_class_id") or "") == str(target_class_id)
    ]
    if limit > 0:
        return filtered[:limit]
    return filtered
