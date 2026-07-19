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
) -> str:
    """Create Class nodes from the entities correlated with each event."""

    selected = [mission_choice, robot_choice, segment_choice]
    dimensions = [AGGREGATION_OPTIONS[choice] for choice in selected]

    value_expressions = []
    class_properties = ["Type: 'Class'"]
    class_id_parts = ["toString(e.activity)"]
    display_parts = ["'activity=' + toString(e.activity)"]

    for alias, property_name, class_property in dimensions:
        variable = class_property.lower()

        if alias == "segment":
            value_expression = f"coalesce(toString({alias}.{property_name}), '<NO_SEGMENT>')"
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

    return f"""
    MATCH (e:Event)-[:CORR]->(mission:Entity {{type: 'Mission'}})
    MATCH (e)-[:CORR]->(robot:Entity {{type: 'Robot'}})
    OPTIONAL MATCH (e)-[:CORR]->(segment:Entity {{type: 'Segment'}})
    WHERE e.activity IS NOT NULL
    WITH e,
         {values}
    WITH e,
         {class_id} AS class_id,
         {display_name} AS display_name,
         {dimension_variables}
    MERGE (c:Class {{
        Event_Id: class_id,
        {properties}
    }})
    SET c.activity = e.activity,
        c.DisplayName = display_name
    MERGE (e)-[:OBS]->(c)
    RETURN count(DISTINCT c) AS class_count,
           count(DISTINCT e) AS observed_events
    """


def class_df_aggregation_query() -> str:
    return """
    MATCH (c1:Class)<-[:OBS]-(e1:Event)-[r:DF]->(e2:Event)-[:OBS]->(c2:Class)
    WITH coalesce(r.Type, r.type, r.perspective_type, 'DF') AS CType,
         c1,
         c2,
         count(r) AS df_freq,
         avg(
             coalesce(
                 r.transitionTimeSeconds,
                 CASE
                   WHEN e1.end IS NOT NULL AND e2.start IS NOT NULL
                   THEN duration.inSeconds(e1.end, e2.start).seconds
                   ELSE null
                 END
             )
         ) AS avg_transition_seconds
    MERGE (c1)-[dfc:DF_C {Type: CType}]->(c2)
    SET dfc.edge_weight = df_freq,
        dfc.avg_transition_seconds = avg_transition_seconds
    RETURN count(dfc) AS dfc_count
    """


def set_class_weight_query() -> str:
    return """
    MATCH (:Event)-[obs:OBS]->(c:Class)
    WITH c, count(obs) AS weight
    SET c.Count = weight
    RETURN count(c) AS weighted_classes
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
        class_record = session.run(
            create_class_query(mission_choice, robot_choice, segment_choice)
        ).single()
        dfc_record = session.run(class_df_aggregation_query()).single()
        weight_record = session.run(set_class_weight_query()).single()

    return {
        "classes": int(class_record["class_count"] if class_record else 0),
        "observed_events": int(class_record["observed_events"] if class_record else 0),
        "dfc_edges": int(dfc_record["dfc_count"] if dfc_record else 0),
        "weighted_classes": int(weight_record["weighted_classes"] if weight_record else 0),
    }


def fetch_class_graph(
    driver: Any,
    database: Optional[str],
    min_frequency: int,
    limit: int,
) -> List[Dict[str, Any]]:
    query = """
    MATCH (c1:Class)-[r:DF_C]->(c2:Class)
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
