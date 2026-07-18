from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st

import neo4j_shared
from collaboration.collaboration_data import (
    build_pattern_catalog,
    build_timeline_summary,
    diagnostic_count,
    extract_multi_pattern_transitions,
    extract_structural_highlights,
    fetch_logs,
    fetch_mission_events,
    fetch_mission_ids,
    load_collaboration_patterns_module,
    mission_span,
    occurrence_count_from_rows,
    prepare_events_for_timeline,
    relative_seconds,
    run_pattern_query,
    segment_extents,
)
from collaboration.collaboration_utils import (
    ALL_OPTION,
    candidate_numeric_keys,
    compact_metric_label,
    format_seconds,
    humanize_name,
    node_id,
    normalize_value,
    pattern_base_name,
    pattern_color_map,
    pattern_edge_hover_html,
    pattern_family,
    pattern_short,
    preferred_timeline_metric,
    segment_color_map,
    table_safe_rows,
)
from collaboration.collaboration_visuals import (
    render_dashboard_cards,
    render_eventdrops_like_timeline,
    render_paper_scope_panel,
    render_pattern_interpretation_table,
    render_rank_bars,
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


def render_explorer(driver: Any, database: Optional[str], catalog: Dict[str, Dict[str, str]], log_name: Optional[str]) -> None:
    st.subheader("Pattern Explorer")
    st.caption("Inspect individual EKG matches or diagnostic rows. The event-level mission timeline is in the separate `Mission Timeline` tab.")

    c1, c2 = st.columns(2)
    with c1:
        category = st.selectbox("Query category", list(catalog.keys()), key="collab_query_category")
    with c2:
        query_name = st.selectbox("Pattern query", list(catalog[category].keys()), key="collab_query_name")

    query = catalog[category][query_name]
    with st.expander("Cypher query", expanded=False):
        st.code(query, language="cypher")

    row_limit = st.slider("Explorer row limit", min_value=10, max_value=1000, value=150, step=10, key="collab_row_limit")

    if st.button("Run selected pattern", key="collab_run_pattern"):
        rows = run_pattern_query(driver, database, query, log_name, row_limit)
        st.session_state["collab_explorer_rows"] = rows
        st.session_state["collab_explorer_query"] = query_name

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


def _plotly_required() -> Tuple[Any, Any]:
    try:
        import plotly.graph_objects as go
        import plotly.io as pio
    except ImportError as exc:  # pragma: no cover
        raise ImportError("Install Plotly with `pip install plotly` to use the interactive mission timeline.") from exc
    return go, pio


def _event_lookup(events: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(event["event_id"]): event for event in events}


def _compact_row_json(row: Dict[str, Any], max_len: int = 1200) -> str:
    normalized = normalize_value(row)
    if isinstance(normalized, dict):
        priority_keys = [
            "teamSize",
            "objectiveDuration",
            "avgEventsPerRobot",
            "maxEventsPerRobot",
            "overlapDuration",
            "robotCompetition",
            "syncDelay",
            "branchWait",
            "transitionTimeSeconds",
            "transitionTime",
            "switchTime",
            "transitionToIntermediate",
            "transitionBack",
            "returnTime",
        ]
        summary: Dict[str, Any] = {}
        for key in priority_keys:
            value = normalized.get(key)
            if value not in (None, "", [], {}):
                summary[key] = value
        if not summary:
            scalar_items = [
                (key, value)
                for key, value in normalized.items()
                if isinstance(value, (str, int, float, bool))
            ]
            for key, value in scalar_items[:8]:
                summary[key] = value
        text = json.dumps(summary, ensure_ascii=True, default=str)
    else:
        text = json.dumps(normalized, ensure_ascii=True, default=str)
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _hover_html(title: str, fields: List[Tuple[str, Any]], row: Optional[Dict[str, Any]] = None) -> str:
    parts = [f"<b>{title}</b>"]
    for label, value in fields:
        if value is None or value == "":
            continue
        parts.append(f"{label}: {value}")
    if row:
        parts.append(f"details: {_compact_row_json(row, max_len=320)}")
    return "<br>".join(parts)


def _midpoint(left: Dict[str, Any], right: Dict[str, Any]) -> float:
    return (float(left["end_s"]) + float(right["start_s"])) / 2.0


def _mission_bounds(events: List[Dict[str, Any]]) -> Tuple[float, float]:
    if not events:
        return 0.0, 1.0
    return 0.0, max(float(event["end_s"]) for event in events)


def _edge_style(pattern_name: str) -> Dict[str, Any]:
    short = pattern_short(pattern_name)
    dash_map = {"HO": "solid", "SW": "dash", "CR": "dot"}
    symbol_map = {"HO": "circle", "SW": "diamond", "CR": "square"}
    return {
        "dash": dash_map.get(short, "solid"),
        "symbol": symbol_map.get(short, "circle"),
        "label": short,
    }


def _is_structural_short(short: str) -> bool:
    return short in {"CP", "PC", "SYNC"}


def _item_visible(short: str, emphasis_mode: str) -> bool:
    if emphasis_mode == "Structural patterns only":
        return _is_structural_short(short)
    if emphasis_mode == "Event-to-event patterns only":
        return short in {"HO", "SW", "CR"}
    return True


def _opacity_for_kind(short: str, emphasis_mode: str, base: float = 1.0) -> float:
    if emphasis_mode == "Balanced":
        return base
    if emphasis_mode == "Structural patterns only":
        return base if _is_structural_short(short) else 0.0
    if emphasis_mode == "Event-to-event patterns only":
        return base if short in {"HO", "SW", "CR"} else 0.0
    if emphasis_mode == "Highlight co-participation":
        if short == "CP":
            return base
        if short == "PC" or short == "SYNC":
            return min(base, 0.35)
        return min(base, 0.22)
    if emphasis_mode == "Highlight parallel collaboration":
        if short == "PC":
            return base
        if short == "CP" or short == "SYNC":
            return min(base, 0.35)
        return min(base, 0.22)
    return base


def build_pattern_rail_items(
    mission_id: str,
    events: List[Dict[str, Any]],
    transitions: List[Dict[str, Any]],
    structural_highlights: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    by_id = _event_lookup(events)
    extents = segment_extents(events)
    mission_start, mission_end = _mission_bounds(events)

    for transition in transitions:
        left = by_id.get(str(transition["from_event_id"]))
        right = by_id.get(str(transition["to_event_id"]))
        if not left or not right:
            continue
        short = pattern_short(transition["pattern_name"])
        if short not in {"HO", "SW", "CR"}:
            continue
        row = transition.get("row", {})
        items.append({
            "kind": "marker",
            "row_label": short,
            "pattern_name": transition["pattern_name"],
            "pattern_short": short,
            "time": _midpoint(left, right),
            "hover": pattern_edge_hover_html(transition["pattern_name"], transition, left, right),
            "row": row,
        })

    for highlight in structural_highlights:
        pattern_name = str(highlight["pattern_name"])
        short = pattern_short(pattern_name)
        row = highlight.get("row", {})
        if highlight["kind"] == "segment_team":
            segment = str(highlight["segment"])
            ext = extents.get(segment)
            if ext:
                items.append({
                    "kind": "interval",
                    "row_label": "CP",
                    "pattern_name": pattern_name,
                    "pattern_short": "CP",
                    "x0": relative_seconds(float(ext["start_ms"]), mission_span(events)[0]),
                    "x1": relative_seconds(float(ext["end_ms"]), mission_span(events)[0]),
                    "hover": _hover_html(
                        "Co-Participation",
                        [
                            ("objective", f"Segment {segment}"),
                            ("teamSize", row.get("teamSize")),
                            ("objectiveDuration", format_seconds(row.get("objectiveDuration"))),
                            ("avgEventsPerRobot", row.get("avgEventsPerRobot")),
                            ("maxEventsPerRobot", row.get("maxEventsPerRobot")),
                        ],
                        row,
                    ),
                })
        elif highlight["kind"] == "mission_team":
            items.append({
                "kind": "interval",
                "row_label": "CP",
                "pattern_name": pattern_name,
                "pattern_short": "CP",
                "x0": mission_start,
                "x1": mission_end,
                "hover": _hover_html(
                    "Co-Participation",
                    [
                        ("objective", f"Mission {mission_id}"),
                        ("teamSize", row.get("teamSize")),
                        ("objectiveDuration", format_seconds(row.get("objectiveDuration"))),
                        ("avgEventsPerRobot", row.get("avgEventsPerRobot")),
                        ("maxEventsPerRobot", row.get("maxEventsPerRobot")),
                    ],
                    row,
                ),
            })
        elif highlight["kind"] == "parallel_segments":
            segment1 = str(highlight["segment1"])
            segment2 = str(highlight["segment2"])
            extent1 = extents.get(segment1)
            extent2 = extents.get(segment2)
            if extent1 and extent2:
                overlap_start = max(float(extent1["start_ms"]), float(extent2["start_ms"]))
                overlap_end = min(float(extent1["end_ms"]), float(extent2["end_ms"]))
                if overlap_start < overlap_end:
                    items.append({
                        "kind": "interval",
                        "row_label": "PC",
                        "pattern_name": pattern_name,
                        "pattern_short": "PC",
                        "x0": relative_seconds(overlap_start, mission_span(events)[0]),
                        "x1": relative_seconds(overlap_end, mission_span(events)[0]),
                        "hover": _hover_html(
                            "Parallel Collaboration",
                            [
                                ("objective", f"{segment1} || {segment2}"),
                                ("overlapDuration", format_seconds(row.get("overlapDuration"))),
                                ("robotCompetition", row.get("robotCompetition")),
                            ],
                            row,
                        ),
                    })
        elif highlight["kind"] == "parallel_mission":
            items.append({
                "kind": "interval",
                "row_label": "PC",
                "pattern_name": pattern_name,
                "pattern_short": "PC",
                "x0": mission_start,
                "x1": mission_end,
                "hover": _hover_html(
                    "Parallel Collaboration",
                    [
                        ("objective", f"Mission {mission_id}"),
                        ("label", highlight.get("label")),
                    ],
                    row,
                ),
            })
        elif highlight["kind"] == "sync":
            downstream = by_id.get(str(highlight.get("downstream_event_id")))
            if downstream:
                items.append({
                    "kind": "sync",
                    "row_label": "SYNC",
                    "pattern_name": pattern_name,
                    "pattern_short": "SYNC",
                    "time": float(downstream["start_s"]),
                    "hover": _hover_html(
                        "Synchronization Diagnostics",
                        [
                            ("objective", f"Mission {mission_id}"),
                            ("downstream_event", highlight.get("downstream_event_id")),
                            ("syncDelay", format_seconds(row.get("syncDelay"))),
                            ("branchWait", format_seconds(row.get("branchWait"))),
                        ],
                        row,
                    ),
                })
    return items


def render_pattern_rail_plotly(
    mission_id: str,
    rail_items: List[Dict[str, Any]],
    x_range: Tuple[float, float],
    emphasis_mode: str,
    large_view: bool,
) -> Any:
    go, _ = _plotly_required()
    row_order = ["SYNC", "PC", "CP", "CR", "SW", "HO"]
    y_map = {label: index for index, label in enumerate(reversed(row_order))}
    colors = pattern_color_map([item["pattern_name"] for item in rail_items])
    fig = go.Figure()

    for item in rail_items:
        short = item["pattern_short"]
        if not _item_visible(short, emphasis_mode):
            continue
        opacity = _opacity_for_kind(short, emphasis_mode, 0.95)
        color = colors.get(item["pattern_name"], "#2563EB")
        y_value = y_map[item["row_label"]]
        if item["kind"] == "interval":
            fig.add_trace(go.Bar(
                x=[max(0.05, float(item["x1"]) - float(item["x0"]))],
                base=[float(item["x0"])],
                y=[y_value],
                orientation="h",
                width=0.62 if short == "CP" else 0.72,
                marker=dict(
                    color=color,
                    opacity=opacity,
                    line=dict(color=color, width=2),
                    pattern=dict(shape="/" if short == "PC" else "", fgcolor=color),
                ),
                name=short,
                legendgroup=short,
                showlegend=False,
                customdata=[[item["hover"]]],
                hovertemplate="%{customdata[0]}<extra></extra>",
            ))
        elif item["kind"] == "sync":
            fig.add_trace(go.Scatter(
                x=[float(item["time"])],
                y=[y_value],
                mode="markers",
                marker=dict(color=color, size=12, symbol="line-ns-open"),
                name=short,
                legendgroup=short,
                showlegend=False,
                customdata=[[item["hover"]]],
                hovertemplate="%{customdata[0]}<extra></extra>",
            ))
        else:
            style = _edge_style(item["pattern_name"])
            fig.add_trace(go.Scatter(
                x=[float(item["time"])],
                y=[y_value],
                mode="markers+text",
                marker=dict(color=color, size=12, symbol=style["symbol"], opacity=opacity),
                text=[style["label"]],
                textposition="middle right",
                textfont=dict(size=9, color=color),
                name=short,
                legendgroup=short,
                showlegend=False,
                customdata=[[item["hover"]]],
                hovertemplate="%{customdata[0]}<extra></extra>",
            ))

    fig.update_layout(
        title=f"Mission {mission_id}: Pattern Rail",
        barmode="overlay",
        dragmode="pan",
        height=260 if large_view else 220,
        margin=dict(l=90, r=20, t=55, b=30),
        xaxis=dict(title="", range=list(x_range), showgrid=True, gridcolor="rgba(148,163,184,0.18)", zeroline=False),
        yaxis=dict(
            title="Pattern",
            tickmode="array",
            tickvals=[y_map[label] for label in row_order],
            ticktext=row_order,
            range=[-0.7, len(row_order) - 0.3],
            showgrid=False,
        ),
        template="plotly_white",
        hovermode="closest",
        showlegend=False,
    )
    return fig


def build_objective_layer_items(
    mission_id: str,
    events: List[Dict[str, Any]],
    structural_highlights: List[Dict[str, Any]],
) -> Dict[str, Any]:
    extents = segment_extents(events)
    min_ms, max_ms = mission_span(events)
    segment_ids = sorted(extents)
    mission_row = f"Mission {mission_id}"
    base_rows: List[Dict[str, Any]] = [{
        "row_label": mission_row,
        "kind": "mission",
        "x0": 0.0,
        "x1": relative_seconds(max_ms, min_ms),
        "hover": _hover_html("Mission Interval", [("mission", mission_id)]),
        "segment_id": "",
    }]
    for segment_id in segment_ids:
        ext = extents[segment_id]
        base_rows.append({
            "row_label": f"Segment {segment_id}",
            "kind": "segment",
            "x0": relative_seconds(float(ext["start_ms"]), min_ms),
            "x1": relative_seconds(float(ext["end_ms"]), min_ms),
            "hover": _hover_html("Segment Interval", [("segment", segment_id)]),
            "segment_id": segment_id,
        })

    overlays: List[Dict[str, Any]] = []
    for highlight in structural_highlights:
        row = highlight.get("row", {})
        if highlight["kind"] == "mission_team":
            overlays.append({
                "kind": "cp",
                "rows": [mission_row],
                "x0": 0.0,
                "x1": relative_seconds(max_ms, min_ms),
                "label": f"CP team={row.get('teamSize', '?')}",
                "hover": _hover_html(
                    "Co-Participation",
                    [
                        ("objective", mission_row),
                        ("teamSize", row.get("teamSize")),
                        ("objectiveDuration", format_seconds(row.get("objectiveDuration"))),
                        ("avgEventsPerRobot", row.get("avgEventsPerRobot")),
                        ("maxEventsPerRobot", row.get("maxEventsPerRobot")),
                    ],
                    row,
                ),
            })
        elif highlight["kind"] == "segment_team":
            segment_id = str(highlight["segment"])
            ext = extents.get(segment_id)
            if ext:
                overlays.append({
                    "kind": "cp",
                    "rows": [f"Segment {segment_id}"],
                    "x0": relative_seconds(float(ext["start_ms"]), min_ms),
                    "x1": relative_seconds(float(ext["end_ms"]), min_ms),
                    "label": f"CP team={row.get('teamSize', '?')}",
                    "hover": _hover_html(
                        "Co-Participation",
                        [
                            ("objective", f"Segment {segment_id}"),
                            ("teamSize", row.get("teamSize")),
                            ("objectiveDuration", format_seconds(row.get("objectiveDuration"))),
                            ("avgEventsPerRobot", row.get("avgEventsPerRobot")),
                            ("maxEventsPerRobot", row.get("maxEventsPerRobot")),
                        ],
                        row,
                    ),
                })
        elif highlight["kind"] == "parallel_segments":
            segment1 = str(highlight["segment1"])
            segment2 = str(highlight["segment2"])
            extent1 = extents.get(segment1)
            extent2 = extents.get(segment2)
            if extent1 and extent2:
                overlap_start = max(float(extent1["start_ms"]), float(extent2["start_ms"]))
                overlap_end = min(float(extent1["end_ms"]), float(extent2["end_ms"]))
                if overlap_start < overlap_end:
                    overlays.append({
                        "kind": "pc",
                        "rows": [f"Segment {segment1}", f"Segment {segment2}"],
                        "x0": relative_seconds(overlap_start, min_ms),
                        "x1": relative_seconds(overlap_end, min_ms),
                        "label": f"PC {segment1} || {segment2}",
                        "hover": _hover_html(
                            "Parallel Collaboration",
                            [
                                ("segment1", segment1),
                                ("segment2", segment2),
                                ("overlapDuration", format_seconds(row.get("overlapDuration"))),
                                ("robotCompetition", row.get("robotCompetition")),
                            ],
                            row,
                        ),
                    })
        elif highlight["kind"] == "parallel_mission":
            overlays.append({
                "kind": "pc",
                "rows": [mission_row],
                "x0": 0.0,
                "x1": relative_seconds(max_ms, min_ms),
                "label": f"PC {highlight.get('label', mission_id)}",
                "hover": _hover_html("Parallel Collaboration", [("objective", mission_row), ("label", highlight.get("label"))], row),
            })
        elif highlight["kind"] == "sync":
            downstream_event = next((event for event in events if str(event["event_id"]) == str(highlight.get("downstream_event_id"))), None)
            if downstream_event:
                overlays.append({
                    "kind": "sync",
                    "rows": [mission_row],
                    "time": float(downstream_event["start_s"]),
                    "label": "SYNC",
                    "hover": _hover_html(
                        "Synchronization Diagnostics",
                        [
                            ("downstream_event", highlight.get("downstream_event_id")),
                            ("syncDelay", format_seconds(row.get("syncDelay"))),
                            ("branchWait", format_seconds(row.get("branchWait"))),
                        ],
                        row,
                    ),
                })
    return {"base_rows": base_rows, "overlays": overlays}


def render_objective_layer_plotly(
    mission_id: str,
    objective_items: Dict[str, Any],
    segment_colors: Dict[str, str],
    x_range: Tuple[float, float],
    emphasis_mode: str,
    large_view: bool,
) -> Any:
    go, _ = _plotly_required()
    row_labels = [item["row_label"] for item in objective_items["base_rows"]]
    y_map = {label: index for index, label in enumerate(reversed(row_labels))}
    fig = go.Figure()

    event_bar_dim = 0.45 if emphasis_mode in {"Highlight co-participation", "Highlight parallel collaboration"} else 0.9

    for item in objective_items["base_rows"]:
        color = "#CBD5E1" if item["kind"] == "mission" else segment_colors.get(item["segment_id"], "#94A3B8")
        fig.add_trace(go.Bar(
            x=[max(0.05, float(item["x1"]) - float(item["x0"]))],
            base=[float(item["x0"])],
            y=[y_map[item["row_label"]]],
            orientation="h",
            width=0.5,
            marker=dict(color=color, opacity=event_bar_dim, line=dict(color=color, width=2)),
            name=item["row_label"],
            showlegend=False,
            customdata=[[item["hover"]]],
            hovertemplate="%{customdata[0]}<extra></extra>",
        ))

    for overlay in objective_items["overlays"]:
        short = "CP" if overlay["kind"] == "cp" else "PC" if overlay["kind"] == "pc" else "SYNC"
        if not _item_visible(short, emphasis_mode):
            continue
        color = {"CP": "#7C3AED", "PC": "#059669", "SYNC": "#D97706"}[short]
        opacity = _opacity_for_kind(short, emphasis_mode, 1.0)
        if overlay["kind"] in {"cp", "pc"}:
            for row_label in overlay["rows"]:
                y_value = y_map[row_label]
                fig.add_trace(go.Scatter(
                    x=[float(overlay["x0"]), float(overlay["x1"])],
                    y=[y_value, y_value],
                    mode="lines",
                    line=dict(
                        color=color,
                        width=16 if overlay["kind"] == "pc" else 12,
                        dash="dash" if overlay["kind"] == "cp" else "solid",
                    ),
                    opacity=opacity,
                    showlegend=False,
                    customdata=[[overlay["hover"]], [overlay["hover"]]],
                    hovertemplate="%{customdata[0]}<extra></extra>",
                ))
            label_row = overlay["rows"][0]
            label_x = (float(overlay["x0"]) + float(overlay["x1"])) / 2
            fig.add_trace(go.Scatter(
                x=[label_x],
                y=[y_map[label_row] + 0.18],
                mode="markers+text",
                marker=dict(
                    color="white",
                    size=16 if overlay["kind"] == "pc" else 14,
                    line=dict(color=color, width=2),
                    symbol="diamond" if overlay["kind"] == "pc" else "square",
                    opacity=opacity,
                ),
                text=[overlay["label"]],
                textposition="middle right",
                textfont=dict(color=color, size=9),
                showlegend=False,
                customdata=[[overlay["hover"]]],
                hovertemplate="%{customdata[0]}<extra></extra>",
            ))
        else:
            time = float(overlay["time"])
            fig.add_vline(x=time, line=dict(color=color, width=2.5, dash="dash"))
            fig.add_trace(go.Scatter(
                x=[time],
                y=[max(y_map.values()) + 0.25],
                mode="markers+text",
                marker=dict(color="white", size=12, line=dict(color=color, width=2)),
                text=[overlay["label"]],
                textposition="middle right",
                textfont=dict(color=color, size=9),
                showlegend=False,
                customdata=[[overlay["hover"]]],
                hovertemplate="%{customdata[0]}<extra></extra>",
            ))

    fig.update_layout(
        title=f"Mission {mission_id}: Objective Layer",
        barmode="overlay",
        dragmode="pan",
        height=max(260, min(720, 110 + 60 * len(row_labels))) if large_view else max(220, min(560, 90 + 52 * len(row_labels))),
        margin=dict(l=130, r=20, t=55, b=30),
        xaxis=dict(title="", range=list(x_range), showgrid=True, gridcolor="rgba(148,163,184,0.18)", zeroline=False),
        yaxis=dict(
            title="Objective",
            tickmode="array",
            tickvals=[y_map[label] for label in row_labels],
            ticktext=row_labels,
            showgrid=False,
        ),
        template="plotly_white",
        hovermode="closest",
        showlegend=False,
    )
    return fig


def render_robot_event_timeline_plotly(
    mission_id: str,
    events: List[Dict[str, Any]],
    transitions: List[Dict[str, Any]],
    segment_colors: Dict[str, str],
    x_range: Tuple[float, float],
    emphasis_mode: str,
    show_activity_labels: bool,
    show_df_backbone: bool,
    large_view: bool,
    enable_range_slider: bool,
) -> Any:
    go, _ = _plotly_required()
    robots = sorted({str(event["robot_id"]) for event in events}, reverse=True)
    y_map = {robot: index for index, robot in enumerate(reversed(robots))}
    edge_colors = pattern_color_map([transition["pattern_name"] for transition in transitions])
    fig = go.Figure()

    event_opacity = 0.38 if emphasis_mode in {"Highlight co-participation", "Highlight parallel collaboration"} else 1.0
    by_segment: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_segment[str(event.get("segment_id") or "mission-level")].append(event)

    for segment_id, segment_events in by_segment.items():
        color = segment_colors.get(segment_id, "#CBD5E1")
        text = [event["activity_short"] if show_activity_labels and float(event["duration_s"]) >= 1.0 else "" for event in segment_events]
        fig.add_trace(go.Bar(
            name=f"Segment {segment_id}",
            y=[y_map[event["robot_id"]] for event in segment_events],
            x=[float(event["duration_s"]) for event in segment_events],
            base=[float(event["start_s"]) for event in segment_events],
            orientation="h",
            width=0.58,
            marker=dict(color="#F8FAFC", opacity=event_opacity, line=dict(color=color, width=2)),
            text=text,
            textposition="inside",
            insidetextanchor="middle",
            textfont=dict(size=11, color="#0F172A"),
            customdata=[
                [
                    event["event_id"],
                    event["activity"],
                    event["robot_id"],
                    event.get("segment_id") or "",
                    event["start_text"],
                    event["end_text"],
                    float(event["duration_s"]),
                ]
                for event in segment_events
            ],
            hovertemplate=(
                "<b>%{customdata[1]}</b><br>"
                "event_id: %{customdata[0]}<br>"
                "robot_id: %{customdata[2]}<br>"
                "segment_id: %{customdata[3]}<br>"
                "start: %{customdata[4]}<br>"
                "end: %{customdata[5]}<br>"
                "duration_s: %{customdata[6]:.3f}<extra></extra>"
            ),
        ))

    if show_df_backbone:
        traces_added = False
        by_robot: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for event in events:
            by_robot[str(event["robot_id"])].append(event)
        for robot, robot_events in by_robot.items():
            robot_events = sorted(robot_events, key=lambda item: (float(item["start_s"]), float(item["end_s"]), str(item["event_id"])))
            xs: List[Optional[float]] = []
            ys: List[Optional[float]] = []
            hover: List[str] = []
            for left, right in zip(robot_events, robot_events[1:]):
                xs.extend([float(left["end_s"]), float(right["start_s"]), None])
                ys.extend([float(y_map[robot]), float(y_map[robot]), None])
                hover.extend([
                    f"{left['activity']} -> {right['activity']}<br>{left['event_id']} -> {right['event_id']}",
                    f"{left['activity']} -> {right['activity']}<br>{left['event_id']} -> {right['event_id']}",
                    "",
                ])
            if xs:
                fig.add_trace(go.Scatter(
                    x=xs,
                    y=ys,
                    mode="lines",
                    name="Robot sequence",
                    legendgroup="df_backbone",
                    showlegend=not traces_added,
                    line=dict(color="rgba(100,116,139,0.58)", width=1.4, dash="dot"),
                    hovertext=hover,
                    hoverinfo="text",
                ))
                traces_added = True

    if emphasis_mode != "Structural patterns only":
        by_id = _event_lookup(events)
        overlap_slots: Dict[Tuple[str, str, int, int], int] = defaultdict(int)
        shown: set[str] = set()
        for transition in transitions:
            pattern_name = str(transition["pattern_name"])
            short = pattern_short(pattern_name)
            if not _item_visible(short, emphasis_mode):
                continue
            left = by_id.get(str(transition["from_event_id"]))
            right = by_id.get(str(transition["to_event_id"]))
            if not left or not right:
                continue
            key = (
                str(left["robot_id"]),
                str(right["robot_id"]),
                round(float(left["end_s"]) * 10),
                round(float(right["start_s"]) * 10),
            )
            slot = overlap_slots[key]
            overlap_slots[key] += 1
            offset = (slot % 5 - 2) * 0.08
            left_y = float(y_map[str(left["robot_id"])]) + offset
            right_y = float(y_map[str(right["robot_id"])]) + offset
            mid_y = (left_y + right_y) / 2.0
            mid_x = _midpoint(left, right)
            color = edge_colors.get(pattern_name, "#DC2626")
            opacity = _opacity_for_kind(short, emphasis_mode, 0.95)
            style = _edge_style(pattern_name)
            hover = pattern_edge_hover_html(pattern_name, transition, left, right)

            fig.add_trace(go.Scatter(
                x=[float(left["end_s"]), float(right["start_s"])],
                y=[left_y, right_y],
                mode="lines+markers",
                name=humanize_name(pattern_name),
                legendgroup=pattern_name,
                showlegend=pattern_name not in shown,
                line=dict(color=color, width=3, dash=style["dash"]),
                marker=dict(color=color, size=6, symbol=style["symbol"], opacity=opacity),
                opacity=opacity,
                hoverinfo="skip",
            ))
            fig.add_trace(go.Scatter(
                x=[mid_x],
                y=[mid_y],
                mode="markers+text",
                name=humanize_name(pattern_name),
                legendgroup=pattern_name,
                showlegend=False,
                marker=dict(color="white", size=12, symbol=style["symbol"], line=dict(color=color, width=2), opacity=opacity),
                text=[style["label"]],
                textposition="top center",
                textfont=dict(color=color, size=9),
                customdata=[[hover]],
                hovertemplate="%{customdata[0]}<extra></extra>",
            ))
            shown.add(pattern_name)

    fig.update_layout(
        title=f"Mission {mission_id}: Robot Event Timeline",
        barmode="overlay",
        dragmode="pan",
        bargap=0.25,
        height=max(640, min(1800, 125 + 78 * len(robots))) if large_view else max(520, min(1400, 105 + 68 * len(robots))),
        margin=dict(l=110, r=40, t=55, b=70),
        xaxis=dict(
            title="Mission time, seconds from first event",
            range=list(x_range),
            showgrid=True,
            gridcolor="rgba(148,163,184,0.25)",
            zeroline=False,
            rangeslider=dict(visible=enable_range_slider),
        ),
        yaxis=dict(
            title="robot_id",
            tickmode="array",
            tickvals=[y_map[robot] for robot in robots],
            ticktext=robots,
            showgrid=True,
            gridcolor="rgba(148,163,184,0.16)",
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
            bgcolor="rgba(255,255,255,0.8)",
        ),
        hovermode="closest",
        template="plotly_white",
    )
    return fig


def render_full_mission_timeline(
    mission_id: str,
    mission_events: List[Dict[str, Any]],
    pattern_rows_by_name: Dict[str, List[Dict[str, Any]]],
    selected_patterns: List[str],
    emphasis_mode: str,
    show_activity_labels: bool,
    show_df_backbone: bool,
    large_view: bool,
    enable_range_slider: bool,
) -> None:
    _, pio = _plotly_required()
    events = prepare_events_for_timeline(mission_events)
    if not events:
        st.info("Mission events do not expose usable start/end timestamps for timeline rendering.")
        return

    transitions = extract_multi_pattern_transitions(pattern_rows_by_name, events, selected_patterns)
    structural_highlights = extract_structural_highlights(pattern_rows_by_name, mission_id, events, selected_patterns)
    render_dashboard_cards(build_timeline_summary(events, transitions, structural_highlights))

    segment_ids = [str(event.get("segment_id") or "mission-level") for event in events]
    segment_colors = segment_color_map(segment_ids)
    if "mission-level" not in segment_colors:
        segment_colors["mission-level"] = "#CBD5E1"
    x_range = _mission_bounds(events)

    rail_items = build_pattern_rail_items(mission_id, events, transitions, structural_highlights)
    objective_items = build_objective_layer_items(mission_id, events, structural_highlights)

    rail_fig = render_pattern_rail_plotly(mission_id, rail_items, x_range, emphasis_mode, large_view)
    objective_fig = render_objective_layer_plotly(mission_id, objective_items, segment_colors, x_range, emphasis_mode, large_view)
    robot_fig = render_robot_event_timeline_plotly(
        mission_id,
        events,
        transitions,
        segment_colors,
        x_range,
        emphasis_mode,
        show_activity_labels,
        show_df_backbone,
        large_view,
        enable_range_slider,
    )

    st.caption("The timeline is split into a pattern rail, an objective layer, and the robot event timeline so objective-level structures such as co-participation and parallel collaboration remain visible.")
    st.plotly_chart(rail_fig, width="stretch", config={"scrollZoom": True, "displaylogo": False, "responsive": True})
    st.plotly_chart(objective_fig, width="stretch", config={"scrollZoom": True, "displaylogo": False, "responsive": True})
    st.plotly_chart(
        robot_fig,
        width="stretch",
        config={
            "scrollZoom": True,
            "displayModeBar": True,
            "displaylogo": False,
            "responsive": True,
            "toImageButtonOptions": {"format": "png", "filename": f"mission_{mission_id}_timeline", "height": int(robot_fig.layout.height or 900), "width": 1800, "scale": 2},
            "modeBarButtonsToAdd": ["drawline", "drawrect", "eraseshape"],
        },
    )

    html = (
        "<html><head><meta charset='utf-8'><title>Mission Timeline</title></head><body style='font-family:sans-serif;background:#f8fafc;'>"
        f"<h2 style='margin:16px 24px;'>Mission {mission_id}: Collaboration Timeline</h2>"
        f"<div style='margin:12px 24px;'>{pio.to_html(rail_fig, full_html=False, include_plotlyjs='cdn')}</div>"
        f"<div style='margin:12px 24px;'>{pio.to_html(objective_fig, full_html=False, include_plotlyjs=False)}</div>"
        f"<div style='margin:12px 24px 24px 24px;'>{pio.to_html(robot_fig, full_html=False, include_plotlyjs=False)}</div>"
        "</body></html>"
    )
    st.download_button(
        "Download / open standalone interactive timeline HTML",
        data=html,
        file_name=f"mission_{mission_id}_interactive_timeline.html",
        mime="text/html",
        key="download_interactive_timeline_html",
    )

    with st.expander("Event table used in the timeline", expanded=False):
        st.dataframe(
            table_safe_rows([
                {
                    "seq": event["seq"],
                    "event_id": event["event_id"],
                    "activity": event["activity"],
                    "robot_id": event["robot_id"],
                    "segment_id": event.get("segment_id", ""),
                    "start": event["start_text"],
                    "end": event["end_text"],
                    "start_s": round(float(event["start_s"]), 3),
                    "duration_s": round(float(event["duration_s"]), 3),
                }
                for event in events
            ]),
            width="stretch",
            hide_index=True,
        )

    with st.expander("Pattern overlays anchored to this timeline", expanded=False):
        overlay_rows: List[Dict[str, Any]] = []
        for transition in transitions:
            overlay_rows.append({
                "kind": "event-link",
                "pattern": transition["pattern_name"],
                "from_event": transition["from_event_id"],
                "to_event": transition["to_event_id"],
                "pair": transition.get("pair"),
                "label": transition["label"],
            })
        for highlight in structural_highlights:
            overlay_rows.append({
                "kind": highlight.get("kind"),
                "pattern": highlight.get("pattern_name"),
                "label": highlight.get("label"),
                "scope": highlight.get("segment") or highlight.get("segment1") or highlight.get("downstream_event_id") or mission_id,
            })
        if overlay_rows:
            st.dataframe(table_safe_rows(overlay_rows), width="stretch", hide_index=True)
        else:
            st.info("No selected pattern occurrence could be anchored to this mission timeline.")


def render_timeline_tab(driver: Any, database: Optional[str], catalog: Dict[str, Dict[str, str]], log_name: Optional[str]) -> None:
    st.subheader("Mission Timeline")
    st.caption(
        "Inspect a selected mission through three coordinated layers: a pattern rail, an objective layer, and the robot event timeline. "
        "This makes co-participation and parallel collaboration visible without losing event-level detail."
    )

    mission_ids = st.session_state.get("collab_mission_ids")
    if mission_ids is None:
        mission_ids = fetch_mission_ids(driver, database, log_name)
        st.session_state["collab_mission_ids"] = mission_ids

    if not mission_ids:
        st.info("No missions found for the current database/log filter.")
        return

    selected_mission_id = st.selectbox("Mission to visualize", mission_ids, key="collab_selected_mission_timeline")

    occurrence_pattern_names = list(catalog["Occurrences"].keys())
    timeline_pattern_names = occurrence_pattern_names[:]
    if "sync_diagnostics_parallel_segments" in catalog.get("Diagnostics", {}):
        timeline_pattern_names.append("sync_diagnostics_parallel_segments")

    default_patterns = [
        name for name in timeline_pattern_names
        if name.startswith((
            "handover_segment",
            "handover_mission",
            "objective_switch_mission",
            "objective_switch_segment",
            "capability_driven_return_segment",
            "co_participation_segment",
            "parallel_collaboration_segment",
            "sync_diagnostics",
        ))
    ]
    if not default_patterns:
        default_patterns = timeline_pattern_names[: min(5, len(timeline_pattern_names))]

    selected_timeline_patterns = st.multiselect(
        "Patterns to highlight",
        timeline_pattern_names,
        default=default_patterns,
        key="collab_selected_timeline_patterns",
    )

    c1, c2, c3, c4 = st.columns([1, 1, 1, 1.2])
    with c1:
        row_limit = st.slider("Rows per pattern", min_value=20, max_value=1500, value=400, step=20, key="collab_timeline_pattern_limit")
    with c2:
        show_activity_labels = st.checkbox("Show activity labels inside bars", value=True, key="collab_show_activity_labels")
    with c3:
        show_df_backbone = st.checkbox("Show robot sequence backbone", value=True, key="collab_show_df_backbone")
    with c4:
        emphasis_mode = st.selectbox(
            "Pattern emphasis mode",
            [
                "Balanced",
                "Structural patterns only",
                "Highlight co-participation",
                "Highlight parallel collaboration",
                "Event-to-event patterns only",
            ],
            index=0,
            key="collab_timeline_emphasis_mode",
        )

    c4, c5 = st.columns([1, 1])
    with c4:
        large_view = st.checkbox("Large / near full-screen view", value=True, key="collab_large_timeline")
    with c5:
        enable_range_slider = st.checkbox("Show x-axis range slider", value=True, key="collab_range_slider")

    mission_events = fetch_mission_events(driver, database, selected_mission_id, log_name)
    pattern_rows_by_name: Dict[str, List[Dict[str, Any]]] = {}
    for pattern_name in selected_timeline_patterns:
        query = catalog["Occurrences"].get(pattern_name) or catalog["Diagnostics"].get(pattern_name)
        if query:
            pattern_rows_by_name[pattern_name] = run_pattern_query(driver, database, query, log_name, row_limit)

    try:
        render_full_mission_timeline(
            mission_id=selected_mission_id,
            mission_events=mission_events,
            pattern_rows_by_name=pattern_rows_by_name,
            selected_patterns=selected_timeline_patterns,
            emphasis_mode=emphasis_mode,
            show_activity_labels=show_activity_labels,
            show_df_backbone=show_df_backbone,
            large_view=large_view,
            enable_range_slider=enable_range_slider,
        )
    except ImportError as exc:
        st.error(str(exc))


def clear_state() -> None:
    for key in (
        "collab_connected",
        "collab_connection_error",
        "collab_logs",
        "collab_dashboard",
        "collab_explorer_rows",
        "collab_explorer_query",
        "collab_mission_ids",
    ):
        st.session_state.pop(key, None)


def render_page() -> None:
    st.title("Object-Centric Collaboration Mining Dashboard")
    st.write("Analyze handovers, co-participation, objective switches, capability-driven returns, and parallel collaboration over the EKG.")
    render_paper_scope_panel()

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
    selected_log = st.selectbox("Log filter", [ALL_OPTION] + logs, index=0, key="collab_log_filter")
    log_name = None if selected_log == ALL_OPTION else selected_log

    driver, error = neo4j_shared.get_neo4j_driver(uri, user, password)
    if driver is None:
        st.error(error)
        return

    try:
        tab_dashboard, tab_explorer, tab_timeline = st.tabs(["Dashboard", "Pattern Explorer", "Mission Timeline"])
        with tab_dashboard:
            render_dashboard(driver, database, catalog, log_name)
        with tab_explorer:
            render_explorer(driver, database, catalog, log_name)
        with tab_timeline:
            render_timeline_tab(driver, database, catalog, log_name)
    finally:
        driver.close()


def main() -> None:
    st.set_page_config(page_title="Object-Centric Collaboration Mining", page_icon="🤖", layout="wide")
    render_page()


if __name__ == "__main__":
    main()
