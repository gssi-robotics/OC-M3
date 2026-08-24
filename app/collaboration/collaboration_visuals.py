from __future__ import annotations

import json
import math
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st
import streamlit.components.v1 as components

from .collaboration_data import (
    build_timeline_summary,
    extract_multi_pattern_transitions,
    extract_structural_highlights,
    mission_span,
    prepare_events_for_timeline,
    relative_seconds,
    segment_extents,
)
from .collaboration_utils import (
    PATTERN_METADATA,
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


def render_dashboard_cards(cards: List[Dict[str, str]]) -> None:
    blocks = []
    for card in cards:
        accent = card.get("accent", "#2563EB")
        blocks.append(
            f"""
            <div style="
                background: linear-gradient(135deg, #ffffff 0%, #f8fbff 100%);
                border: 1px solid #e6edf5;
                border-top: 4px solid {accent};
                border-radius: 16px;
                padding: 16px 18px;
                min-height: 112px;
                box-shadow: 0 8px 22px rgba(15, 23, 42, 0.06);
            ">
                <div style="font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; color: #64748b;">
                    {str(card['label'])}
                </div>
                <div style="font-size: 32px; font-weight: 800; color: #0f172a; margin-top: 8px;">
                    {str(card['value'])}
                </div>
                <div style="font-size: 13px; color: #475569; margin-top: 8px; line-height: 1.35;">
                    {str(card.get('caption', ''))}
                </div>
            </div>
            """
        )
    html = f"""
    <div style="display:grid; grid-template-columns: repeat({max(1, len(cards))}, minmax(0, 1fr)); gap: 14px; margin: 6px 0 18px 0;">
        {''.join(blocks)}
    </div>
    """
    components.html(html, height=160, scrolling=False)


def render_paper_scope_panel() -> None:
    html = """
    <div style="border:1px solid #dbeafe; border-radius:18px; padding:18px 20px; background:linear-gradient(135deg,#eff6ff 0%,#ffffff 58%); margin:8px 0 18px 0;">
        <div style="font-size:18px; font-weight:800; color:#0f172a; margin-bottom:8px;">Object-centric collaboration diagnostics</div>
        <div style="font-size:14px; color:#334155; line-height:1.5; max-width:1150px;">
        This page operationalizes the paper's collaboration structures over the EKG. Pattern queries return concrete matches, while the dashboard connects them to occurrence rates, transition and control-event context, allocation continuity, workload share, capability demand and availability, parallel resource coupling, and segment synchronization. The mission timeline remains task-level, with each block representing one task execution.
        </div>
    </div>
    """
    components.html(html, height=130, scrolling=False)


def render_rank_bars(title: str, rows: List[Dict[str, Any]], label_key: str, value_key: str, color: str = "#2563eb") -> None:
    if not rows:
        return
    max_value = max(float(row.get(value_key, 0) or 0) for row in rows) or 1.0
    blocks = []
    for row in rows[:10]:
        label = humanize_name(str(row.get(label_key, "")))
        value = float(row.get(value_key, 0) or 0)
        width = max(6.0, (value / max_value) * 100.0)
        blocks.append(
            f"""
            <div style="margin-bottom: 12px;">
                <div style="display:flex; justify-content:space-between; font-size:13px; color:#1f2937; margin-bottom:4px;">
                    <span>{label}</span><strong>{value:g}</strong>
                </div>
                <div style="background:#edf2f7; border-radius:999px; height:10px; overflow:hidden;">
                    <div style="width:{width:.2f}%; height:10px; background:{color}; border-radius:999px;"></div>
                </div>
            </div>
            """
        )
    html = f"""
    <div style="border:1px solid #e5e7eb; border-radius:16px; padding:16px 18px; background:white;">
        <div style="font-size:15px; font-weight:700; color:#0f172a; margin-bottom:12px;">{title}</div>
        {''.join(blocks)}
    </div>
    """
    components.html(html, height=380, scrolling=False)


def render_eventdrops_like_timeline(title: str, rows: List[Dict[str, Any]], metric_key: str) -> None:
    import plotly.graph_objects as go

    numeric_rows = []
    for idx, row in enumerate(rows):
        value = row.get(metric_key)
        if isinstance(value, (int, float)) and not math.isnan(float(value)):
            label = None
            for key in ("objective", "robot", "capability", "mission", "segment1", "segment2"):
                label = node_id(row.get(key))
                if label:
                    break
            numeric_rows.append({"label": label or f"row {idx + 1}", "value": float(value), "row": row})
    if not numeric_rows:
        return

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=[item["value"] for item in numeric_rows],
        y=[item["label"] for item in numeric_rows],
        mode="markers",
        marker=dict(size=11, color="#2563EB", opacity=0.78),
        customdata=[json.dumps(item["row"], default=str) for item in numeric_rows],
        hovertemplate="%{y}<br>value=%{x}<br>%{customdata}<extra></extra>",
    ))
    fig.update_layout(
        title=title,
        height=max(320, min(650, 38 * len(numeric_rows) + 120)),
        margin=dict(l=120, r=30, t=50, b=45),
        xaxis_title=metric_key,
        yaxis_title="",
    )
    st.plotly_chart(fig, width="stretch", config={"scrollZoom": True, "displaylogo": False})


def render_pattern_interpretation_table(occurrence_metrics: Dict[str, int]) -> None:
    rows = []
    for name, count in sorted(occurrence_metrics.items()):
        base = pattern_base_name(name)
        meta = PATTERN_METADATA.get(base, {})
        rows.append({
            "pattern_view": name,
            "family": meta.get("family", "Other"),
            "occurrences": count,
            "diagnostic_question": meta.get("question", ""),
        })
    st.dataframe(rows, width="stretch", hide_index=True)


def _plotly_required() -> Tuple[Any, Any]:
    try:
        import plotly.graph_objects as go
        import plotly.io as pio
    except ImportError as exc:  # pragma: no cover
        raise ImportError("Install Plotly with `pip install plotly` to use the interactive mission timeline.") from exc
    return go, pio


def _as_hover_json(value: Any) -> str:
    return json.dumps(normalize_value(value), ensure_ascii=True, default=str)


def _event_lookup(events: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(event["event_id"]): event for event in events}


def add_event_bars(fig: Any, events: List[Dict[str, Any]], segment_colors: Dict[str, str], show_activity_labels: bool) -> None:
    by_segment: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_segment[str(event.get("segment_id") or "mission-level")].append(event)

    for segment, segment_events in by_segment.items():
        color = segment_colors.get(segment, "#CBD5E1")
        fig.add_trace(go_bar_trace(segment, segment_events, color, show_activity_labels))


def go_bar_trace(segment: str, events: List[Dict[str, Any]], color: str, show_activity_labels: bool) -> Any:
    import plotly.graph_objects as go

    text = [event["activity_short"] if show_activity_labels else str(event["seq"]) for event in events]
    return go.Bar(
        name=f"Segment {segment}",
        y=[event["robot_id"] for event in events],
        x=[event["duration_s"] for event in events],
        base=[event["start_s"] for event in events],
        orientation="h",
        marker=dict(color="#F8FAFC", line=dict(color=color, width=1)),
        width=0.55,
        text=text,
        textposition="inside",
        insidetextanchor="middle",
        textfont=dict(size=11, color="#0F172A"),
        customdata=[
            [
                event["event_id"], event["activity"], event["start_text"], event["end_text"],
                event["robot_id"], event.get("segment_id") or "", event["seq"],
            ]
            for event in events
        ],
        hovertemplate=(
            "<b>%{customdata[1]}</b><br>"
            "event: %{customdata[0]}<br>"
            "robot: %{customdata[4]}<br>"
            "segment: %{customdata[5]}<br>"
            "start: %{customdata[2]}<br>"
            "end: %{customdata[3]}<br>"
            "duration: %{x:.2f}s<extra></extra>"
        ),
    )


def add_lane_backbone(fig: Any, events: List[Dict[str, Any]]) -> None:
    import plotly.graph_objects as go

    traces_added = False
    by_robot: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_robot[event["robot_id"]].append(event)

    for robot, robot_events in by_robot.items():
        robot_events = sorted(robot_events, key=lambda item: (item["start_s"], item["end_s"], str(item["event_id"])))
        xs: List[Optional[float]] = []
        ys: List[Optional[str]] = []
        hover: List[str] = []
        for left, right in zip(robot_events, robot_events[1:]):
            xs.extend([left["end_s"], right["start_s"], None])
            ys.extend([robot, robot, None])
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
                line=dict(color="rgba(100,116,139,0.65)", width=1.6, dash="dot"),
                hovertext=hover,
                hoverinfo="text",
            ))
            traces_added = True


def add_pattern_links(fig: Any, events: List[Dict[str, Any]], transitions: List[Dict[str, Any]], selected_patterns: List[str]) -> None:
    import plotly.graph_objects as go

    by_id = _event_lookup(events)
    colors = pattern_color_map(selected_patterns)
    shown: set[str] = set()

    for transition in transitions:
        left = by_id.get(transition["from_event_id"])
        right = by_id.get(transition["to_event_id"])
        if not left or not right:
            continue
        pattern_name = transition["pattern_name"]
        color = colors.get(pattern_name, "#DC2626")
        label = pattern_short(pattern_name)
        hover = pattern_edge_hover_html(pattern_name, transition, left, right)
        mid_x = (float(left["end_s"]) + float(right["start_s"])) / 2
        mid_y = left["robot_id"] if left["robot_id"] == right["robot_id"] else right["robot_id"]

        fig.add_trace(go.Scatter(
            x=[left["end_s"], right["start_s"]],
            y=[left["robot_id"], right["robot_id"]],
            mode="lines+markers+text",
            name=humanize_name(pattern_name),
            legendgroup=pattern_name,
            showlegend=pattern_name not in shown,
            line=dict(color=color, width=3),
            marker=dict(color=color, size=9, symbol="circle"),
            text=["", label],
            textposition="top center",
            textfont=dict(color=color, size=11),
            hoverinfo="skip",
        ))
        fig.add_trace(go.Scatter(
            x=[mid_x],
            y=[mid_y],
            mode="markers",
            name=humanize_name(pattern_name),
            legendgroup=pattern_name,
            showlegend=False,
            marker=dict(color=color, size=18, opacity=0.0),
            customdata=[[hover]],
            hovertemplate="%{customdata[0]}<extra></extra>",
        ))
        shown.add(pattern_name)


def add_structural_overlays(
    fig: Any,
    events: List[Dict[str, Any]],
    structural_highlights: List[Dict[str, Any]],
    selected_patterns: List[str],
) -> List[Dict[str, Any]]:
    extents = segment_extents(events)
    min_ms, _ = mission_span(events)
    colors = pattern_color_map(selected_patterns)
    rows: List[Dict[str, Any]] = []

    for highlight in structural_highlights:
        pattern_name = highlight["pattern_name"]
        color = colors.get(pattern_name, "#7C3AED")
        kind = highlight["kind"]
        label = highlight.get("label", pattern_short(pattern_name))

        if kind == "parallel_segments":
            segment1 = highlight["segment1"]
            segment2 = highlight["segment2"]
            extent1 = extents.get(segment1)
            extent2 = extents.get(segment2)
            if extent1 and extent2:
                overlap_start = max(float(extent1["start_ms"]), float(extent2["start_ms"]))
                overlap_end = min(float(extent1["end_ms"]), float(extent2["end_ms"]))
                if overlap_start < overlap_end:
                    x0 = relative_seconds(overlap_start, min_ms)
                    x1 = relative_seconds(overlap_end, min_ms)
                    fig.add_vrect(
                        x0=x0,
                        x1=x1,
                        fillcolor=color,
                        opacity=0.14,
                        line=dict(color=color, width=2, dash="dot"),
                        annotation_text=f"PC {segment1} || {segment2}",
                        annotation_position="top left",
                    )
                    rows.append({"kind": "parallel segments", "scope": f"{segment1} || {segment2}", "pattern": pattern_name, "label": label})

        elif kind == "parallel_mission":
            fig.add_annotation(
                x=0.5,
                y=1.13,
                xref="paper",
                yref="paper",
                text=f"PC {label}",
                showarrow=False,
                bgcolor="white",
                bordercolor=color,
                borderwidth=2,
                font=dict(color=color, size=12),
            )
            rows.append({"kind": "parallel mission", "scope": "mission", "pattern": pattern_name, "label": label})

        elif kind == "sync":
            downstream_id = highlight.get("downstream_event_id")
            event = _event_lookup(events).get(str(downstream_id))
            if event:
                fig.add_vline(
                    x=event["start_s"],
                    line=dict(color=color, width=2.5, dash="dash"),
                    annotation_text=f"SYNC {label}",
                    annotation_position="bottom right",
                )
                rows.append({"kind": "synchronization", "scope": downstream_id, "pattern": pattern_name, "label": label})

    return rows


def render_plotly_mission_timeline(
    mission_id: str,
    mission_events: List[Dict[str, Any]],
    pattern_rows_by_name: Dict[str, List[Dict[str, Any]]],
    selected_patterns: List[str],
    show_activity_labels: bool,
    show_df_backbone: bool,
    large_view: bool,
    enable_range_slider: bool,
) -> None:
    go, pio = _plotly_required()

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

    fig = go.Figure()
    add_event_bars(fig, events, segment_colors, show_activity_labels)

    if show_df_backbone:
        add_lane_backbone(fig, events)

    structural_rows = add_structural_overlays(fig, events, structural_highlights, selected_patterns)
    add_pattern_links(fig, events, transitions, selected_patterns)

    robots = sorted({event["robot_id"] for event in events}, reverse=True)
    height = max(640, min(1800, 125 + 72 * len(robots)))
    if large_view:
        height = max(900, min(2400, 170 + 92 * len(robots)))

    fig.update_layout(
        title=f"Mission {mission_id}: event-level process timeline",
        barmode="overlay",
        bargap=0.25,
        height=height,
        margin=dict(l=120, r=50, t=95, b=70),
        xaxis=dict(
            title="Mission time, seconds from first event",
            showgrid=True,
            gridcolor="rgba(148,163,184,0.25)",
            zeroline=False,
            rangeslider=dict(visible=enable_range_slider),
        ),
        yaxis=dict(
            title="Robot",
            categoryorder="array",
            categoryarray=robots,
            showgrid=True,
            gridcolor="rgba(148,163,184,0.18)",
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

    st.caption(
        "Bars are concrete events. The y-axis is robot_id. Segment membership is visible through bar colors. "
        "Transition metrics stay on pattern-edge hover so the process view remains inspectable."
    )
    config = {
        "scrollZoom": True,
        "displayModeBar": True,
        "displaylogo": False,
        "responsive": True,
        "toImageButtonOptions": {"format": "png", "filename": f"mission_{mission_id}_timeline", "height": height, "width": 1800, "scale": 2},
        "modeBarButtonsToAdd": ["drawline", "drawrect", "eraseshape"],
    }
    st.plotly_chart(fig, width="stretch", config=config)

    html = pio.to_html(fig, full_html=True, include_plotlyjs=True, config=config)
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
                    "start_s": round(event["start_s"], 3),
                    "duration_s": round(event["duration_s"], 3),
                }
                for event in events
            ]),
            width="stretch",
            hide_index=True,
        )

    with st.expander("Pattern overlays anchored to this timeline", expanded=False):
        rows: List[Dict[str, Any]] = []
        for transition in transitions:
            rows.append({
                "kind": "event-link",
                "pattern": transition["pattern_name"],
                "from_event": transition["from_event_id"],
                "to_event": transition["to_event_id"],
                "label": transition["label"],
            })
        rows.extend(structural_rows)
        if rows:
            st.dataframe(table_safe_rows(rows), width="stretch", hide_index=True)
        else:
            st.info("No selected pattern occurrence could be anchored to this mission timeline.")
