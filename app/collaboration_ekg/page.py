from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import streamlit as st

import neo4j_shared
from collaboration.collaboration_utils import format_seconds, table_safe_rows

from .data import (
    OBJECTIVE_OPTIONS,
    PATTERN_OPTIONS,
    aggregate_occurrence_rows,
    fetch_base_graph,
    fetch_pattern_occurrence_rows,
    filter_occurrences_for_aggregate,
)
from .visuals import build_occurrence_lane_graph, build_overview_graph


HIDDEN_CLASS_PROPERTIES = {"Type", "Event_Id", "DisplayName", "Log", "sourceColumn"}


def clear_state() -> None:
    for key in (
        "cpi_connected",
        "cpi_error",
        "cpi_base",
        "cpi_summary",
        "cpi_occurrence_rows",
        "cpi_summary_signature",
        "cpi_occurrence_index",
        "cpi_selected_aggregate_key",
    ):
        st.session_state.pop(key, None)


def _summary_signature(pattern: str, objective_type: str, minimum_frequency: int) -> Dict[str, object]:
    return {
        "pattern": pattern,
        "objective_type": objective_type,
        "minimum_frequency": minimum_frequency,
    }


def _aggregate_key(row: Dict[str, object]) -> Tuple[str, str]:
    return (
        str(row["source_class_id"]),
        str(row["target_class_id"]),
    )


def _aggregate_label(row: Dict[str, object]) -> str:
    return (
        f"{row['source_activity']} -> {row['target_activity']} | "
        f"n={row['frequency']} | avg={format_seconds(row.get('avg_duration_seconds'))}"
    )


def _visible_class_items(details: Any, count: Any) -> List[Tuple[str, Any]]:
    if not isinstance(details, dict):
        return []
    items: List[Tuple[str, Any]] = []
    for key, value in details.items():
        if key in HIDDEN_CLASS_PROPERTIES or key == "activity":
            continue
        items.append((str(key), value))
    if count is not None:
        items.append(("Count", count))
    return items


def _render_class_detail_panel(title: str, activity: Any, details: Any, count: Any) -> None:
    st.markdown(f"**{title}**")
    rows = [{"property": "activity", "value": activity}]
    rows.extend({"property": key, "value": value} for key, value in _visible_class_items(details, count))
    safe_rows = table_safe_rows(rows)
    normalized_rows = [
        {
            "property": str(row.get("property", "")),
            "value": "" if row.get("value") is None else str(row.get("value")),
        }
        for row in safe_rows
    ]
    st.dataframe(normalized_rows, width="stretch", hide_index=True)


def _render_selected_aggregate_details(selected: Dict[str, object]) -> None:
    st.caption(f"Selected aggregate: {_aggregate_label(selected)}")
    c1, c2 = st.columns(2)
    with c1:
        _render_class_detail_panel(
            "Source class",
            selected.get("source_activity"),
            selected.get("source_details"),
            selected.get("source_count"),
        )
    with c2:
        _render_class_detail_panel(
            "Target class",
            selected.get("target_activity"),
            selected.get("target_details"),
            selected.get("target_count"),
        )


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
        driver, error = neo4j_shared.get_neo4j_driver(
            connection["uri"],
            connection["user"],
            connection["password"],
        )
        if driver is None:
            st.error(error)
            return None

        try:
            occurrence_rows = fetch_pattern_occurrence_rows(
                driver,
                connection["database"],
                pattern,
                objective_type,
            )
            st.session_state["cpi_occurrence_rows"] = occurrence_rows
            st.session_state["cpi_summary"] = aggregate_occurrence_rows(
                pattern,
                objective_type,
                occurrence_rows,
                pattern_min,
            )
            st.session_state["cpi_summary_signature"] = _summary_signature(pattern, objective_type, pattern_min)
            st.session_state.pop("cpi_occurrence_index", None)
            st.session_state.pop("cpi_selected_aggregate_key", None)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not derive pattern aggregates: {exc}")
        finally:
            driver.close()
    summary_signature = st.session_state.get("cpi_summary_signature")
    current_summary_signature = _summary_signature(pattern, objective_type, pattern_min)

    if summary_signature != current_summary_signature:
        st.session_state.pop("cpi_summary", None)
        st.session_state.pop("cpi_occurrence_rows", None)
        st.session_state.pop("cpi_occurrence_index", None)
        st.session_state.pop("cpi_selected_aggregate_key", None)

        st.info(
            "The pattern configuration changed. "
            "Press `Find pattern aggregates` to derive the matching aggregates."
        )
        return None
    summary = st.session_state.get("cpi_summary", [])
    if not summary:
        st.info("Choose a pattern and derive its aggregated occurrences.")
        return None

    display_rows = [
        {
            "index": index,
            "source class id": row["source_class_id"],
            "from": row["source_activity"],
            "target class id": row["target_class_id"],
            "to": row["target_activity"],
            "frequency": row["frequency"],
            "average performance": format_seconds(row.get("avg_duration_seconds")),
        }
        for index, row in enumerate(summary)
    ]
    st.dataframe(display_rows, width="stretch", hide_index=True)
    aggregate_keys = [_aggregate_key(row) for row in summary]

    selected_key = st.selectbox(
        "Selected aggregate",
        aggregate_keys,
        format_func=lambda key: next(_aggregate_label(row) for row in summary if _aggregate_key(row) == key),
        key="cpi_selected_aggregate_key",
    )
    selected = next(row for row in summary if _aggregate_key(row) == selected_key)
    _render_selected_aggregate_details(selected)
    return {
        "pattern": pattern,
        "objective_type": objective_type,
        "selected": selected,
    }


def _render_occurrences(connection: Dict[str, str], pattern: str, objective_type: str, selected: Dict[str, object]) -> None:
    st.subheader("3. Concrete occurrences")
    aggregate_frequency = int(selected.get("frequency") or 0)
    if aggregate_frequency <= 0:
        st.warning("The selected aggregate reports 0 occurrences, so no concrete event-level evidence can be loaded.")
        return

    if aggregate_frequency > 1:
        occurrence_limit = st.slider(
            "Occurrence limit",
            min_value=1,
            max_value=aggregate_frequency,
            value=aggregate_frequency,
            help="The maximum equals the number of occurrences in the selected aggregate.",
            key="cpi_occurrence_limit",
        )
    else:
        occurrence_limit = 1
        st.caption("Occurrence limit: 1 available occurrence for this aggregate.")

    st.info(
        f"The rows below are the {aggregate_frequency} event-level occurrence"
        f"{'' if aggregate_frequency == 1 else 's'} aggregated into this exact "
        "source-class to target-class pattern transition."
    )
    occurrence_rows = st.session_state.get("cpi_occurrence_rows", [])
    occurrences = filter_occurrences_for_aggregate(
        occurrence_rows,
        str(selected["source_class_id"]),
        str(selected["target_class_id"]),
        occurrence_limit,
    )

    if not occurrences:
        st.info("No event-level occurrences were found for the selected aggregate.")
        return

    if len(occurrences) != aggregate_frequency:
        st.warning(
            f"Aggregate reports {aggregate_frequency} occurrence"
            f"{'' if aggregate_frequency == 1 else 's'}, but the detail query returned {len(occurrences)}. "
            "Check summary/detail predicate consistency."
        )

    st.caption(
        f"Expected class pair: `{selected['source_class_id']}` -> "
        f"`{selected['target_class_id']}`"
    )

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
