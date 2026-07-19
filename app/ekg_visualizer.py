from __future__ import annotations

from typing import Any, Dict, List, Optional

import streamlit as st

import neo4j_shared


AGGREGATION_OPTIONS = {
    "Mission ID": ("mission", "id", "Mission_id"),
    "Mission type": ("mission", "type", "Mission_type"),
    "Robot ID": ("robot", "id", "Robot_id"),
    "Robot type": ("robot", "type", "Robot_type"),
    "Segment ID": ("segment", "id", "Segment_id"),
    "Segment type": ("segment", "type", "Segment_type"),
}

CLASS_COLOR = "#8EC5FC"
PERSPECTIVE_COLORS = [
    "#D1495B",
    "#2E86AB",
    "#3C8D40",
    "#8E6C8A",
    "#F18F01",
    "#0B6E4F",
    "#6C5CE7",
    "#C44536",
]


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
            value_expression = (
                f"coalesce(toString({alias}.{property_name}), '<NO_SEGMENT>')"
            )
        else:
            value_expression = f"toString({alias}.{property_name})"

        value_expressions.append(
            f"{value_expression} AS {variable}"
        )
        class_properties.append(f"{class_property}: {variable}")
        class_id_parts.append(variable)
        display_parts.append(f"'{class_property}=' + {variable}")

    values = ",\n         ".join(value_expressions)
    properties = ", ".join(class_properties)
    class_id = " + '|' + ".join(class_id_parts)
    display_name = " + '\\n' + ".join(display_parts)

    dimension_variables = ", ".join(
        class_property.lower()
        for _, _, class_property in dimensions
    )

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
        agg_function = create_class_query(mission_choice, robot_choice, segment_choice)
        class_record = session.run(agg_function).single()
        print(f"Aggregation function: {agg_function}")
        dfc_record = session.run(class_df_aggregation_query()).single()
        weight_record = session.run(set_class_weight_query()).single()

    return {
        "classes": int(class_record["class_count"] if class_record else 0),
        "observed_events": int(
            class_record["observed_events"] if class_record else 0
        ),
        "dfc_edges": int(dfc_record["dfc_count"] if dfc_record else 0),
        "weighted_classes": int(
            weight_record["weighted_classes"] if weight_record else 0
        ),
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
        return [dict(record) for record in session.run(
            query,
            min_frequency=min_frequency,
            limit=limit,
        )]



def _tooltip(details: Dict[str, Any], count: int) -> str:
    ordered = sorted((str(key), value) for key, value in (details or {}).items())
    lines = [f"{key}: {value}" for key, value in ordered]
    if not any(key == "Count" for key, _ in ordered):
        lines.append(f"Count: {count}")
    return "\n".join(lines)


def _perspective_colors(rows: List[Dict[str, Any]]) -> Dict[str, str]:
    perspectives = sorted({str(row.get("perspective") or "DF") for row in rows})
    return {
        perspective: PERSPECTIVE_COLORS[index % len(PERSPECTIVE_COLORS)]
        for index, perspective in enumerate(perspectives)
    }


def build_graphviz(
    rows: List[Dict[str, Any]],
    show_time: bool,
    color_by_perspective: Dict[str, str],
) -> str:
    try:
        import graphviz
    except ImportError as exc:
        raise ImportError(
            "Install the `graphviz` Python package and the Graphviz executable."
        ) from exc

    dot = graphviz.Digraph(
        "aggregated_ekg",
        graph_attr={"rankdir": "LR", "bgcolor": "white", "overlap": "false"},
    )
    dot.attr(
        "node",
        shape="box",
        style="rounded,filled",
        fillcolor=CLASS_COLOR,
        fontname="Helvetica",
    )
    dot.attr("edge", fontname="Helvetica", arrowsize="0.8")

    nodes: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        nodes[str(row["source_id"])] = {
            "activity": str(row.get("source_activity") or "n/a"),
            "details": dict(row.get("source_details") or {}),
            "count": int(row.get("source_count") or 0),
        }
        nodes[str(row["target_id"])] = {
            "activity": str(row.get("target_activity") or "n/a"),
            "details": dict(row.get("target_details") or {}),
            "count": int(row.get("target_count") or 0),
        }

    for node_id, node in nodes.items():
        dot.node(
            node_id,
            label=node["activity"],
            tooltip=_tooltip(node["details"], node["count"]),
        )

    for row in rows:
        perspective = str(row.get("perspective") or "DF")
        color = color_by_perspective.get(perspective, "#4A4A4A")
        label = f"{perspective} | n={row['frequency']}"
        if show_time and row.get("avg_transition_seconds") is not None:
            label += f"\navg={float(row['avg_transition_seconds']):.2f}s"
        dot.edge(
            str(row["source_id"]),
            str(row["target_id"]),
            label=label,
            color=color,
            fontcolor=color,
            penwidth=str(1.0 + min(float(row["frequency"]), 20.0) / 4.0),
            tooltip=(
                f"Perspective: {perspective}\n"
                f"Frequency: {row['frequency']}\n"
                f"Average transition: {row.get('avg_transition_seconds')} seconds"
            ),
        )

    return dot.source


def render_perspective_legend(color_by_perspective: Dict[str, str]) -> None:
    """Render a compact perspective legend outside the Graphviz canvas."""
    if not color_by_perspective:
        return

    chips = []
    for perspective, color in color_by_perspective.items():
        chips.append(
            '<span style="display:inline-flex;align-items:center;gap:0.4rem;'
            'padding:0.25rem 0.55rem;margin:0.15rem 0.25rem 0.15rem 0;'
            'border:1px solid #dddddd;border-radius:999px;background:#ffffff;'
            'font-size:0.88rem;">'
            f'<span style="width:0.8rem;height:0.22rem;background:{color};'
            'display:inline-block;border-radius:2px;"></span>'
            f'{perspective}</span>'
        )

    st.markdown(
        '<div style="margin:0.25rem 0 0.8rem 0;">' + ''.join(chips) + '</div>',
        unsafe_allow_html=True,
    )


def clear_state() -> None:
    for key in (
        "agg_connected",
        "agg_error",
        "agg_rows",
        "agg_result",
    ):
        st.session_state.pop(key, None)


def render_page() -> None:
    st.title("Personalized EKG Aggregation")
    st.write(
        "Choose the Mission, Robot, and Segment granularity. The application "
        "creates `:Class` nodes, `:OBS` relationships, and aggregated "
        "`:DF_C` relationships."
    )

    neo4j_shared.render_connection_summary()
    connection = neo4j_shared.get_connection_settings()
    uri = connection["uri"]
    user = connection["user"]
    password = connection["password"]
    database = connection["database"]

    connect_col, reset_col = st.columns(2)
    with connect_col:
        connect_clicked = st.button("Connect")
    with reset_col:
        reset_clicked = st.button("Reset")

    if reset_clicked:
        clear_state()

    if connect_clicked:
        driver, error = neo4j_shared.get_neo4j_driver(uri, user, password)
        if driver is None:
            st.session_state["agg_connected"] = False
            st.session_state["agg_error"] = error
        else:
            st.session_state["agg_connected"] = True
            st.session_state["agg_error"] = None
            driver.close()

    if not st.session_state.get("agg_connected", False):
        if st.session_state.get("agg_error"):
            st.error(st.session_state["agg_error"])
        st.info("Insert the Neo4j connection settings and press Connect.")
        return

    st.subheader("Aggregation")
    c1, c2, c3 = st.columns(3)
    with c1:
        mission_choice = st.radio(
            "Mission",
            ["Mission ID", "Mission type"],
            horizontal=True,
        )
    with c2:
        robot_choice = st.radio(
            "Robot",
            ["Robot ID", "Robot type"],
            index=1,
            horizontal=True,
        )
    with c3:
        segment_choice = st.radio(
            "Segment",
            ["Segment ID", "Segment type"],
            index=1,
            horizontal=True,
        )

    st.caption(
        "Example: Mission ID + Robot type + Segment type groups events by "
        "`activity`, the correlated Mission node ID, Robot node type, and "
        "Segment node type."
    )

    if st.button("Create aggregated graph", type="primary"):
        driver, error = neo4j_shared.get_neo4j_driver(uri, user, password)
        if driver is None:
            st.error(error)
            return
        try:
            st.session_state["agg_result"] = materialize_aggregation(
                driver,
                database,
                mission_choice,
                robot_choice,
                segment_choice,
            )
            st.session_state.pop("agg_rows", None)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not create the aggregated graph: {exc}")
        finally:
            driver.close()

    result = st.session_state.get("agg_result")
    if result:
        c1, c2, c3 = st.columns(3)
        c1.metric("Class nodes", result["classes"])
        c2.metric("Observed events", result["observed_events"])
        c3.metric("DF_C edges", result["dfc_edges"])

    st.subheader("Class DFG")
    c1, c2, c3 = st.columns(3)
    with c1:
        min_frequency = st.slider("Minimum frequency", 1, 100, 1)
    with c2:
        edge_limit = st.slider("Edge limit", 10, 500, 100, step=10)
    with c3:
        show_time = st.checkbox("Show average transition time", value=True)

    if st.button("Visualize class graph"):
        driver, error = neo4j_shared.get_neo4j_driver(uri, user, password)
        if driver is None:
            st.error(error)
            return
        try:
            st.session_state["agg_rows"] = fetch_class_graph(
                driver,
                database,
                min_frequency,
                edge_limit,
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not load the class graph: {exc}")
        finally:
            driver.close()

    rows = st.session_state.get("agg_rows", [])
    if rows:
        perspectives = sorted(
            {str(row.get("perspective") or "DF") for row in rows}
        )
        selected_perspectives = st.multiselect(
            "Visible perspectives",
            options=perspectives,
            default=perspectives,
            help="Hide or show DF_C edges by perspective without changing Neo4j.",
        )

        filtered_rows = [
            row
            for row in rows
            if str(row.get("perspective") or "DF") in selected_perspectives
        ]
        color_by_perspective = _perspective_colors(rows)
        visible_colors = {
            perspective: color_by_perspective[perspective]
            for perspective in selected_perspectives
            if perspective in color_by_perspective
        }

        st.caption(
            f"Showing {len(filtered_rows)} of {len(rows)} edges across "
            f"{len(selected_perspectives)} perspective(s)."
        )
        render_perspective_legend(visible_colors)

        if filtered_rows:
            st.graphviz_chart(
                build_graphviz(
                    filtered_rows,
                    show_time,
                    color_by_perspective,
                ),
                width="stretch",
            )
        else:
            st.info("Select at least one perspective to display its edges.")

        with st.expander("DF_C details"):
            st.dataframe(filtered_rows, width="stretch")
    else:
        st.info("Create the aggregation, then press Visualize class graph.")


def main() -> None:
    st.set_page_config(
        page_title="Personalized EKG Aggregation",
        page_icon="🕸️",
        layout="wide",
    )
    render_page()


if __name__ == "__main__":
    main()