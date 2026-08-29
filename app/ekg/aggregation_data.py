from __future__ import annotations

from typing import Any, Dict, List, Optional

import neo4j_shared


AGGREGATION_OPTIONS = {
    "Mission ID": ("mission", "id", "Mission_id"),
    "Mission type": ("mission", "type", "Mission_type"),
    "Robot ID": ("robot", "id", "Robot_id"),
    "Robot type": ("robot", "type", "Robot_type"),
    "Segment ID": ("segment", "id", "Segment_id"),
    "Segment type": ("segment", "type", "Segment_type"),
}


def create_class_query(
    mission_choice: str,
    robot_choice: str,
    segment_choice: str,
    event_type: str = "Task",
) -> str:
    """Create typed Class nodes from the entities correlated with each event."""

    if event_type not in {"Task", "Control"}:
        raise ValueError(f"Unsupported event type for aggregation: {event_type}")

    selected = [mission_choice, robot_choice, segment_choice]
    dimensions = [AGGREGATION_OPTIONS[choice] for choice in selected]

    value_expressions = []
    class_properties = [f"Type: 'Class'", f"type: '{event_type}'"]
    class_id_parts = [f"'{event_type}'", "toString(e.activity)"]
    display_parts = ["'activity=' + toString(e.activity)"]

    for alias, property_name, class_property in dimensions:
        variable = class_property.lower()

        if alias == "segment":
            value_expression = f"coalesce(toString({alias}.{property_name}), '<NO_SEGMENT>')"
        elif alias == "mission" and event_type == "Control":
            value_expression = f"coalesce(toString({alias}.{property_name}), '<NO_MISSION>')"
        else:
            value_expression = f"toString({alias}.{property_name})"

        value_expressions.append(f"{value_expression} AS {variable}")
        class_properties.append(f"{class_property}: {variable}")
        class_id_parts.append(variable)
        display_parts.append(f"'{class_property}=' + {variable}")

    values = ",\n         ".join(value_expressions)
    properties = ", ".join(class_properties)
    class_id = " + '|' + ".join(class_id_parts)
    display_name = " + '\\n' + ".join(display_parts)
    dimension_variables = ", ".join(class_property.lower() for _, _, class_property in dimensions)

    if event_type == "Task":
        event_matches = """MATCH (e:Event {Type: 'Task'})-[:CORR]->(mission:Entity {type: 'Mission'})
    MATCH (e)-[:CORR]->(robot:Entity {type: 'Robot'})
    OPTIONAL MATCH (e)-[:CORR]->(segment:Entity {type: 'Segment'})"""
    else:
        event_matches = """MATCH (e:Event {Type: 'Control'})-[:CORR]->(robot:Entity {type: 'Robot'})
    OPTIONAL MATCH (e)-[:CORR]->(mission:Entity {type: 'Mission'})
    OPTIONAL MATCH (e)-[:CORR]->(segment:Entity {type: 'Segment'})"""

    return f"""
    {event_matches}
    WHERE e.activity IS NOT NULL
    WITH e,
         {values}
    WITH e,
         {class_id} AS class_id,
         {display_name} AS display_name,
         {dimension_variables}
    MERGE (c:Class:{event_type} {{
        Event_Id: class_id,
        {properties}
    }})
    SET c.activity = e.activity,
        c.DisplayName = display_name,
        c.EventType = e.Type
    MERGE (e)-[:OBS]->(c)
    RETURN count(DISTINCT c) AS class_count,
           count(DISTINCT e) AS observed_events
    """


def class_df_aggregation_query() -> str:
    return """
    MATCH (c1:Class:Task)<-[:OBS]-(e1:Event)-[r:DF]->(e2:Event)-[:OBS]->(c2:Class:Task)
    WITH coalesce(r.Type, r.type, r.perspective_type, 'DF') AS CType,
         c1,
         c2,
         count(r) AS df_freq,
         round(avg(
             coalesce(
                 r.transitionTimeSeconds,
                 CASE
                   WHEN e1.end IS NOT NULL AND e2.start IS NOT NULL
                   THEN duration.inSeconds(e1.end, e2.start).seconds
                   ELSE null
                 END
             )
         ), 3) AS avg_transition_seconds
    MERGE (c1)-[dfc:DF_C {Type: CType}]->(c2)
    SET dfc.edge_weight = df_freq,
        dfc.avg_transition_seconds = avg_transition_seconds
    RETURN count(dfc) AS dfc_count
    """


def class_df_control_aggregation_query() -> str:
    """Aggregate robot DF_Control edges, including Control-to-Task transitions."""
    return """
    MATCH (c1:Class)<-[:OBS]-(e1:Event)-[r:DF_Control]->(e2:Event)-[:OBS]->(c2:Class)
    WITH coalesce(r.Type, r.type, r.perspective_type, 'Robot') AS CType,
         c1,
         c2,
         count(r) AS df_freq,
         round(avg(
             coalesce(
                 r.transitionTimeSeconds,
                 CASE
                   WHEN e1.end IS NOT NULL AND e2.start IS NOT NULL
                   THEN duration.inSeconds(e1.end, e2.start).seconds
                   ELSE null
                 END
             )
         ), 3) AS avg_transition_seconds
    MERGE (c1)-[dfc:DF_C_Control {Type: CType}]->(c2)
    SET dfc.edge_weight = df_freq,
        dfc.avg_transition_seconds = avg_transition_seconds
    RETURN count(dfc) AS dfc_control_count,
           count(CASE
             WHEN c1.type = 'Control' AND c2.type = 'Task' THEN 1
           END) AS control_to_task_count,
           count(CASE
             WHEN c1.type = 'Task' AND c2.type = 'Control' THEN 1
           END) AS task_to_control_count
    """


def set_class_weight_query() -> str:
    return """
    MATCH (:Event)-[obs:OBS]->(c:Class)
    WITH c, count(obs) AS weight
    SET c.Count = weight
    RETURN count(c) AS weighted_classes
    """


def set_event_and_class_duration_query() -> str:
    """Store event durations and aggregate them on Task and Control Classes."""
    return """
    MATCH (e:Event)-[:OBS]->(c:Class)
    WHERE e.start IS NOT NULL AND e.end IS NOT NULL
    WITH c, e,
         round(toFloat(e.end.epochMillis - e.start.epochMillis) / 1000.0, 3) AS duration_seconds
    SET e.duration_seconds = duration_seconds
    WITH c,
         count(DISTINCT e) AS duration_event_count,
         round(avg(duration_seconds), 3) AS avg_duration_seconds,
         round(sum(duration_seconds), 3) AS total_duration_seconds
    SET c.duration_event_count = duration_event_count,
        c.avg_duration_seconds = avg_duration_seconds,
        c.total_duration_seconds = total_duration_seconds
    RETURN count(c) AS duration_annotated_classes,
           sum(duration_event_count) AS duration_annotated_events
    """


def set_class_entity_duration_query(choice: str) -> str:
    """Annotate Classes with entity elapsed duration for one dimension."""
    alias, property_name, class_property = AGGREGATION_OPTIONS[choice]
    dimension = alias.capitalize()
    duration_property = (
        f"{dimension}_avg_total_duration_seconds"
        if property_name == "type"
        else f"{dimension}_total_duration_seconds"
    )
    instance_count_property = f"{dimension}_duration_instance_count"

    return f"""
    MATCH (c:Class:Task)
    WHERE c.{class_property} IS NOT NULL
      AND c.{class_property} <> '<NO_SEGMENT>'
    MATCH (entity:Entity {{type: '{dimension}'}})
    WHERE toString(entity.{property_name}) = toString(c.{class_property})
    CALL (entity) {{
      MATCH (entity)<-[:CORR]-(event:Event)
      WHERE event.start IS NOT NULL AND event.end IS NOT NULL
      RETURN min(event.start) AS instance_start,
             max(event.end) AS instance_end
    }}
    WITH c, entity,
         CASE
           WHEN instance_start IS NOT NULL AND instance_end IS NOT NULL
           THEN toFloat(duration.inSeconds(instance_start, instance_end).seconds)
           ELSE null
         END AS instance_duration_seconds
    WHERE instance_duration_seconds IS NOT NULL
    WITH c,
         count(DISTINCT entity) AS represented_instances,
         round(avg(instance_duration_seconds), 3) AS duration_seconds
    SET c.{duration_property} = duration_seconds,
        c.{instance_count_property} = represented_instances
    RETURN count(c) AS annotated_classes
    """


def delete_class_graph_query() -> str:
    return "MATCH (c:Class) DETACH DELETE c"


def materialize_aggregation(
    driver: Any,
    database: Optional[str],
    mission_choice: str,
    robot_choice: str,
    segment_choice: str,
) -> Dict[str, int]:
    with driver.session(**neo4j_shared.session_kwargs(database)) as session:
        session.run(delete_class_graph_query()).consume()
        task_class_record = session.run(
            create_class_query(mission_choice, robot_choice, segment_choice, "Task")
        ).single()
        control_class_record = session.run(
            create_class_query(mission_choice, robot_choice, segment_choice, "Control")
        ).single()
        for choice in (mission_choice, robot_choice, segment_choice):
            session.run(set_class_entity_duration_query(choice)).consume()
        dfc_record = session.run(class_df_aggregation_query()).single()
        dfc_control_record = session.run(class_df_control_aggregation_query()).single()
        duration_record = session.run(set_event_and_class_duration_query()).single()
        weight_record = session.run(set_class_weight_query()).single()

    task_classes = int(task_class_record["class_count"] if task_class_record else 0)
    control_classes = int(control_class_record["class_count"] if control_class_record else 0)
    task_events = int(task_class_record["observed_events"] if task_class_record else 0)
    control_events = int(control_class_record["observed_events"] if control_class_record else 0)
    return {
        "classes": task_classes + control_classes,
        "task_classes": task_classes,
        "control_classes": control_classes,
        "observed_events": task_events + control_events,
        "observed_task_events": task_events,
        "observed_control_events": control_events,
        "dfc_edges": int(dfc_record["dfc_count"] if dfc_record else 0),
        "dfc_control_edges": int(
            dfc_control_record["dfc_control_count"] if dfc_control_record else 0
        ),
        "control_to_task_edges": int(
            dfc_control_record["control_to_task_count"] if dfc_control_record else 0
        ),
        "task_to_control_edges": int(
            dfc_control_record["task_to_control_count"] if dfc_control_record else 0
        ),
        "duration_annotated_classes": int(
            duration_record["duration_annotated_classes"] if duration_record else 0
        ),
        "duration_annotated_events": int(
            duration_record["duration_annotated_events"] if duration_record else 0
        ),
        "weighted_classes": int(weight_record["weighted_classes"] if weight_record else 0),
    }


def fetch_class_durations(
    driver: Any,
    database: Optional[str],
    mission_choice: str,
    robot_choice: str,
    segment_choice: str,
) -> List[Dict[str, Any]]:
    duration_columns: List[str] = []
    for choice in (mission_choice, robot_choice, segment_choice):
        alias, property_name, class_property = AGGREGATION_OPTIONS[choice]
        dimension = alias.capitalize()
        duration_property = (
            f"{dimension}_avg_total_duration_seconds"
            if property_name == "type"
            else f"{dimension}_total_duration_seconds"
        )
        measure = "average instance duration" if property_name == "type" else "instance duration"
        duration_columns.extend([
            f"c.{class_property} AS {alias}_group",
            f"'{measure}' AS {alias}_duration_measure",
            f"c.{dimension}_duration_instance_count AS {alias}_instances",
            f"c.{duration_property} AS {alias}_duration_seconds",
        ])

    selected_columns = ",\n           ".join(duration_columns)
    query = f"""
    MATCH (c:Class:Task)
    RETURN c.Event_Id AS class_id,
           c.activity AS activity,
           c.Count AS observed_events,
           c.avg_duration_seconds AS avg_duration_seconds,
           c.total_duration_seconds AS total_duration_seconds,
           {selected_columns}
    ORDER BY activity, class_id
    """
    with driver.session(**neo4j_shared.session_kwargs(database)) as session:
        return [dict(record) for record in session.run(query)]


def fetch_class_graph(
    driver: Any,
    database: Optional[str],
    min_frequency: int,
    limit: int,
) -> List[Dict[str, Any]]:
    query = """
    MATCH (c1:Class:Task)-[r:DF_C]->(c2:Class:Task)
    WHERE r.edge_weight >= $min_frequency
    RETURN c1.Event_Id AS source_id,
           c1.activity AS source_activity,
           properties(c1) AS source_details,
           c1.Count AS source_count,
           c2.Event_Id AS target_id,
           c2.activity AS target_activity,
           properties(c2) AS target_details,
           c2.Count AS target_count,
           r.Type AS perspective,
           r.edge_weight AS frequency,
           r.avg_transition_seconds AS avg_transition_seconds
    ORDER BY frequency DESC, source_activity, target_activity
    LIMIT $limit
    """
    with driver.session(**neo4j_shared.session_kwargs(database)) as session:
        return [
            dict(record)
            for record in session.run(
                query,
                min_frequency=min_frequency,
                limit=limit,
            )
        ]


def fetch_class_starts(
    driver: Any,
    database: Optional[str],
    min_frequency: int,
    limit: int,
) -> List[Dict[str, Any]]:
    """Return first observed Task classes for each object perspective."""
    query = """
    MATCH (entity:Entity)<-[:CORR]-(event:Event {Type: 'Task'})-[:OBS]->(c:Class)
    WHERE entity.type IN ['Mission', 'Robot', 'Segment']
      AND NOT EXISTS {
        MATCH (entity)<-[:CORR]-(previous:Event {Type: 'Task'})-[incoming:DF]->(event)
        WHERE coalesce(incoming.Type, incoming.type, incoming.perspective_type, 'DF') = entity.type
          AND toString(incoming.perspective_id) = toString(entity.id)
      }
    WITH entity.type AS perspective,
         c,
         count(DISTINCT entity) AS frequency
    WHERE frequency >= $min_frequency
    RETURN perspective,
           c.Event_Id AS target_id,
           c.activity AS target_activity,
           properties(c) AS target_details,
           c.Count AS target_count,
           frequency
    ORDER BY frequency DESC, perspective, target_activity
    LIMIT $limit
    """
    with driver.session(**neo4j_shared.session_kwargs(database)) as session:
        return [
            dict(record)
            for record in session.run(
                query,
                min_frequency=min_frequency,
                limit=limit,
            )
        ]
