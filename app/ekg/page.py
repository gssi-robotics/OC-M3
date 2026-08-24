from __future__ import annotations

from numbers import Number
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st

import neo4j_shared
from collaboration.collaboration_utils import format_seconds, table_safe_rows
from ekg.aggregation_data import fetch_class_durations, fetch_class_graph, materialize_aggregation
from ekg.aggregation_visuals import render_class_dfg_panel


def clear_state() -> None:
    for key in (
        "agg_connected",
        "agg_error",
        "agg_rows",
        "agg_result",
        "agg_class_durations",
    ):
        st.session_state.pop(key, None)


def _render_connection_gate() -> Optional[Dict[str, str]]:
    neo4j_shared.render_connection_summary()
    connection = neo4j_shared.get_connection_settings()
    uri = connection["uri"]
    user = connection["user"]
    password = connection["password"]
    database = connection["database"]

    connect_col, reset_col = st.columns(2)
    with connect_col:
        connect_clicked = st.button("Connect", key="ekg_connect_button")
    with reset_col:
        reset_clicked = st.button("Reset", key="ekg_reset_button")

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
        return None

    return {
        "uri": uri,
        "user": user,
        "password": password,
        "database": database,
    }


def _render_aggregation_controls(connection: Dict[str, str]) -> None:
    st.subheader("Aggregation")
    c1, c2, c3 = st.columns(3)
    with c1:
        mission_choice = st.radio("Mission", ["Mission ID", "Mission type"], horizontal=True)
    with c2:
        robot_choice = st.radio("Robot", ["Robot ID", "Robot type"], index=1, horizontal=True)
    with c3:
        segment_choice = st.radio("Segment", ["Segment ID", "Segment type"], index=1, horizontal=True)

    st.caption(
        "Example: Mission ID + Robot type + Segment type groups events by "
        "`activity`, the correlated Mission node ID, Robot node type, and "
        "Segment node type."
    )

    if st.button("Create aggregated graph", type="primary", key="ekg_create_agg_graph"):
        driver, error = neo4j_shared.get_neo4j_driver(
            connection["uri"],
            connection["user"],
            connection["password"],
        )
        if driver is None:
            st.error(error)
            return
        try:
            st.session_state["agg_result"] = materialize_aggregation(
                driver,
                connection["database"],
                mission_choice,
                robot_choice,
                segment_choice,
            )
            st.session_state["agg_class_durations"] = fetch_class_durations(
                driver,
                connection["database"],
                mission_choice,
                robot_choice,
                segment_choice,
            )
            st.session_state.pop("agg_rows", None)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not create the aggregated graph: {exc}")
        finally:
            driver.close()


def _render_aggregation_summary() -> None:
    result = st.session_state.get("agg_result")
    if not result:
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("Class nodes", result["classes"])
    c2.metric("Observed events", result["observed_events"])
    c3.metric("DF_C edges", result["dfc_edges"])

    duration_rows = st.session_state.get("agg_class_durations", [])
    if duration_rows:
        duration_columns = st.columns(3)
        for column, dimension in zip(duration_columns, ("mission", "robot", "segment")):
            duration, instances = _overall_entity_duration(duration_rows, dimension)
            with column:
                st.metric(
                    f"Avg {dimension.capitalize()} duration",
                    format_seconds(duration) or "n/a",
                )
                st.caption(f"Across {instances:,} represented {dimension} instances")

    st.markdown("#### Class entity durations")
    st.caption(
        "ID dimensions show the elapsed duration of the concrete entity. Type dimensions show the average "
        "elapsed duration across represented entity instances. Each duration spans the first correlated "
        "event start to the last correlated event end."
    )
    if duration_rows:
        st.dataframe(table_safe_rows(duration_rows), width="stretch", hide_index=True)
    else:
        st.info("Create the aggregated graph to calculate Class entity durations.")


def _overall_entity_duration(
    rows: List[Dict[str, Any]],
    dimension: str,
) -> Tuple[Optional[float], int]:
    """Average over unique groups without weighting repeated activity Classes."""
    groups: Dict[str, Tuple[float, int]] = {}
    for row in rows:
        group = str(row.get(f"{dimension}_group") or "")
        duration = row.get(f"{dimension}_duration_seconds")
        instances = row.get(f"{dimension}_instances")
        if not group or not isinstance(duration, Number):
            continue
        instance_count = int(instances) if isinstance(instances, Number) else 1
        groups[group] = (float(duration), max(1, instance_count))

    represented_instances = sum(instance_count for _, instance_count in groups.values())
    if represented_instances == 0:
        return None, 0
    weighted_duration = sum(
        duration * instance_count
        for duration, instance_count in groups.values()
    )
    return weighted_duration / represented_instances, represented_instances


def _render_class_dfg_controls(connection: Dict[str, str]) -> None:
    st.subheader("Class DFG")
    c1, c2, c3 = st.columns(3)
    with c1:
        min_frequency = st.slider("Minimum frequency", 1, 100, 1, key="ekg_min_frequency")
    with c2:
        edge_limit = st.slider("Edge limit", 10, 500, 100, step=10, key="ekg_edge_limit")
    with c3:
        show_time = st.checkbox("Show average transition time", value=True, key="ekg_show_avg_transition")

    if st.button("Visualize class graph", key="ekg_visualize_class_graph"):
        driver, error = neo4j_shared.get_neo4j_driver(
            connection["uri"],
            connection["user"],
            connection["password"],
        )
        if driver is None:
            st.error(error)
            return
        try:
            st.session_state["agg_rows"] = fetch_class_graph(
                driver,
                connection["database"],
                min_frequency,
                edge_limit,
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not load the class graph: {exc}")
        finally:
            driver.close()

    _render_class_dfg(show_time)


def _render_class_dfg(show_time: bool) -> None:
    rows = st.session_state.get("agg_rows", [])
    render_class_dfg_panel(
        rows,
        show_time=show_time,
        key_prefix="ekg",
        empty_message="Create the aggregation, then press Visualize class graph.",
    )


def render_page() -> None:
    st.title("Personalized EKG Aggregation")
    st.write(
        "Choose the Mission, Robot, and Segment granularity. The application "
        "creates `:Class` nodes, `:OBS` relationships, and aggregated "
        "`:DF_C` relationships."
    )

    connection = _render_connection_gate()
    if connection is None:
        return

    _render_aggregation_controls(connection)
    _render_aggregation_summary()
    _render_class_dfg_controls(connection)
