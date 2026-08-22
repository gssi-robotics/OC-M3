from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Dict, List, Optional

import streamlit as st

import neo4j_shared
from collaboration.collaboration_data import (
    build_pattern_catalog,
    diagnostic_count,
    fetch_logs,
    fetch_mission_ids,
    load_collaboration_patterns_module,
    occurrence_count_from_rows,
    run_pattern_query,
)
from collaboration.collaboration_utils import (
    ALL_OPTION,
    candidate_numeric_keys,
    humanize_name,
    pattern_family,
    preferred_timeline_metric,
    table_safe_rows,
)
from collaboration.collaboration_visuals import (
    render_dashboard_cards,
    render_eventdrops_like_timeline,
    render_paper_scope_panel,
    render_pattern_interpretation_table,
    render_rank_bars,
)
from collaboration.timeline_views import render_timeline_tab
from collaboration.evaluation_views import (
    render_evaluation_workspace,
    render_occurrence_explainability,
)
from collaboration.analysis_views import (
    render_capability_diagnostics_tab,
    render_object_centric_pairwise_tab,
    render_process_maps_tab,
)


def render_dashboard(driver: Any, database: Optional[str], catalog: Dict[str, Dict[str, str]], log_name: Optional[str]) -> None:
    st.subheader("Collaboration Metrics Dashboard")
    st.caption("Counts are derived from EKG pattern occurrence queries; diagnostics connect them to time, teaming, capability pressure, and synchronization.")

    if st.button("Refresh dashboard", type="primary", key="collab_refresh_dashboard"):
        occurrence_metrics: Dict[str, int] = {}
        diagnostic_tables: Dict[str, List[Dict[str, Any]]] = {}
        for name, query in catalog["Occurrences"].items():
            occurrence_metrics[name] = occurrence_count_from_rows(driver, database, query, log_name)
        for name, query in catalog["Diagnostics"].items():
            diagnostic_tables[name] = run_pattern_query(driver, database, query, log_name)
        st.session_state["collab_dashboard"] = {
            "occurrence_metrics": occurrence_metrics,
            "diagnostic_tables": diagnostic_tables,
            "log_name": log_name or ALL_OPTION,
        }

    payload = st.session_state.get("collab_dashboard")
    if not payload:
        st.info("Press `Refresh dashboard` to compute collaboration metrics.")
        return

    occurrence_metrics = payload["occurrence_metrics"]
    family_counts: Dict[str, int] = defaultdict(int)
    for pattern, count in occurrence_metrics.items():
        family_counts[pattern_family(pattern)] += count

    render_dashboard_cards([
        {"label": "Coordination intensity", "value": str(family_counts.get("Coordination intensity", 0)), "caption": "Handovers and work transfers", "accent": "#DC2626"},
        {"label": "Allocation dynamics", "value": str(family_counts.get("Allocation dynamics", 0)), "caption": "Robot objective switches", "accent": "#2563EB"},
        {"label": "Capability pressure", "value": str(family_counts.get("Capability pressure", 0)), "caption": "Capability-driven returns", "accent": "#7C3AED"},
        {"label": "Parallelism", "value": str(family_counts.get("Parallelism and synchronization", 0)), "caption": "Parallel mission/segment structures", "accent": "#059669"},
    ])

    st.markdown("#### Pattern occurrence views")
    c1, c2 = st.columns([1.35, 1])
    with c1:
        render_pattern_interpretation_table(occurrence_metrics)
    with c2:
        metric_rows = [{"pattern": name, "occurrences": count} for name, count in sorted(occurrence_metrics.items(), key=lambda item: -item[1])]
        render_rank_bars("Most frequent collaboration views", metric_rows, "pattern", "occurrences", color="#1D4ED8")

    st.markdown("#### Diagnostic views")
    for name, rows in payload["diagnostic_tables"].items():
        with st.expander(humanize_name(name), expanded=False):
            agg = diagnostic_count(rows, ["count", "capReturnCount", "robotCompetition", "teamSize"])
            if agg is not None:
                st.caption(f"Aggregate diagnostic value: {agg:g}")
            metric_key = preferred_timeline_metric(rows)
            if metric_key:
                render_eventdrops_like_timeline(f"{humanize_name(name)} by {metric_key}", rows, metric_key)
            if rows:
                st.dataframe(table_safe_rows(rows), width="stretch", hide_index=True)
            else:
                st.info("No rows returned.")


def build_explorer_query_options(catalog: Dict[str, Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    options: Dict[str, Dict[str, str]] = {}
    for category in ("Occurrences", "Diagnostics"):
        for query_name in catalog.get(category, {}):
            label = f"[{category[:-1]}] {humanize_name(query_name)}"
            options[label] = {"category": category, "query_name": query_name}
    return options


def render_explorer(driver: Any, database: Optional[str], catalog: Dict[str, Dict[str, str]], log_name: Optional[str]) -> None:
    st.subheader("Pattern Explorer")
    st.caption("Inspect individual EKG matches or diagnostic rows. The event-level mission timeline is in the separate `Mission Timeline` tab.")

    labeled_options = build_explorer_query_options(catalog)
    option_labels = list(labeled_options.keys())
    default_label = st.session_state.get("collab_explorer_query_label")
    default_index = option_labels.index(default_label) if default_label in option_labels else 0
    selected_label = st.selectbox("Pattern query", option_labels, index=default_index, key="collab_query_to_inspect")
    selected_option = labeled_options[selected_label]
    category = selected_option["category"]
    query_name = selected_option["query_name"]
    query = catalog[category][query_name]

    st.session_state["collab_explorer_query_label"] = selected_label
    st.session_state["collab_explorer_query_category"] = category

    with st.expander("Cypher query", expanded=False):
        st.code(query, language="cypher")

    row_limit = st.slider("Explorer row limit", min_value=10, max_value=1000, value=150, step=10, key="collab_row_limit")

    if st.button("Run selected pattern", key="collab_run_pattern"):
        rows = run_pattern_query(driver, database, query, log_name, row_limit)
        st.session_state["collab_explorer_rows"] = rows
        st.session_state["collab_explorer_query"] = query_name
        st.session_state["collab_explorer_query_label"] = selected_label
        st.session_state["collab_explorer_query_category"] = category

    rows = st.session_state.get("collab_explorer_rows")
    if rows is None:
        st.info("Run a pattern query to inspect its occurrences or diagnostics.")
        return

    st.caption(f"Results for `{st.session_state.get('collab_explorer_query', query_name)}`")
    if not rows:
        st.info("The selected query returned no rows.")
        return

    numeric_keys = candidate_numeric_keys(rows)
    render_dashboard_cards([
        {"label": "Returned rows", "value": str(len(rows)), "caption": "Occurrences or diagnostic rows", "accent": "#2563EB"},
        {"label": "Numeric metrics", "value": str(len(numeric_keys)), "caption": "Available for previews", "accent": "#059669"},
        {"label": "Query type", "value": category, "caption": "Occurrence or diagnostic", "accent": "#D97706"},
    ])

    if numeric_keys:
        with st.expander("Metric preview", expanded=False):
            preferred = preferred_timeline_metric(rows)
            index = numeric_keys.index(preferred) if preferred in numeric_keys else 0
            selected_metric = st.selectbox("Metric", numeric_keys, index=index, key="collab_explorer_metric_preview")
            render_eventdrops_like_timeline(f"{humanize_name(st.session_state.get('collab_explorer_query', query_name))} by {selected_metric}", rows, selected_metric)

    st.dataframe(table_safe_rows(rows), width="stretch", hide_index=True)
    st.download_button(
        "Download results JSON",
        data=json.dumps(rows, ensure_ascii=True, indent=2),
        file_name=f"{st.session_state.get('collab_explorer_query', query_name)}.json",
        mime="application/json",
        key="collab_download_results",
    )


def clear_state() -> None:
    for key in (
        "collab_connected",
        "collab_connection_error",
        "collab_logs",
        "collab_dashboard",
        "collab_explorer_rows",
        "collab_explorer_query",
        "collab_explorer_query_label",
        "collab_explorer_query_category",
        "collab_query_to_inspect",
        "collab_mission_ids",
        "evaluation_payload",
        "evaluation_signature",
        "evaluation_logs",
        "explain_rows",
        "explain_signature",
        "explain_occurrence_index",
    ):
        st.session_state.pop(key, None)


def render_page() -> None:
    st.title("Collaboration Evaluation")
    st.write(
        "Compare multi-robot allocation strategies through collaboration structures and indicators, "
        "then inspect the Control events that explain individual Task transitions."
    )

    neo4j_shared.render_connection_summary()
    connection = neo4j_shared.get_connection_settings()
    uri = connection["uri"]
    user = connection["user"]
    password = connection["password"]
    database = connection.get("database", "")

    c1, c2 = st.columns(2)
    with c1:
        connect_clicked = st.button("Connect", key="collab_connect_button")
    with c2:
        reset_clicked = st.button("Reset", key="collab_reset_button")

    if reset_clicked:
        clear_state()

    if connect_clicked:
        driver, error = neo4j_shared.get_neo4j_driver(uri, user, password)
        if driver is None:
            st.session_state["collab_connected"] = False
            st.session_state["collab_connection_error"] = error
        else:
            try:
                st.session_state["collab_logs"] = fetch_logs(driver, database)
                st.session_state["collab_mission_ids"] = fetch_mission_ids(driver, database, None)
                st.session_state["collab_connected"] = True
                st.session_state["collab_connection_error"] = None
            except Exception as exc:  # noqa: BLE001
                st.session_state["collab_connected"] = False
                st.session_state["collab_connection_error"] = f"Could not inspect Neo4j: {exc}"
            finally:
                driver.close()

    if not st.session_state.get("collab_connected", False):
        error = st.session_state.get("collab_connection_error")
        if error:
            st.warning(error)
        st.info("Insert Neo4j credentials and press `Connect` to load the collaboration dashboard.")
        return

    try:
        patterns_module = load_collaboration_patterns_module()
        factory = patterns_module.CollaborationPatternCypher()
        catalog = build_pattern_catalog(factory)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not load collaboration pattern queries: {exc}")
        return

    logs = st.session_state.get("collab_logs", [])
    if not logs:
        st.warning("No EKG logs were found. Load at least one graph before running the evaluation.")
        return

    driver, error = neo4j_shared.get_neo4j_driver(uri, user, password)
    if driver is None:
        st.error(error)
        return

    try:
        tab_evaluation, tab_timeline, tab_explain = st.tabs(
            ["Evaluation Export", "Collaboration Timeline", "Explain an Occurrence"]
        )
        with tab_evaluation:
            render_evaluation_workspace(driver, database, catalog, logs)
        with tab_timeline:
            selected_log = st.selectbox(
                "Strategy / log",
                logs,
                key="timeline_log_filter",
            )
            render_timeline_tab(driver, database, catalog, selected_log)
        with tab_explain:
            render_occurrence_explainability(driver, database, catalog, logs)
    finally:
        driver.close()


def main() -> None:
    st.set_page_config(page_title="Object-Centric Collaboration Mining", page_icon="🤖", layout="wide")
    render_page()


if __name__ == "__main__":
    main()
