from __future__ import annotations

from typing import Any, Dict, List, Optional

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


def pattern_summary_query(pattern: str, objective_type: str) -> str:
    if pattern == "Robot handover":
        return f"""
        MATCH (c1:Class)<-[:{OBS_REL}]-(e1:Event)-[df:{DF_REL}]->(e2:Event)-[:{OBS_REL}]->(c2:Class)
        MATCH (e1)-[:{CORR_REL}]->(o:Entity {{type: $objective_type}})<-[:{CORR_REL}]-(e2)
        MATCH (e1)-[:{CORR_REL}]->(r1:Entity {{type: 'Robot'}})
        MATCH (e2)-[:{CORR_REL}]->(r2:Entity {{type: 'Robot'}})
        WHERE r1 <> r2
          AND coalesce(df.type, df.perspective_type, df.Type) = $objective_type
          AND toString(df.perspective_id) = toString(o.id)
        WITH c1, c2, count(*) AS frequency,
             avg(coalesce(df.transitionTimeSeconds,
                 CASE WHEN e1.end IS NOT NULL AND e2.start IS NOT NULL
                      THEN duration.inSeconds(e1.end, e2.start).seconds END)) AS avg_seconds
        RETURN $pattern AS pattern, $objective_type AS objective_type,
               c1.Event_Id AS source_id, c1.activity AS source_activity,
               c2.Event_Id AS target_id, c2.activity AS target_activity,
               frequency, avg_seconds
        """
    if pattern == "Objective switch":
        return f"""
        MATCH (c1:Class)<-[:{OBS_REL}]-(e1:Event)-[df:{DF_REL}]->(e2:Event)-[:{OBS_REL}]->(c2:Class)
        MATCH (e1)-[:{CORR_REL}]->(robot:Entity {{type: 'Robot'}})<-[:{CORR_REL}]-(e2)
        MATCH (e1)-[:{CORR_REL}]->(o1:Entity {{type: $objective_type}})
        MATCH (e2)-[:{CORR_REL}]->(o2:Entity {{type: $objective_type}})
        WHERE o1 <> o2
          AND coalesce(df.type, df.perspective_type, df.Type) = 'Robot'
          AND toString(df.perspective_id) = toString(robot.id)
        WITH c1, c2, count(*) AS frequency,
             avg(coalesce(df.transitionTimeSeconds,
                 CASE WHEN e1.end IS NOT NULL AND e2.start IS NOT NULL
                      THEN duration.inSeconds(e1.end, e2.start).seconds END)) AS avg_seconds
        RETURN $pattern AS pattern, $objective_type AS objective_type,
               c1.Event_Id AS source_id, c1.activity AS source_activity,
               c2.Event_Id AS target_id, c2.activity AS target_activity,
               frequency, avg_seconds
        """
    if pattern == "Capability-driven return":
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
        WHERE returning <> intermediate AND NOT (returning)-[:HAS]->(cap)
          AND coalesce(df1.type, df1.perspective_type, df1.Type) = $objective_type
          AND coalesce(df2.type, df2.perspective_type, df2.Type) = $objective_type
        WITH c1, c3, count(*) AS frequency,
             avg(coalesce(df1.transitionTimeSeconds, 0) +
                 CASE WHEN e2.start IS NOT NULL AND e2.end IS NOT NULL
                      THEN duration.inSeconds(e2.start, e2.end).seconds ELSE 0 END +
                 coalesce(df2.transitionTimeSeconds, 0)) AS avg_seconds
        RETURN $pattern AS pattern, $objective_type AS objective_type,
               c1.Event_Id AS source_id, c1.activity AS source_activity,
               c3.Event_Id AS target_id, c3.activity AS target_activity,
               frequency, avg_seconds
        """
    if pattern == "Co-participation":
        return f"""
        MATCH (o:Entity {{type: $objective_type}})<-[:{CORR_REL}]-(e:Event)-[:{CORR_REL}]->(robot:Entity {{type: 'Robot'}})
        MATCH (e)-[:{OBS_REL}]->(c:Class)
        WITH o, collect(DISTINCT robot) AS team, collect(DISTINCT {{event:e, class:c}}) AS items
        WHERE size(team) > 1
        UNWIND items AS item
        WITH o, team, item ORDER BY item.event.start, item.event.end
        WITH o, team, collect(item) AS ordered
        WITH head(ordered).class AS c1, last(ordered).class AS c2,
             CASE WHEN head(ordered).event.start IS NOT NULL AND last(ordered).event.end IS NOT NULL
                  THEN duration.inSeconds(head(ordered).event.start, last(ordered).event.end).seconds END AS seconds
        WITH c1, c2, count(*) AS frequency, avg(seconds) AS avg_seconds
        RETURN $pattern AS pattern, $objective_type AS objective_type,
               c1.Event_Id AS source_id, c1.activity AS source_activity,
               c2.Event_Id AS target_id, c2.activity AS target_activity,
               frequency, avg_seconds
        """
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
    WITH head([x IN left_items WHERE x.event.start = start1 | x.class]) AS c1,
         head([x IN right_items WHERE x.event.start = start2 | x.class]) AS c2,
         CASE WHEN start1 >= start2 THEN start1 ELSE start2 END AS overlap_start,
         CASE WHEN end1 <= end2 THEN end1 ELSE end2 END AS overlap_end
    WITH c1, c2, count(*) AS frequency,
         avg(duration.inSeconds(overlap_start, overlap_end).seconds) AS avg_seconds
    RETURN $pattern AS pattern, $objective_type AS objective_type,
           c1.Event_Id AS source_id, c1.activity AS source_activity,
           c2.Event_Id AS target_id, c2.activity AS target_activity,
           frequency, avg_seconds
    """


def fetch_pattern_summary(
    driver: Any,
    database: Optional[str],
    pattern: str,
    objective_type: str,
    min_frequency: int,
) -> List[Dict[str, Any]]:
    rows = run_query(
        driver,
        database,
        pattern_summary_query(pattern, objective_type),
        {"pattern": pattern, "objective_type": objective_type},
    )
    return [row for row in rows if int(row.get("frequency") or 0) >= min_frequency]


def occurrence_query(pattern: str) -> str:
    if pattern == "Robot handover":
        return f"""
        MATCH (c1:Class)<-[:{OBS_REL}]-(e1:Event)-[df:{DF_REL}]->(e2:Event)-[:{OBS_REL}]->(c2:Class)
        MATCH (e1)-[:{CORR_REL}]->(o:Entity {{type:$objective_type}})<-[:{CORR_REL}]-(e2)
        MATCH (e1)-[:{CORR_REL}]->(r1:Entity {{type:'Robot'}})
        MATCH (e2)-[:{CORR_REL}]->(r2:Entity {{type:'Robot'}})
        OPTIONAL MATCH (e1)-[:{CORR_REL}]->(s1:Entity {{type:'Segment'}})
        OPTIONAL MATCH (e2)-[:{CORR_REL}]->(s2:Entity {{type:'Segment'}})
        WHERE c1.Event_Id=$source_id AND c2.Event_Id=$target_id AND r1<>r2
          AND coalesce(df.type,df.perspective_type,df.Type)=$objective_type
        RETURN toString(o.id) AS objective_id, toString(r1.id) AS from_robot,
               toString(r2.id) AS to_robot, toString(s1.id) AS from_segment,
               toString(s2.id) AS to_segment, coalesce(e1.Event_Id,e1.id,toString(id(e1))) AS from_event,
               coalesce(e2.Event_Id,e2.id,toString(id(e2))) AS to_event,
               e1.activity AS from_activity, e2.activity AS to_activity,
               e1.start AS from_start, e1.end AS from_end, e2.start AS to_start, e2.end AS to_end,
               coalesce(df.transitionTimeSeconds,
                 CASE WHEN e1.end IS NOT NULL AND e2.start IS NOT NULL THEN duration.inSeconds(e1.end,e2.start).seconds END) AS duration_seconds
        ORDER BY from_start LIMIT $limit
        """
    if pattern == "Objective switch":
        return f"""
        MATCH (c1:Class)<-[:{OBS_REL}]-(e1:Event)-[df:{DF_REL}]->(e2:Event)-[:{OBS_REL}]->(c2:Class)
        MATCH (e1)-[:{CORR_REL}]->(robot:Entity {{type:'Robot'}})<-[:{CORR_REL}]-(e2)
        MATCH (e1)-[:{CORR_REL}]->(o1:Entity {{type:$objective_type}})
        MATCH (e2)-[:{CORR_REL}]->(o2:Entity {{type:$objective_type}})
        WHERE c1.Event_Id=$source_id AND c2.Event_Id=$target_id AND o1<>o2
        RETURN toString(robot.id) AS robot_id, toString(o1.id) AS from_objective,
               toString(o2.id) AS to_objective, coalesce(e1.Event_Id,e1.id,toString(id(e1))) AS from_event,
               coalesce(e2.Event_Id,e2.id,toString(id(e2))) AS to_event,
               e1.activity AS from_activity, e2.activity AS to_activity,
               e1.start AS from_start, e1.end AS from_end, e2.start AS to_start, e2.end AS to_end,
               coalesce(df.transitionTimeSeconds,
                 CASE WHEN e1.end IS NOT NULL AND e2.start IS NOT NULL THEN duration.inSeconds(e1.end,e2.start).seconds END) AS duration_seconds
        ORDER BY from_start LIMIT $limit
        """
    if pattern == "Capability-driven return":
        return f"""
        MATCH (c1:Class)<-[:{OBS_REL}]-(e1:Event)-[df1:{DF_REL}]->(e2:Event)-[df2:{DF_REL}]->(e3:Event)-[:{OBS_REL}]->(c3:Class)
        MATCH (e1)-[:{CORR_REL}]->(o:Entity {{type:$objective_type}})<-[:{CORR_REL}]-(e2)
        MATCH (e3)-[:{CORR_REL}]->(o)
        MATCH (e1)-[:{CORR_REL}]->(returning:Entity {{type:'Robot'}})<-[:{CORR_REL}]-(e3)
        MATCH (e2)-[:{CORR_REL}]->(intermediate:Entity {{type:'Robot'}})
        MATCH (e2)-[:REQ]->(cap)<-[:HAS]-(intermediate)
        WHERE c1.Event_Id=$source_id AND c3.Event_Id=$target_id AND returning<>intermediate
          AND NOT (returning)-[:HAS]->(cap)
        RETURN toString(o.id) AS objective_id, toString(returning.id) AS returning_robot,
               toString(intermediate.id) AS intermediate_robot,
               coalesce(cap.id,cap.name,toString(id(cap))) AS capability,
               coalesce(e1.Event_Id,e1.id,toString(id(e1))) AS from_event,
               coalesce(e2.Event_Id,e2.id,toString(id(e2))) AS intermediate_event,
               coalesce(e3.Event_Id,e3.id,toString(id(e3))) AS return_event,
               e1.activity AS from_activity, e2.activity AS intermediate_activity, e3.activity AS return_activity,
               e1.start AS from_start, e2.start AS intermediate_start, e3.start AS return_start,
               coalesce(df1.transitionTimeSeconds,0) +
               CASE WHEN e2.start IS NOT NULL AND e2.end IS NOT NULL THEN duration.inSeconds(e2.start,e2.end).seconds ELSE 0 END +
               coalesce(df2.transitionTimeSeconds,0) AS duration_seconds
        ORDER BY from_start LIMIT $limit
        """
    if pattern == "Co-participation":
        return f"""
        MATCH (o:Entity {{type:$objective_type}})<-[:{CORR_REL}]-(e:Event)-[:{CORR_REL}]->(robot:Entity {{type:'Robot'}})
        MATCH (e)-[:{OBS_REL}]->(c:Class)
        WITH o, collect(DISTINCT robot) AS team, collect(DISTINCT {{event:e,class:c}}) AS items
        WHERE size(team)>1
        UNWIND items AS item
        WITH o, team, item ORDER BY item.event.start
        WITH o, team, collect(item) AS ordered
        WHERE head(ordered).class.Event_Id=$source_id AND last(ordered).class.Event_Id=$target_id
        RETURN toString(o.id) AS objective_id,
               [r IN team | toString(r.id)] AS robots,
               size(team) AS team_size,
               [x IN ordered | coalesce(x.event.Event_Id,x.event.id,toString(id(x.event)))] AS events,
               [x IN ordered | x.event.activity] AS activities,
               head(ordered).event.start AS start,
               last(ordered).event.end AS end,
               CASE WHEN head(ordered).event.start IS NOT NULL AND last(ordered).event.end IS NOT NULL
                    THEN duration.inSeconds(head(ordered).event.start,last(ordered).event.end).seconds END AS duration_seconds
        ORDER BY start LIMIT $limit
        """
    return f"""
    MATCH (o1:Entity {{type:$objective_type}}), (o2:Entity {{type:$objective_type}})
    WHERE toString(o1.id)<toString(o2.id)
    MATCH (o1)<-[:{CORR_REL}]-(e1:Event)-[:{OBS_REL}]->(c1:Class)
    MATCH (o2)<-[:{CORR_REL}]-(e2:Event)-[:{OBS_REL}]->(c2:Class)
    WITH o1,o2,min(e1.start) AS start1,max(e1.end) AS end1,min(e2.start) AS start2,max(e2.end) AS end2,
         collect(DISTINCT {{event:e1,class:c1}}) AS left_items, collect(DISTINCT {{event:e2,class:c2}}) AS right_items
    WHERE start1<end2 AND start2<end1
    WITH o1,o2,start1,end1,start2,end2,
         head([x IN left_items WHERE x.event.start=start1 | x.class]) AS left_class,
         head([x IN right_items WHERE x.event.start=start2 | x.class]) AS right_class,
         CASE WHEN start1>=start2 THEN start1 ELSE start2 END AS overlap_start,
         CASE WHEN end1<=end2 THEN end1 ELSE end2 END AS overlap_end
    WHERE left_class.Event_Id=$source_id AND right_class.Event_Id=$target_id
    RETURN toString(o1.id) AS left_objective, toString(o2.id) AS right_objective,
           start1,end1,start2,end2,overlap_start,overlap_end,
           duration.inSeconds(overlap_start,overlap_end).seconds AS duration_seconds
    ORDER BY overlap_start LIMIT $limit
    """


def fetch_occurrences(
    driver: Any,
    database: Optional[str],
    pattern: str,
    objective_type: str,
    source_id: str,
    target_id: str,
    limit: int,
) -> List[Dict[str, Any]]:
    return run_query(
        driver,
        database,
        occurrence_query(pattern),
        {
            "objective_type": objective_type,
            "source_id": source_id,
            "target_id": target_id,
            "limit": limit,
        },
    )
