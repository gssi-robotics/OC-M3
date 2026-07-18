from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st

import neo4j_shared


ACTIVITY_COLOR = "#8EC5FC"
NO_PERSPECTIVE = "<DF edges without perspective_type>"
ALL_OPTION = "All"
PERSPECTIVE_PALETTE = [
    "#D1495B",
    "#2E86AB",
    "#3C8D40",
    "#8E6C8A",
    "#F18F01",
    "#0B6E4F",
    "#6C5CE7",
    "#C44536",
]


def normalize_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): normalize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [normalize_value(item) for item in value]
    return str(value)


def format_seconds(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        numeric = float(value)
        if numeric.is_integer():
            return f"{int(numeric)}s"
        return f"{numeric:.2f}s"
    except Exception:  # noqa: BLE001
        return str(value)


def perspective_color_map(perspective_types: List[str]) -> Dict[str, str]:
    ordered = sorted(set(perspective_types))
    return {
        perspective: PERSPECTIVE_PALETTE[index % len(PERSPECTIVE_PALETTE)]
        for index, perspective in enumerate(ordered)
    }


def scaled_penwidth(value: Optional[float], minimum: float, maximum: float) -> str:
    if value is None:
        return "1.4"
    if maximum <= minimum:
        return "3.0"
    normalized = (float(value) - minimum) / (maximum - minimum)
    return f"{1.2 + normalized * 4.8:.2f}"


def fetch_schema(driver: Any, database: Optional[str]) -> Dict[str, Any]:
    with driver.session(**neo4j_shared.session_kwargs(database)) as session:
        labels = [
            record["label"]
            for record in session.run(
                """
                MATCH (n)
                UNWIND labels(n) AS label
                RETURN DISTINCT label
                ORDER BY label
                """
            )
        ]
        rel_types = [
            record["rel_type"]
            for record in session.run(
                """
                MATCH ()-[r]->()
                RETURN DISTINCT type(r) AS rel_type
                ORDER BY rel_type
                """
            )
        ]
        logs = [
            str(record["log"])
            for record in session.run(
                """
                MATCH (n)
                WHERE n.Log IS NOT NULL
                RETURN DISTINCT n.Log AS log
                ORDER BY log
                """
            )
            if record["log"] is not None
        ]
        df_property_keys = [
            record["key"]
            for record in session.run(
                """
                MATCH ()-[r:DF]->()
                UNWIND keys(r) AS key
                RETURN DISTINCT key
                ORDER BY key
                """
            )
        ]
        perspective_records = session.run(
            """
            MATCH ()-[r:DF]->()
            WITH coalesce(r.perspective_type, $no_perspective) AS perspective_type,
                 collect(DISTINCT r.perspective_id) AS raw_ids,
                 count(r) AS edge_count
            RETURN perspective_type, raw_ids, edge_count
            ORDER BY perspective_type
            """,
            no_perspective=NO_PERSPECTIVE,
        )
        df_perspectives: Dict[str, Dict[str, Any]] = {}
        for record in perspective_records:
            perspective_type = str(record["perspective_type"])
            ids = sorted([str(item) for item in record["raw_ids"] if item is not None])
            df_perspectives[perspective_type] = {
                "ids": ids,
                "edge_count": record["edge_count"],
            }

    return {
        "labels": labels,
        "relationship_types": rel_types,
        "logs": logs,
        "df_property_keys": df_property_keys,
        "df_perspectives": df_perspectives,
    }


def fetch_counts(driver: Any, database: Optional[str], log_name: Optional[str]) -> Dict[str, Any]:
    query = """
    MATCH (n)
    WHERE $log_name IS NULL OR n.Log = $log_name
    WITH count(n) AS nodes
    OPTIONAL MATCH (e1:Event)-[df:DF]->(e2:Event)
    WHERE $log_name IS NULL OR e1.Log = $log_name OR e2.Log = $log_name
    WITH nodes, count(df) AS df_edges
    OPTIONAL MATCH (e:Event)
    WHERE $log_name IS NULL OR e.Log = $log_name
    RETURN nodes, df_edges, count(e) AS events
    """
    with driver.session(**neo4j_shared.session_kwargs(database)) as session:
        record = session.run(query, log_name=log_name).single()
    return dict(record) if record else {"nodes": 0, "df_edges": 0, "events": 0}


def fetch_activity_dfg(
    driver: Any,
    database: Optional[str],
    log_name: Optional[str],
    perspective_type: Optional[str],
    perspective_id: Optional[str],
    min_frequency: int,
    edge_limit: int,
) -> List[Dict[str, Any]]:
    """Return an activity-level directly-follows graph aggregated from Event-DF-Event edges."""
    query = """
    MATCH (e1:Event)-[r:DF]->(e2:Event)
    WHERE ($log_name IS NULL OR e1.Log = $log_name OR e2.Log = $log_name)
      AND ($perspective_type IS NULL OR coalesce(r.perspective_type, $no_perspective) = $perspective_type)
      AND ($perspective_id IS NULL OR toString(r.perspective_id) = $perspective_id)
      AND e1.activity IS NOT NULL
      AND e2.activity IS NOT NULL
    WITH e1.activity AS from_activity,
         e2.activity AS to_activity,
         coalesce(r.perspective_type, $no_perspective) AS df_perspective_type,
         count(r) AS frequency,
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
    WHERE frequency >= $min_frequency
    RETURN from_activity, to_activity, df_perspective_type, frequency, avg_transition_seconds
    ORDER BY frequency DESC, df_perspective_type, from_activity, to_activity
    LIMIT $edge_limit
    """
    with driver.session(**neo4j_shared.session_kwargs(database)) as session:
        return [
            {
                "from_activity": record["from_activity"],
                "to_activity": record["to_activity"],
                "df_perspective_type": record["df_perspective_type"],
                "frequency": record["frequency"],
                "avg_transition_seconds": record["avg_transition_seconds"],
            }
            for record in session.run(
                query,
                log_name=log_name,
                perspective_type=perspective_type,
                perspective_id=perspective_id,
                min_frequency=min_frequency,
                edge_limit=edge_limit,
                no_perspective=NO_PERSPECTIVE,
            )
        ]


def build_activity_dfg_graphviz(
    rows: List[Dict[str, Any]],
    show_avg_transition: bool,
    thickness_metric: str,
) -> str:
    try:
        import graphviz
    except ImportError as exc:
        raise ImportError("Install the `graphviz` Python package to render the process view.") from exc

    dot = graphviz.Digraph("activity_dfg", graph_attr={"rankdir": "LR", "bgcolor": "white"})
    dot.attr(
        "node",
        style="filled",
        fontname="Helvetica",
        penwidth="1.2",
        shape="box",
        fillcolor=ACTIVITY_COLOR,
    )
    dot.attr("edge", fontname="Helvetica", color="#4A4A4A", arrowsize="0.8")

    activities = sorted({row["from_activity"] for row in rows} | {row["to_activity"] for row in rows})
    for activity in activities:
        dot.node(str(activity), label=str(activity), tooltip=f"Activity: {activity}")

    perspective_types = [str(row["df_perspective_type"]) for row in rows]
    color_by_perspective = perspective_color_map(perspective_types)
    metric_values = [
        float(row[thickness_metric])
        for row in rows
        if row.get(thickness_metric) is not None
    ] if thickness_metric != "none" else []
    minimum = min(metric_values) if metric_values else 0.0
    maximum = max(metric_values) if metric_values else 0.0

    for row in rows:
        label = f"n={row['frequency']}"
        if show_avg_transition:
            label += f"\navg={format_seconds(row.get('avg_transition_seconds'))}"
        perspective_type = str(row["df_perspective_type"])
        metric_value = None if thickness_metric == "none" else row.get(thickness_metric)
        dot.edge(
            str(row["from_activity"]),
            str(row["to_activity"]),
            label=label,
            color=color_by_perspective.get(perspective_type, "#4A4A4A"),
            fontcolor=color_by_perspective.get(perspective_type, "#4A4A4A"),
            penwidth=scaled_penwidth(metric_value, minimum, maximum),
            tooltip=(
                f"{row['from_activity']} -> {row['to_activity']}\n"
                f"perspective: {perspective_type}\n"
                f"frequency: {row['frequency']}\n"
                f"avg transition: {format_seconds(row.get('avg_transition_seconds'))}"
            ),
        )

    with dot.subgraph(name="cluster_legend") as legend:
        legend.attr(label="Perspective colors", color="#DDDDDD", fontsize="10")
        legend.attr("node", shape="plaintext", style="", fontname="Helvetica")
        for perspective_type, color in color_by_perspective.items():
            legend.node(
                f"legend_{perspective_type}",
                label=f'<<TABLE BORDER="0" CELLBORDER="0" CELLPADDING="2"><TR><TD><FONT COLOR="{color}">■</FONT></TD><TD>{perspective_type}</TD></TR></TABLE>>',
            )

    return dot.source


def render_activity_summary(rows: List[Dict[str, Any]]) -> None:
    activities = {row["from_activity"] for row in rows} | {row["to_activity"] for row in rows}
    total_frequency = sum(int(row["frequency"]) for row in rows)
    perspectives = sorted({str(row["df_perspective_type"]) for row in rows})
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Activities shown", len(activities))
    with c2:
        st.metric("DFG edges shown", len(rows))
    with c3:
        st.metric("Total DF frequency", total_frequency)
    st.caption("Perspectives shown: " + ", ".join(perspectives))


def clear_visualizer_state() -> None:
    for key in (
        "viz_connected",
        "viz_connection_error",
        "viz_schema",
        "viz_last_graph",
    ):
        st.session_state.pop(key, None)


def render_page() -> None:
    st.title("EKG Process View Visualizer")
    st.write(
        "Visualize the process-oriented activity directly-follows graph induced by "
        "Neo4j `(:Event)-[:DF]->(:Event)` relations. The graph aggregates concrete "
        "event-level DF edges by activity."
    )

    neo4j_shared.render_connection_summary()
    connection = neo4j_shared.get_connection_settings()
    uri = connection["uri"]
    user = connection["user"]
    password = connection["password"]
    database = connection["database"]
    c1, c2 = st.columns(2)
    with c1:
        connect_clicked = st.button("Connect", key="viz_connect_button")
    with c2:
        reset_clicked = st.button("Reset", key="viz_reset_button")

    if reset_clicked:
        clear_visualizer_state()

    if connect_clicked:
        driver, error = neo4j_shared.get_neo4j_driver(uri, user, password)
        if driver is None:
            st.session_state["viz_connected"] = False
            st.session_state["viz_connection_error"] = error
        else:
            try:
                st.session_state["viz_connected"] = True
                st.session_state["viz_connection_error"] = None
                st.session_state["viz_schema"] = fetch_schema(driver, database)
            except Exception as exc:  # noqa: BLE001
                st.session_state["viz_connected"] = False
                st.session_state["viz_connection_error"] = f"Could not inspect the graph schema: {exc}"
            finally:
                driver.close()

    if not st.session_state.get("viz_connected", False):
        error = st.session_state.get("viz_connection_error")
        if error:
            st.warning(error)
        st.info("Insert Neo4j credentials and press `Connect` to load graph filters.")
        return

    schema = st.session_state.get("viz_schema", {})

    with st.expander("Schema overview", expanded=False):
        st.json(
            {
                "labels": schema.get("labels", []),
                "relationship_types": schema.get("relationship_types", []),
                "df_properties": schema.get("df_property_keys", []),
                "df_perspectives": schema.get("df_perspectives", {}),
            }
        )

    logs = schema.get("logs", [])
    log_options = [ALL_OPTION] + logs
    selected_log = st.selectbox("Log filter", log_options, index=0)
    log_name = None if selected_log == ALL_OPTION else selected_log

    driver, error = neo4j_shared.get_neo4j_driver(uri, user, password)
    if driver is not None:
        try:
            counts = fetch_counts(driver, database, log_name)
            c1, c2, c3 = st.columns(3)
            c1.metric("Events", counts.get("events", 0))
            c2.metric("DF edges", counts.get("df_edges", 0))
            c3.metric("Nodes", counts.get("nodes", 0))
        finally:
            driver.close()

    if "DF" not in schema.get("relationship_types", []):
        st.warning(
            "No `DF` relationships were found. The process-oriented view is built from "
            "`(:Event)-[:DF]->(:Event)` edges, so derive/load DF edges first."
        )
        return

    perspective_options = schema.get("df_perspectives", {})
    perspective_types = [ALL_OPTION] + list(perspective_options.keys())

    st.subheader("Filters")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        selected_perspective_type = st.selectbox("DF perspective type", perspective_types, index=0)
    with c2:
        if selected_perspective_type == ALL_OPTION:
            perspective_id_options = [ALL_OPTION]
        else:
            perspective_id_options = [ALL_OPTION] + perspective_options.get(selected_perspective_type, {}).get("ids", [])
        selected_perspective_id = st.selectbox("DF perspective id", perspective_id_options, index=0)
    with c3:
        edge_limit = st.slider("Activity-edge limit", min_value=5, max_value=500, value=80, step=5)
    with c4:
        min_frequency = st.slider("Minimum frequency", min_value=1, max_value=50, value=1, step=1)

    c5, c6 = st.columns(2)
    with c5:
        show_avg_transition = st.checkbox("Show average transition time", value=True)
    with c6:
        thickness_metric = st.selectbox(
            "Edge thickness metric",
            [
                "none",
                "frequency",
                "avg_transition_seconds",
            ],
            index=1,
            format_func=lambda value: {
                "none": "Fixed width",
                "frequency": "Frequency",
                "avg_transition_seconds": "Performance / avg transition",
            }[value],
        )
    st.caption("Process-oriented view: activity DFG aggregated from `(:Event)-[:DF]->(:Event)` relations.")

    perspective_type_param = None if selected_perspective_type == ALL_OPTION else selected_perspective_type
    perspective_id_param = None if selected_perspective_id == ALL_OPTION else selected_perspective_id

    if st.button("Visualize process view", type="primary", key="visualize_process_view_button"):
        driver, error = neo4j_shared.get_neo4j_driver(uri, user, password)
        if driver is None:
            st.error(error)
            return
        try:
            rows = fetch_activity_dfg(
                driver=driver,
                database=database,
                log_name=log_name,
                perspective_type=perspective_type_param,
                perspective_id=perspective_id_param,
                min_frequency=min_frequency,
                edge_limit=edge_limit,
            )
            if not rows:
                st.info("No activity DFG edges matched the selected filters.")
                return
            dot_source = build_activity_dfg_graphviz(
                rows,
                show_avg_transition=show_avg_transition,
                thickness_metric=thickness_metric,
            )
            st.session_state["viz_last_graph"] = {
                "rows": rows,
                "dot_source": dot_source,
                "perspective_type": selected_perspective_type,
                "perspective_id": selected_perspective_id,
                "log": selected_log,
                "thickness_metric": thickness_metric,
            }
        except ImportError as exc:
            st.error(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not load or render the process view from Neo4j: {exc}")
            return
        finally:
            driver.close()

    graph_payload = st.session_state.get("viz_last_graph")
    if not graph_payload:
        st.info("Press `Visualize process view` to render the activity DFG.")
        return

    subtitle = "Process-oriented activity DFG"
    if graph_payload.get("log") and graph_payload.get("log") != ALL_OPTION:
        subtitle += f" | Log: {graph_payload.get('log')}"
    if graph_payload.get("perspective_type") and graph_payload.get("perspective_type") != ALL_OPTION:
        subtitle += f" | Perspective: {graph_payload.get('perspective_type')}"
    if graph_payload.get("perspective_id") and graph_payload.get("perspective_id") != ALL_OPTION:
        subtitle += f" / {graph_payload.get('perspective_id')}"
    if graph_payload.get("thickness_metric") and graph_payload.get("thickness_metric") != "none":
        subtitle += f" | Edge thickness: {graph_payload.get('thickness_metric')}"
    st.caption(subtitle)

    render_activity_summary(graph_payload["rows"])
    st.graphviz_chart(graph_payload["dot_source"], width="stretch")

    with st.expander("Activity DFG edge details", expanded=False):
        st.dataframe(graph_payload["rows"], width="stretch")


def main() -> None:
    st.set_page_config(page_title="EKG Process View Visualizer", page_icon="🕸️", layout="wide")
    render_page()


if __name__ == "__main__":
    main()
