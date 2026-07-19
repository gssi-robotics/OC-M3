from __future__ import annotations

from typing import Dict, Optional

import streamlit as st

import neo4j_shared
from collaboration.collaboration_utils import format_seconds, table_safe_rows

from .data import (
    OBJECTIVE_OPTIONS,
    PATTERN_OPTIONS,
    fetch_base_graph,
    fetch_occurrences,
    fetch_pattern_summary,
)
from .visuals import build_occurrence_lane_graph, build_overview_graph


def clear_state() -> None:
    for key in ("cpi_connected", "cpi_error", "cpi_base", "cpi_summary", "cpi_occurrences"):
        st.session_state.pop(key, None)


def _render_connection_gate() -> Optional[Dict[str, str]]:
    neo4j_shared.render_connection_summary()
    connection = neo4j_shared.get_connection_settings()
    uri = connection["uri"]
    user = connection["user"]
    password = connection["password"]
    database = connection["database"]

    c1, c2 = st.columns(2)
    with c1:
        connect = st.button("Connect", key="cpi_connect")
    with c2:
        reset = st.button("Reset", key="cpi_reset")

    if reset:
        clear_state()

    if connect:
        driver, error = neo4j_shared.get_neo4j_driver(uri, user, password)
        if driver is None:
            st.session_state["cpi_connected"] = False
            st.session_state["cpi_error"] = error
        else:
            st.session_state["cpi_connected"] = True
            st.session_state["cpi_error"] = None
            driver.close()

    if not st.session_state.get("cpi_connected", False):
        if st.session_state.get("cpi_error"):
            st.error(st.session_state["cpi_error"])
        st.info("Insert Neo4j settings and press Connect.")
        return None

    return {
        "uri": uri,
        "user": user,
        "password": password,
        "database": database,
    }


def _render_overview(connection: Dict[str, str]) -> None:
    st.subheader("1. Aggregated process overview")
    a, b = st.columns(2)
    with a:
        base_min = st.slider("Minimum DF_C frequency", 1, 100, 1, key="cpi_base_min")
    with b:
        base_limit = st.slider("DF_C edge limit", 10, 500, 120, 10, key="cpi_base_limit")
    if st.button("Load aggregated DFG", key="cpi_load_aggregated_dfg"):
        driver, error = neo4j_shared.get_neo4j_driver(connection["uri"], connection["user"], connection["password"])
        if driver is None:
            st.error(error)
            return
        try:
            st.session_state["cpi_base"] = fetch_base_graph(driver, connection["database"], base_min, base_limit)
        finally:
            driver.close()

    base_rows = st.session_state.get("cpi_base", [])
    if base_rows:
        st.graphviz_chart(build_overview_graph(base_rows), width="stretch")
    else:
        st.info("Load the materialized Class/DF_C graph first.")


def _render_pattern_selection(connection: Dict[str, str]) -> Optional[Dict[str, object]]:
    st.subheader("2. Select a collaboration pattern")
    p1, p2, p3 = st.columns(3)
    with p1:
        pattern = st.selectbox("Pattern", PATTERN_OPTIONS, key="cpi_pattern")
    with p2:
        objective_type = st.selectbox("Objective perspective", OBJECTIVE_OPTIONS, key="cpi_objective_type")
    with p3:
        pattern_min = st.slider("Minimum pattern frequency", 1, 100, 1, key="cpi_pattern_min")

    if st.button("Find pattern aggregates", type="primary", key="cpi_find_pattern_aggregates"):
        driver, error = neo4j_shared.get_neo4j_driver(connection["uri"], connection["user"], connection["password"])
        if driver is None:
            st.error(error)
            return None
        try:
            st.session_state["cpi_summary"] = fetch_pattern_summary(
                driver,
                connection["database"],
                pattern,
                objective_type,
                pattern_min,
            )
            st.session_state.pop("cpi_occurrences", None)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not derive pattern aggregates: {exc}")
        finally:
            driver.close()

    summary = st.session_state.get("cpi_summary", [])
    if not summary:
        st.info("Choose a pattern and derive its aggregated occurrences.")
        return None

    display_rows = [
        {
            "index": index,
            "from": row["source_activity"],
            "to": row["target_activity"],
            "frequency": row["frequency"],
            "average performance": format_seconds(row.get("avg_seconds")),
        }
        for index, row in enumerate(summary)
    ]
    st.dataframe(display_rows, width="stretch", hide_index=True)
    selection = st.selectbox(
        "Selected aggregate",
        list(range(len(summary))),
        format_func=lambda i: (
            f"{summary[i]['source_activity']} -> {summary[i]['target_activity']} | "
            f"n={summary[i]['frequency']} | avg={format_seconds(summary[i].get('avg_seconds'))}"
        ),
        key="cpi_selected_aggregate",
    )
    return {
        "pattern": pattern,
        "objective_type": objective_type,
        "selected": summary[selection],
    }


def _render_occurrences(connection: Dict[str, str], pattern: str, objective_type: str, selected: Dict[str, object]) -> None:
    st.subheader("3. Concrete occurrences")
    max_occurrences = max(1, int(selected.get("frequency") or 1))
    occurrence_limit = st.slider(
        "Occurrence limit",
        min_value=1,
        max_value=max_occurrences,
        value=max_occurrences,
        help="The maximum equals the number of occurrences in the selected aggregate.",
        key="cpi_occurrence_limit",
    )
    if st.button("Load concrete occurrences", key="cpi_load_concrete_occurrences"):
        driver, error = neo4j_shared.get_neo4j_driver(connection["uri"], connection["user"], connection["password"])
        if driver is None:
            st.error(error)
            return
        try:
            st.session_state["cpi_occurrences"] = fetch_occurrences(
                driver,
                connection["database"],
                pattern,
                objective_type,
                str(selected["source_id"]),
                str(selected["target_id"]),
                occurrence_limit,
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not load occurrences: {exc}")
        finally:
            driver.close()

    occurrences = st.session_state.get("cpi_occurrences", [])
    if not occurrences:
        st.info("Load the event-level evidence for the selected aggregate.")
        return

    st.dataframe(table_safe_rows(occurrences), width="stretch", hide_index=True)
    occurrence_index = st.selectbox(
        "Occurrence to decompose",
        list(range(len(occurrences))),
        format_func=lambda i: f"Occurrence {i + 1}",
        key="cpi_occurrence_index",
    )
    st.subheader("4. Collaboration decomposition")
    st.caption("This lower view explains one concrete occurrence. It is generated in Streamlit and is not materialized in Neo4j.")
    st.graphviz_chart(
        build_occurrence_lane_graph(pattern, occurrences[occurrence_index], occurrence_index),
        width="stretch",
    )

    with st.expander("Selected occurrence details", expanded=False):
        st.json(occurrences[occurrence_index])


def render_page() -> None:
    st.title("Collaboration Pattern Inspector")
    st.write("Inspect collaboration patterns through an aggregated DFG and an event-level decomposition of one selected pattern transition.")
    connection = _render_connection_gate()
    if connection is None:
        return

    _render_overview(connection)
    selected_payload = _render_pattern_selection(connection)
    if not selected_payload:
        return

    _render_occurrences(
        connection,
        str(selected_payload["pattern"]),
        str(selected_payload["objective_type"]),
        dict(selected_payload["selected"]),
    )
