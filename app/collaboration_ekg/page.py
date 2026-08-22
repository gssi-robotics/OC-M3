from __future__ import annotations

from collections import Counter
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st

import neo4j_shared
from collaboration.collaboration_utils import format_seconds, table_safe_rows
from collaboration.collaboration_visuals import render_dashboard_cards, render_rank_bars
from ekg.aggregation_visuals import render_class_dfg_panel

from .data import (
    OBJECTIVE_OPTIONS,
    PATTERN_OPTIONS,
    aggregate_occurrence_rows,
    aggregate_occurrence_rows_by_patterns,
    combine_pattern_occurrence_rows,
    fetch_base_graph,
    fetch_pattern_occurrence_rows,
    filter_occurrences_for_aggregate,
    summary_rows_to_graph_rows,
)
from .visuals import build_occurrence_lane_graph


HIDDEN_CLASS_PROPERTIES = {"Type", "Event_Id", "DisplayName", "Log", "sourceColumn"}
PROJECTION_OPTIONS = ["Base class DFG", "Pattern projection"]


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


def _summary_signature(patterns: List[str], objective_type: str, minimum_frequency: int) -> Dict[str, object]:
    return {
        "patterns": tuple(sorted(patterns)),
        "objective_type": objective_type,
        "minimum_frequency": minimum_frequency,
    }


def _aggregate_key(row: Dict[str, object]) -> Tuple[str, str, str]:
    return (
        str(row["pattern"]),
        str(row["source_class_id"]),
        str(row["target_class_id"]),
    )


def _aggregate_label(row: Dict[str, object]) -> str:
    return (
        f"[{row['pattern']}] {row['source_activity']} -> {row['target_activity']} | "
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
    st.caption(f"Pattern: `{selected.get('pattern')}` | Objective perspective: `{selected.get('objective_type')}`")
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


def _duration_values(rows: List[Dict[str, Any]]) -> List[float]:
    values: List[float] = []
    for row in rows:
        value = row.get("duration_seconds")
        if isinstance(value, (int, float)):
            values.append(float(value))
    return values


def _pair_label(left: Any, right: Any) -> str:
    return f"{left or '?'} -> {right or '?'}"


def _top_counter_rows(counter: Counter[str], label_key: str, value_key: str, limit: int = 8) -> List[Dict[str, Any]]:
    return [
        {label_key: label, value_key: count}
        for label, count in counter.most_common(limit)
    ]


def _render_occurrence_perspective_summary(pattern: str, occurrences: List[Dict[str, Any]]) -> None:
    st.markdown("**Pattern Summary**")
    durations = _duration_values(occurrences)
    robot_pairs: Counter[str] = Counter()
    segment_pairs: Counter[str] = Counter()
    objective_pairs: Counter[str] = Counter()
    capabilities: Counter[str] = Counter()
    teams: Counter[str] = Counter()

    for row in occurrences:
        if pattern == "Robot handover":
            robot_pairs[_pair_label(row.get("from_robot"), row.get("to_robot"))] += 1
            segment_pairs[_pair_label(row.get("from_segment"), row.get("to_segment"))] += 1
        elif pattern == "Objective switch":
            objective_pairs[_pair_label(row.get("from_objective"), row.get("to_objective"))] += 1
            robot_pairs[str(row.get("robot_id") or "?")] += 1
        elif pattern == "Capability-driven return":
            robot_pairs[_pair_label(row.get("returning_robot"), row.get("intermediate_robot"))] += 1
            for capability in row.get("capabilities") or ["?"]:
                capabilities[str(capability)] += 1
        elif pattern == "Parallel collaboration":
            objective_pairs[_pair_label(row.get("left_objective"), row.get("right_objective"))] += 1
            teams[" | ".join(str(item) for item in (row.get("shared_robots") or [])) or "disjoint teams"] += 1

    cards = [
        {
            "label": "Occurrences",
            "value": str(len(occurrences)),
            "caption": "Event-level support for the selected aggregate",
            "accent": "#2563EB",
        },
        {
            "label": "Median time",
            "value": format_seconds(median(durations)) if durations else "n/a",
            "caption": "Median duration/transition across occurrences",
            "accent": "#059669",
        },
        {
            "label": "Avg time",
            "value": format_seconds(sum(durations) / len(durations)) if durations else "n/a",
            "caption": "Average duration/transition across occurrences",
            "accent": "#D97706",
        },
        {
            "label": "Max time",
            "value": format_seconds(max(durations)) if durations else "n/a",
            "caption": "Longest observed duration/transition",
            "accent": "#DC2626",
        },
    ]
    render_dashboard_cards(cards)

    chart_rows: List[Tuple[str, List[Dict[str, Any]], str]] = []
    if robot_pairs:
        chart_rows.append(("Robot perspective", _top_counter_rows(robot_pairs, "label", "count"), "#B22222"))
    if segment_pairs:
        chart_rows.append(("Segment perspective", _top_counter_rows(segment_pairs, "label", "count"), "#7C3AED"))
    if objective_pairs:
        chart_rows.append(("Objective perspective", _top_counter_rows(objective_pairs, "label", "count"), "#2563EB"))
    if capabilities:
        chart_rows.append(("Capability perspective", _top_counter_rows(capabilities, "label", "count"), "#264653"))
    if teams:
        chart_rows.append(("Team perspective", _top_counter_rows(teams, "label", "count"), "#0F766E"))

    if chart_rows:
        cols = st.columns(len(chart_rows))
        for column, (title, rows, color) in zip(cols, chart_rows):
            with column:
                render_rank_bars(title, rows, "label", "count", color=color)


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


def _render_projection_mode() -> str:
    return st.radio(
        "Projection mode",
        PROJECTION_OPTIONS,
        index=0,
        horizontal=True,
        key="cpi_projection_mode",
        help="Switch between the generic Class/DF_C view and a pattern-induced class projection.",
    )


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
    render_class_dfg_panel(
        base_rows,
        show_time=True,
        key_prefix="cpi_base",
        empty_message="Load the materialized Class/DF_C graph first.",
    )


def _render_pattern_projection_overview(
    summary_rows: List[Dict[str, Any]],
) -> None:
    st.subheader("1. Pattern Projection Overview")
    st.caption(
        "This projection keeps only the selected collaboration-pattern edges and aggregates "
        "their event-level support into a class-to-class graph."
    )
    render_class_dfg_panel(
        summary_rows_to_graph_rows(summary_rows),
        show_time=True,
        key_prefix="cpi_pattern_projection",
        empty_message="Derive pattern aggregates to visualize the pattern-induced class graph.",
        detail_expander_label="Pattern-edge details",
    )


def _render_pattern_selection(connection: Dict[str, str]) -> Optional[Dict[str, object]]:
    st.subheader("2. Select a collaboration pattern")
    p1, p2, p3 = st.columns(3)
    with p1:
        selected_patterns = st.multiselect(
            "Patterns",
            PATTERN_OPTIONS,
            default=[PATTERN_OPTIONS[0]] if PATTERN_OPTIONS else [],
            key="cpi_patterns",
        )
    with p2:
        objective_type = st.selectbox("Objective perspective", OBJECTIVE_OPTIONS, key="cpi_objective_type")
    with p3:
        pattern_min = st.slider("Minimum pattern frequency", 1, 100, 1, key="cpi_pattern_min")

    if not selected_patterns:
        st.info("Select at least one pattern to derive a pattern projection.")
        return None

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
            rows_by_pattern = {
                pattern: fetch_pattern_occurrence_rows(
                    driver,
                    connection["database"],
                    pattern,
                    objective_type,
                )
                for pattern in selected_patterns
            }
            st.session_state["cpi_occurrence_rows"] = combine_pattern_occurrence_rows(rows_by_pattern)
            st.session_state["cpi_summary"] = aggregate_occurrence_rows_by_patterns(
                rows_by_pattern,
                objective_type,
                pattern_min,
            )
            st.session_state["cpi_summary_signature"] = _summary_signature(selected_patterns, objective_type, pattern_min)
            st.session_state.pop("cpi_occurrence_index", None)
            st.session_state.pop("cpi_selected_aggregate_key", None)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not derive pattern aggregates: {exc}")
        finally:
            driver.close()
    summary_signature = st.session_state.get("cpi_summary_signature")
    current_summary_signature = _summary_signature(selected_patterns, objective_type, pattern_min)

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
        "patterns": selected_patterns,
        "objective_type": objective_type,
        "summary_rows": summary,
        "selected": selected,
    }


def _render_occurrences(connection: Dict[str, str], objective_type: str, selected: Dict[str, object]) -> None:
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
        str(selected["pattern"]),
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

    _render_occurrence_perspective_summary(str(selected["pattern"]), occurrences)
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
        build_occurrence_lane_graph(str(selected["pattern"]), occurrences[occurrence_index], occurrence_index),
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

    projection_mode = _render_projection_mode()
    if projection_mode == "Base class DFG":
        _render_overview(connection)

    selected_payload = _render_pattern_selection(connection)
    if not selected_payload:
        return

    if projection_mode == "Pattern projection":
        _render_pattern_projection_overview(list(selected_payload["summary_rows"]))

    _render_occurrences(
        connection,
        str(selected_payload["objective_type"]),
        dict(selected_payload["selected"]),
    )
