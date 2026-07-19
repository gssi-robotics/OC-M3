from __future__ import annotations

import base64
import json
import math
from collections import defaultdict
from typing import Any, Dict, List, Mapping, Optional, Tuple

import streamlit as st
import streamlit.components.v1 as components

from .collaboration_data import (
    build_timeline_summary,
    extract_multi_pattern_transitions,
    extract_structural_highlights,
    fetch_mission_events,
    fetch_mission_ids,
    mission_span,
    prepare_events_for_timeline,
    relative_seconds,
    run_pattern_query,
    segment_extents,
)
from .collaboration_utils import (
    format_seconds,
    humanize_name,
    normalize_value,
    pattern_color_map,
    pattern_edge_hover_html,
    pattern_short,
    segment_color_map,
    table_safe_rows,
)
from .collaboration_visuals import render_dashboard_cards

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

def prepare_events_for_multi_mission_timeline(
    mission_events_by_id: Mapping[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Prepare events for an all-missions overview.

    The x-axis is global: seconds from the first event among all selected
    missions. Each event keeps its mission_id, robot_id, and segment_id.
    """
    raw_events: List[Dict[str, Any]] = []
    for mission_id, mission_events in mission_events_by_id.items():
        for event in mission_events:
            if not isinstance(event.get("start_ms"), (int, float)) or not isinstance(event.get("end_ms"), (int, float)):
                continue
            item = event.copy()
            item["mission_id"] = str(mission_id)
            item["robot_id"] = str(item.get("robot_id") or "unassigned")
            item["segment_id"] = str(item.get("segment_id") or "")
            raw_events.append(item)

    if not raw_events:
        return []

    base_ms = min(float(event["start_ms"]) for event in raw_events)
    raw_events.sort(key=lambda item: (float(item["start_ms"]), float(item["end_ms"]), str(item.get("event_id", ""))))

    for idx, event in enumerate(raw_events, start=1):
        event["seq"] = idx
        event["start_s"] = relative_seconds(float(event["start_ms"]), base_ms)
        event["end_s"] = relative_seconds(float(event["end_ms"]), base_ms)
        event["duration_s"] = max(0.05, float(event["end_s"]) - float(event["start_s"]))
        event["activity_short"] = str(event.get("activity") or "")[:28]
    return raw_events

def prepare_mission_events_for_detail(mission_id: str, mission_events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Prepare one mission with mission-relative time and explicit mission_id."""
    events = prepare_events_for_timeline(mission_events)
    for event in events:
        event["mission_id"] = str(mission_id)
        event["robot_id"] = str(event.get("robot_id") or "unassigned")
        event["segment_id"] = str(event.get("segment_id") or "")
    return events

def _edge_style(pattern_name: str) -> Dict[str, Any]:
    short = pattern_short(pattern_name)
    dash_map = {"HO": "solid", "SW": "dash", "CR": "dot"}
    symbol_map = {"HO": "circle", "SW": "diamond", "CR": "square"}
    return {
        "dash": dash_map.get(short, "solid"),
        "symbol": symbol_map.get(short, "circle"),
        "label": short,
    }

def _is_event_link_short(short: str) -> bool:
    return short in {"HO", "SW", "CR"}

def _is_structural_short(short: str) -> bool:
    return short in {"CP", "PC", "SYNC"}

def _item_visible(short: str, emphasis_mode: str) -> bool:
    if emphasis_mode == "Structural patterns only":
        return _is_structural_short(short)
    if emphasis_mode == "Event-to-event patterns only":
        return _is_event_link_short(short)
    return True

def _opacity_for_kind(short: str, emphasis_mode: str, base: float = 1.0) -> float:
    if emphasis_mode == "Balanced":
        return base
    if emphasis_mode == "Structural patterns only":
        return base if _is_structural_short(short) else 0.0
    if emphasis_mode == "Event-to-event patterns only":
        return base if _is_event_link_short(short) else 0.0
    if emphasis_mode == "Highlight co-participation":
        if short == "CP":
            return base
        if short in {"PC", "SYNC"}:
            return min(base, 0.45)
        return min(base, 0.25)
    if emphasis_mode == "Highlight parallel collaboration":
        if short == "PC":
            return base
        if short in {"CP", "SYNC"}:
            return min(base, 0.45)
        return min(base, 0.25)
    return base

def _safe_dom_id(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value)[:80] or "timeline"


def _safe_key_text(value: Any, max_len: int = 80) -> str:
    text = str(value).strip().replace("/", "_").replace("\\", "_").replace(" ", "_")
    cleaned = "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in text)
    return cleaned[:max_len] or "timeline"


def render_open_html_button(html: str, button_label: str = "Open timeline in new browser tab", unique_id: str = "timeline") -> None:
    """Render a browser-side button that opens Plotly HTML in a new tab."""
    encoded = base64.b64encode(html.encode("utf-8")).decode("ascii")
    element_id = f"open-timeline-tab-{_safe_dom_id(unique_id)}"
    button_html = f"""
    <div style="margin: 0.25rem 0 1rem 0;">
      <button id="{element_id}" style="
          background:#2563eb;
          color:white;
          border:0;
          border-radius:0.55rem;
          padding:0.65rem 1rem;
          font-weight:700;
          cursor:pointer;
          box-shadow:0 4px 12px rgba(37,99,235,0.25);
      ">{button_label}</button>
      <span style="margin-left:0.75rem;color:#64748b;font-size:0.9rem;">
        Opens a standalone interactive Plotly page for zooming and full-screen inspection.
      </span>
    </div>
    <script>
    const htmlBase64_{_safe_dom_id(unique_id)} = "{encoded}";
    document.getElementById("{element_id}").onclick = function() {{
        const html = atob(htmlBase64_{_safe_dom_id(unique_id)});
        const blob = new Blob([html], {{type: "text/html"}});
        const url = URL.createObjectURL(blob);
        window.open(url, "_blank", "noopener,noreferrer");
        setTimeout(function() {{ URL.revokeObjectURL(url); }}, 60000);
    }};
    </script>
    """
    components.html(button_html, height=74, scrolling=False)

def _mission_time_bounds_from_raw(mission_events: List[Dict[str, Any]], base_ms: float) -> Tuple[float, float]:
    valid = [event for event in mission_events if isinstance(event.get("start_ms"), (int, float)) and isinstance(event.get("end_ms"), (int, float))]
    if not valid:
        return 0.0, 0.05
    return (
        relative_seconds(min(float(event["start_ms"]) for event in valid), base_ms),
        relative_seconds(max(float(event["end_ms"]) for event in valid), base_ms),
    )

def _all_raw_events(mission_events_by_id: Mapping[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    return [
        event
        for mission_events in mission_events_by_id.values()
        for event in mission_events
        if isinstance(event.get("start_ms"), (int, float)) and isinstance(event.get("end_ms"), (int, float))
    ]

def _overview_base_ms(mission_events_by_id: Mapping[str, List[Dict[str, Any]]]) -> float:
    raw_events = _all_raw_events(mission_events_by_id)
    if not raw_events:
        return 0.0
    return min(float(event["start_ms"]) for event in raw_events)

def _overview_x_range(mission_events_by_id: Mapping[str, List[Dict[str, Any]]]) -> Tuple[float, float]:
    raw_events = _all_raw_events(mission_events_by_id)
    if not raw_events:
        return 0.0, 1.0
    base_ms = min(float(event["start_ms"]) for event in raw_events)
    max_s = relative_seconds(max(float(event["end_ms"]) for event in raw_events), base_ms)
    return 0.0, max(1.0, max_s * 1.08)

def _structural_badges_for_mission(
    mission_id: str,
    detail_events: List[Dict[str, Any]],
    raw_events: List[Dict[str, Any]],
    structural_highlights: List[Dict[str, Any]],
    base_ms: float,
) -> List[Dict[str, Any]]:
    """Convert CP/PC/SYNC highlights into compact badge items for overview or objective context."""
    badges: List[Dict[str, Any]] = []
    raw_by_event = {str(event.get("event_id")): event for event in raw_events}
    extents = segment_extents(raw_events)
    mission_x0, mission_x1 = _mission_time_bounds_from_raw(raw_events, base_ms)

    for highlight in structural_highlights:
        row = highlight.get("row", {})
        pattern_name = str(highlight.get("pattern_name", ""))
        kind = str(highlight.get("kind", ""))
        if kind == "mission_team":
            x = (mission_x0 + mission_x1) / 2.0
            badges.append({
                "short": "CP",
                "pattern_name": pattern_name,
                "x": x,
                "row_label": f"Mission {mission_id}",
                "label": f"CP team={row.get('teamSize', '?')}",
                "hover": _hover_html(
                    "Co-participation",
                    [
                        ("mission", mission_id),
                        ("teamSize", row.get("teamSize")),
                        ("objectiveDuration", format_seconds(row.get("objectiveDuration"))),
                        ("avgEventsPerRobot", row.get("avgEventsPerRobot")),
                        ("maxEventsPerRobot", row.get("maxEventsPerRobot")),
                    ],
                    row,
                ),
            })
        elif kind == "segment_team":
            segment = str(highlight.get("segment", ""))
            ext = extents.get(segment)
            if ext:
                x0 = relative_seconds(float(ext["start_ms"]), base_ms)
                x1 = relative_seconds(float(ext["end_ms"]), base_ms)
                badges.append({
                    "short": "CP",
                    "pattern_name": pattern_name,
                    "x": (x0 + x1) / 2.0,
                    "row_label": f"Segment {segment}",
                    "label": f"CP team={row.get('teamSize', '?')}",
                    "hover": _hover_html(
                        "Co-participation",
                        [
                            ("mission", mission_id),
                            ("segment", segment),
                            ("teamSize", row.get("teamSize")),
                            ("objectiveDuration", format_seconds(row.get("objectiveDuration"))),
                            ("avgEventsPerRobot", row.get("avgEventsPerRobot")),
                            ("maxEventsPerRobot", row.get("maxEventsPerRobot")),
                        ],
                        row,
                    ),
                })
        elif kind == "parallel_segments":
            segment1 = str(highlight.get("segment1", ""))
            segment2 = str(highlight.get("segment2", ""))
            extent1 = extents.get(segment1)
            extent2 = extents.get(segment2)
            if extent1 and extent2:
                overlap_start = max(float(extent1["start_ms"]), float(extent2["start_ms"]))
                overlap_end = min(float(extent1["end_ms"]), float(extent2["end_ms"]))
                if overlap_start < overlap_end:
                    x = relative_seconds((overlap_start + overlap_end) / 2.0, base_ms)
                    badges.append({
                        "short": "PC",
                        "pattern_name": pattern_name,
                        "x": x,
                        "row_label": f"Segment {segment1}",
                        "label": f"PC {segment1} || {segment2}",
                        "hover": _hover_html(
                            "Parallel collaboration",
                            [
                                ("mission", mission_id),
                                ("segment1", segment1),
                                ("segment2", segment2),
                                ("overlapDuration", format_seconds(row.get("overlapDuration"))),
                                ("robotCompetition", row.get("robotCompetition")),
                            ],
                            row,
                        ),
                    })
        elif kind == "sync":
            downstream_id = str(highlight.get("downstream_event_id", ""))
            downstream = raw_by_event.get(downstream_id)
            if downstream:
                x = relative_seconds(float(downstream["start_ms"]), base_ms)
                sync_delay = format_seconds(row.get("syncDelay"))
                badges.append({
                    "short": "SYNC",
                    "pattern_name": pattern_name,
                    "x": x,
                    "row_label": f"Mission {mission_id}",
                    "label": f"SYNC {sync_delay}" if sync_delay else "SYNC",
                    "hover": _hover_html(
                        "Synchronization point",
                        [
                            ("mission", mission_id),
                            ("downstream_event", downstream_id),
                            ("downstream_activity", downstream.get("activity")),
                            ("syncDelay", sync_delay),
                            ("branchWait", format_seconds(row.get("branchWait"))),
                        ],
                        row,
                    ),
                })
    return badges

def _format_relative_time(seconds: Any) -> str:
    """Readable relative time label while keeping seconds recoverable."""
    if not isinstance(seconds, (int, float)) or math.isnan(float(seconds)):
        return ""
    value = float(seconds)
    sign = "-" if value < 0 else ""
    value = abs(value)
    total_seconds = int(round(value))
    if total_seconds < 60:
        return f"{sign}{total_seconds}s"
    if total_seconds < 3600:
        minutes = total_seconds // 60
        rem_seconds = total_seconds % 60
        return f"{sign}{minutes}m {rem_seconds:02d}s ({sign}{total_seconds}s)"
    hours = total_seconds // 3600
    rem = total_seconds % 3600
    minutes = rem // 60
    rem_seconds = rem % 60
    return f"{sign}{hours}h {minutes:02d}m {rem_seconds:02d}s ({sign}{total_seconds}s)"

def _nice_time_step(max_seconds: float, target_ticks: int = 8) -> float:
    if max_seconds <= 0:
        return 1.0
    candidates = [
        1, 2, 5, 10, 15, 30,
        60, 120, 300, 600, 900, 1200, 1800,
        3600, 7200, 10800, 14400, 21600, 43200,
        86400, 172800, 604800,
    ]
    ideal = max_seconds / max(1, target_ticks)
    for step in candidates:
        if step >= ideal:
            return float(step)
    return float(candidates[-1])

def _relative_time_ticks(x_range: Tuple[float, float], target_ticks: int = 8) -> Tuple[List[float], List[str]]:
    start, end = x_range
    if end <= start:
        return [0.0], ["0s"]
    step = _nice_time_step(end - start, target_ticks=target_ticks)
    ticks: List[float] = []
    value = math.ceil(start / step) * step
    while value <= end + 1e-9:
        ticks.append(float(value))
        value += step
    if 0.0 not in ticks and start <= 0 <= end:
        ticks.insert(0, 0.0)
    tick_text = [_format_relative_time(tick) for tick in ticks]
    return ticks, tick_text

def _robot_distribution_rows(
    mission_events_by_id: Mapping[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for mission_id, mission_events in mission_events_by_id.items():
        by_robot: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"active_time": 0.0, "events": 0})
        for event in mission_events:
            if not isinstance(event.get("start_ms"), (int, float)) or not isinstance(event.get("end_ms"), (int, float)):
                continue
            robot = str(event.get("robot_id") or "unassigned")
            duration_s = max(0.0, (float(event["end_ms"]) - float(event["start_ms"])) / 1000.0)
            by_robot[robot]["active_time"] += duration_s
            by_robot[robot]["events"] += 1
        mission_total = sum(stats["active_time"] for stats in by_robot.values())
        for robot, stats in sorted(by_robot.items()):
            active_time = float(stats["active_time"])
            rows.append({
                "mission_id": str(mission_id),
                "robot_id": robot,
                "active_time": active_time,
                "events": int(stats["events"]),
                "share": (active_time / mission_total) if mission_total > 0 else 0.0,
                "mission_robot_total": mission_total,
            })
    return rows

def render_all_missions_overview_plotly(
    mission_events_by_id: Mapping[str, List[Dict[str, Any]]],
    pattern_rows_by_name: Dict[str, List[Dict[str, Any]]],
    selected_patterns: List[str],
    large_view: bool,
) -> Any:
    """Clean all-missions overview: only mission duration bars on a normalized global time axis."""
    go, _ = _plotly_required()
    mission_ids = list(mission_events_by_id.keys())
    base_ms = _overview_base_ms(mission_events_by_id)
    x_range = _overview_x_range(mission_events_by_id)
    tickvals, ticktext = _relative_time_ticks(x_range)
    y_map = {mission_id: index for index, mission_id in enumerate(reversed(mission_ids))}
    fig = go.Figure()

    for mission_id, raw_events in mission_events_by_id.items():
        if not raw_events:
            continue
        y = y_map[mission_id]
        mission_x0, mission_x1 = _mission_time_bounds_from_raw(raw_events, base_ms)
        duration_s = max(0.0, mission_x1 - mission_x0)
        robots = sorted({str(event.get("robot_id") or "unassigned") for event in raw_events})
        event_count = len([event for event in raw_events if event.get("event_id") is not None])
        fig.add_trace(go.Bar(
            x=[max(0.05, duration_s)],
            base=[mission_x0],
            y=[y],
            orientation="h",
            width=0.62,
            marker=dict(color="#E5E7EB", opacity=0.95, line=dict(color="#475569", width=1.4)),
            name="Mission duration",
            legendgroup="mission_duration",
            showlegend=mission_id == mission_ids[0],
            customdata=[[
                _hover_html(
                    "Mission duration",
                    [
                        ("mission", mission_id),
                        ("start", _format_relative_time(mission_x0)),
                        ("end", _format_relative_time(mission_x1)),
                        ("duration", _format_relative_time(duration_s)),
                        ("duration_seconds", f"{duration_s:.3f}"),
                        ("robots", ", ".join(robots)),
                        ("events", event_count),
                    ],
                )
            ]],
            hovertemplate="%{customdata[0]}<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=[mission_x1],
            y=[y],
            mode="text",
            text=[f"  {_format_relative_time(duration_s)} · {len(robots)} robots"],
            textposition="middle right",
            textfont=dict(color="#334155", size=11),
            hoverinfo="skip",
            showlegend=False,
        ))

    fig.update_layout(
        title="All-missions overview: mission duration on normalized global time",
        barmode="overlay",
        dragmode="pan",
        height=max(340, min(1100, 110 + 50 * len(mission_ids))) if large_view else max(300, min(850, 95 + 44 * len(mission_ids))),
        margin=dict(l=145, r=150, t=60, b=70),
        xaxis=dict(
            title="Global relative time from first selected event",
            range=list(x_range),
            tickmode="array",
            tickvals=tickvals,
            ticktext=ticktext,
            showgrid=True,
            gridcolor="rgba(148,163,184,0.24)",
            zeroline=False,
        ),
        yaxis=dict(
            title="Mission",
            tickmode="array",
            tickvals=[y_map[mission_id] for mission_id in mission_ids],
            ticktext=[f"Mission {mission_id}" for mission_id in mission_ids],
            showgrid=True,
            gridcolor="rgba(148,163,184,0.14)",
        ),
        hovermode="closest",
        template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    return fig

def render_robot_activity_lanes_over_missions_plotly(
    mission_events_by_id: Mapping[str, List[Dict[str, Any]]],
    large_view: bool,
) -> Any:
    """Robot-lane event timeline for all selected missions.

    This replaces the previous stacked active-time distribution because stacked
    bars show contribution but hide concurrency. Here, each row is a
    mission/robot lane and each rectangle is one concrete event. Parallel robot
    work inside the same mission is visible as horizontally overlapping event
    bars on different robot lanes.
    """
    go, _ = _plotly_required()
    mission_ids = list(mission_events_by_id.keys())
    base_ms = _overview_base_ms(mission_events_by_id)
    x_range = _overview_x_range(mission_events_by_id)
    tickvals, ticktext = _relative_time_ticks(x_range)

    # Stable robot colors across all missions.
    all_robot_ids = sorted({
        str(event.get("robot_id") or "unassigned")
        for events in mission_events_by_id.values()
        for event in events
    })
    robot_colors = segment_color_map(all_robot_ids)

    # Build y lanes as Mission / Robot. Reversing the final labels gives a
    # natural top-to-bottom order in Plotly.
    lane_labels: List[str] = []
    lane_keys: List[Tuple[str, str]] = []
    for mission_id in mission_ids:
        robots = sorted({str(event.get("robot_id") or "unassigned") for event in mission_events_by_id.get(mission_id, [])})
        if not robots:
            robots = ["unassigned"]
        for robot_id in robots:
            lane_keys.append((str(mission_id), robot_id))
            lane_labels.append(f"M {mission_id} / R {robot_id}")

    y_map = {key: index for index, key in enumerate(reversed(lane_keys))}
    y_tickvals = [y_map[key] for key in lane_keys]
    y_ticktext = [f"M {mission_id} / R {robot_id}" for mission_id, robot_id in lane_keys]

    fig = go.Figure()

    # Add a light mission-start/mission-end reference line through the lanes.
    # These are thin guide lines, not colored background bands.
    for mission_id, raw_events in mission_events_by_id.items():
        if not raw_events:
            continue
        mission_x0, mission_x1 = _mission_time_bounds_from_raw(raw_events, base_ms)
        fig.add_vline(x=mission_x0, line=dict(color="rgba(100,116,139,0.25)", width=1, dash="dot"))
        fig.add_vline(x=mission_x1, line=dict(color="rgba(100,116,139,0.25)", width=1, dash="dot"))

    for robot_id in all_robot_ids:
        xs: List[float] = []
        bases: List[float] = []
        ys: List[int] = []
        customdata: List[List[str]] = []
        texts: List[str] = []
        for mission_id, raw_events in mission_events_by_id.items():
            for event in raw_events:
                if str(event.get("robot_id") or "unassigned") != robot_id:
                    continue
                if not isinstance(event.get("start_ms"), (int, float)) or not isinstance(event.get("end_ms"), (int, float)):
                    continue
                start_s = (float(event["start_ms"]) - base_ms) / 1000.0
                end_s = (float(event["end_ms"]) - base_ms) / 1000.0
                duration_s = max(0.05, end_s - start_s)
                lane_key = (str(mission_id), robot_id)
                if lane_key not in y_map:
                    continue
                xs.append(duration_s)
                bases.append(start_s)
                ys.append(y_map[lane_key])
                activity = str(event.get("activity") or event.get("event_id") or "event")
                texts.append(activity[:18])
                customdata.append([
                    _hover_html(
                        "Robot event in mission",
                        [
                            ("mission", mission_id),
                            ("robot", robot_id),
                            ("event_id", event.get("event_id")),
                            ("activity", activity),
                            ("segment", event.get("segment_id") or "mission-level"),
                            ("start_global", _format_relative_time(start_s)),
                            ("end_global", _format_relative_time(end_s)),
                            ("duration", _format_relative_time(duration_s)),
                            ("duration_seconds", f"{duration_s:.3f}"),
                        ],
                    )
                ])
        if xs:
            fig.add_trace(go.Bar(
                name=f"Robot {robot_id}",
                x=xs,
                base=bases,
                y=ys,
                orientation="h",
                width=0.58,
                marker=dict(
                    color=robot_colors.get(robot_id, "#94A3B8"),
                    opacity=0.82,
                    line=dict(color="#0F172A", width=0.35),
                ),
                text=texts,
                textposition="inside",
                insidetextanchor="middle",
                textfont=dict(size=9, color="white"),
                customdata=customdata,
                hovertemplate="%{customdata[0]}<extra></extra>",
            ))

    fig.update_layout(
        title="Robot activity over missions: concurrency-visible event lanes",
        barmode="overlay",
        dragmode="pan",
        height=max(420, min(1800, 120 + 42 * len(lane_keys))) if large_view else max(360, min(1200, 105 + 34 * len(lane_keys))),
        margin=dict(l=165, r=40, t=60, b=70),
        xaxis=dict(
            title="Global relative time from first selected event",
            range=list(x_range),
            tickmode="array",
            tickvals=tickvals,
            ticktext=ticktext,
            showgrid=True,
            gridcolor="rgba(148,163,184,0.24)",
            zeroline=False,
        ),
        yaxis=dict(
            title="Mission / Robot",
            tickmode="array",
            tickvals=y_tickvals,
            ticktext=y_ticktext,
            showgrid=True,
            gridcolor="rgba(148,163,184,0.14)",
        ),
        hovermode="closest",
        template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    return fig

def add_structural_pattern_overlays_to_robot_timeline(
    fig: Any,
    events: List[Dict[str, Any]],
    structural_highlights: List[Dict[str, Any]],
    selected_patterns: List[str],
    emphasis_mode: str,
    y_badge: float,
) -> List[Dict[str, Any]]:
    """Draw only synchronization points on the robot timeline."""
    go, _ = _plotly_required()
    if not events or not structural_highlights:
        return []

    by_event = _event_lookup(events)
    colors = pattern_color_map(selected_patterns + [str(item.get("pattern_name", "")) for item in structural_highlights])
    overlay_rows: List[Dict[str, Any]] = []
    shown: set[str] = set()
    badge_offsets: Dict[str, int] = defaultdict(int)

    def add_badge(x: float, label: str, color: str, hover: str, pattern_name: str) -> None:
        offset = badge_offsets["SYNC"]
        badge_offsets["SYNC"] += 1
        y = y_badge + (offset % 4) * 0.18
        fig.add_trace(go.Scatter(
            x=[x],
            y=[y],
            mode="markers+text",
            name=humanize_name(pattern_name),
            legendgroup=pattern_name,
            showlegend=pattern_name not in shown,
            marker=dict(color="white", size=15, symbol="square", line=dict(color=color, width=2)),
            text=[label],
            textposition="middle right",
            textfont=dict(color=color, size=10),
            customdata=[[hover]],
            hovertemplate="%{customdata[0]}<extra></extra>",
        ))
        shown.add(pattern_name)

    for highlight in structural_highlights:
        if str(highlight.get("kind", "")) != "sync":
            continue
        pattern_name = str(highlight.get("pattern_name", "sync_diagnostics"))
        if not _item_visible("SYNC", emphasis_mode):
            continue
        row = highlight.get("row", {})
        color = colors.get(pattern_name, "#D97706")
        downstream_id = str(highlight.get("downstream_event_id", ""))
        downstream = by_event.get(downstream_id)
        if not downstream:
            continue
        time = float(downstream["start_s"])
        sync_delay = format_seconds(row.get("syncDelay"))
        label = f"SYNC {sync_delay}" if sync_delay else "SYNC"
        hover = _hover_html(
            "Synchronization point",
            [
                ("mission", downstream.get("mission_id")),
                ("downstream_event", downstream_id),
                ("downstream_activity", downstream.get("activity")),
                ("syncDelay", sync_delay),
                ("branchWait", format_seconds(row.get("branchWait"))),
            ],
            row,
        )
        fig.add_vline(x=time, line_color=color, line_width=2.5, line_dash="dash")
        add_badge(time, label, color, hover, pattern_name)
        overlay_rows.append({"kind": "synchronization", "scope": downstream_id, "pattern": pattern_name, "label": label})

    return overlay_rows

def render_robot_event_timeline_plotly(
    mission_id: str,
    events: List[Dict[str, Any]],
    transitions: List[Dict[str, Any]],
    structural_highlights: List[Dict[str, Any]],
    selected_patterns: List[str],
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
    pattern_names_for_colors = list(selected_patterns)
    pattern_names_for_colors.extend(str(transition["pattern_name"]) for transition in transitions)
    edge_colors = pattern_color_map(pattern_names_for_colors)
    fig = go.Figure()

    event_opacity = 0.75 if emphasis_mode in {"Highlight co-participation", "Highlight parallel collaboration"} else 1.0
    by_segment: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_segment[str(event.get("segment_id") or "mission-level")].append(event)

    for segment_id, segment_events in by_segment.items():
        color = segment_colors.get(segment_id, "#CBD5E1")
        text = [event["activity_short"] if show_activity_labels and float(event["duration_s"]) >= 1.0 else "" for event in segment_events]
        fig.add_trace(go.Bar(
            name=f"Segment {segment_id}",
            y=[y_map[str(event["robot_id"])] for event in segment_events],
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
                    event.get("mission_id") or "",
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
                "mission_id: %{customdata[4]}<br>"
                "start: %{customdata[5]}<br>"
                "end: %{customdata[6]}<br>"
                "duration_s: %{customdata[7]:.3f}<extra></extra>"
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
            if not _is_event_link_short(short) or not _item_visible(short, emphasis_mode):
                continue
            left = by_id.get(str(transition["from_event_id"]))
            right = by_id.get(str(transition["to_event_id"]))
            if not left or not right:
                continue
            left_robot = str(left["robot_id"])
            right_robot = str(right["robot_id"])
            key = (
                left_robot,
                right_robot,
                round(float(left["end_s"]) * 10),
                round(float(right["start_s"]) * 10),
            )
            slot = overlap_slots[key]
            overlap_slots[key] += 1
            offset = (slot % 5 - 2) * 0.08
            left_y = float(y_map[left_robot]) + offset
            right_y = float(y_map[right_robot]) + offset
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

    add_structural_pattern_overlays_to_robot_timeline(
        fig=fig,
        events=events,
        structural_highlights=structural_highlights,
        selected_patterns=selected_patterns,
        emphasis_mode=emphasis_mode,
        y_badge=max(0.0, float(len(robots)) - 0.35),
    )

    fig.update_layout(
        title=f"Mission {mission_id}: Robot Event Timeline",
        barmode="overlay",
        dragmode="pan",
        bargap=0.25,
        height=max(600, min(1500, 125 + 78 * len(robots))) if large_view else max(460, min(1100, 105 + 68 * len(robots))),
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
            range=[-0.7, max(0.8, float(len(robots)) + 0.6)],
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

def render_mission_context_layer_plotly(
    mission_id: str,
    events: List[Dict[str, Any]],
    structural_highlights: List[Dict[str, Any]],
    segment_colors: Dict[str, str],
    x_range: Tuple[float, float],
    emphasis_mode: str,
    large_view: bool,
) -> Any:
    """Mission + segment context layer with compact CP/PC/SYNC badges, no background bands."""
    go, _ = _plotly_required()
    extents = segment_extents(events)
    segment_ids = sorted(extents)
    row_labels = [f"Mission {mission_id}"] + [f"Segment {segment_id}" for segment_id in segment_ids]
    y_map = {label: index for index, label in enumerate(reversed(row_labels))}
    fig = go.Figure()

    mission_x0, mission_x1 = _mission_bounds(events)
    fig.add_trace(go.Bar(
        x=[max(0.05, mission_x1 - mission_x0)],
        base=[mission_x0],
        y=[y_map[f"Mission {mission_id}"]],
        orientation="h",
        width=0.48,
        marker=dict(color="#E5E7EB", opacity=0.9, line=dict(color="#94A3B8", width=1.5)),
        name="Mission interval",
        showlegend=False,
        customdata=[[_hover_html("Mission interval", [("mission", mission_id), ("duration", format_seconds(mission_x1 - mission_x0))])]],
        hovertemplate="%{customdata[0]}<extra></extra>",
    ))

    for segment_id, ext in extents.items():
        x0 = relative_seconds(float(ext["start_ms"]), mission_span(events)[0])
        x1 = relative_seconds(float(ext["end_ms"]), mission_span(events)[0])
        color = segment_colors.get(segment_id, "#94A3B8")
        row_label = f"Segment {segment_id}"
        fig.add_trace(go.Bar(
            x=[max(0.05, x1 - x0)],
            base=[x0],
            y=[y_map[row_label]],
            orientation="h",
            width=0.48,
            marker=dict(color=color, opacity=0.95, line=dict(color=color, width=1)),
            name=row_label,
            showlegend=False,
            customdata=[[_hover_html("Segment interval", [("mission", mission_id), ("segment", segment_id), ("duration", format_seconds(x1 - x0))])]],
            hovertemplate="%{customdata[0]}<extra></extra>",
        ))

    # CP and PC as badges only; SYNC as a vertical marker.
    for highlight in structural_highlights:
        kind = str(highlight.get("kind", ""))
        row = highlight.get("row", {})
        pattern_name = str(highlight.get("pattern_name", ""))
        if kind == "mission_team":
            if not _item_visible("CP", emphasis_mode):
                continue
            x = (mission_x0 + mission_x1) / 2.0
            y = y_map[f"Mission {mission_id}"] + 0.23
            hover = _hover_html("Co-participation", [("mission", mission_id), ("teamSize", row.get("teamSize")), ("objectiveDuration", format_seconds(row.get("objectiveDuration")))], row)
            fig.add_trace(go.Scatter(x=[x], y=[y], mode="markers+text", marker=dict(color="white", size=13, symbol="square", line=dict(color="#7C3AED", width=2)), text=[f"CP team={row.get('teamSize', '?')}"], textposition="middle right", textfont=dict(color="#7C3AED", size=9), customdata=[[hover]], hovertemplate="%{customdata[0]}<extra></extra>", showlegend=False))
        elif kind == "segment_team":
            if not _item_visible("CP", emphasis_mode):
                continue
            segment_id = str(highlight.get("segment", ""))
            ext = extents.get(segment_id)
            if not ext:
                continue
            x0 = relative_seconds(float(ext["start_ms"]), mission_span(events)[0])
            x1 = relative_seconds(float(ext["end_ms"]), mission_span(events)[0])
            row_label = f"Segment {segment_id}"
            hover = _hover_html("Co-participation", [("segment", segment_id), ("teamSize", row.get("teamSize")), ("objectiveDuration", format_seconds(row.get("objectiveDuration"))), ("avgEventsPerRobot", row.get("avgEventsPerRobot")), ("maxEventsPerRobot", row.get("maxEventsPerRobot"))], row)
            fig.add_trace(go.Scatter(x=[(x0 + x1) / 2.0], y=[y_map[row_label] + 0.23], mode="markers+text", marker=dict(color="white", size=13, symbol="square", line=dict(color="#7C3AED", width=2)), text=[f"CP team={row.get('teamSize', '?')}"], textposition="middle right", textfont=dict(color="#7C3AED", size=9), customdata=[[hover]], hovertemplate="%{customdata[0]}<extra></extra>", showlegend=False))
        elif kind == "parallel_segments":
            if not _item_visible("PC", emphasis_mode):
                continue
            segment1 = str(highlight.get("segment1", ""))
            segment2 = str(highlight.get("segment2", ""))
            extent1 = extents.get(segment1)
            extent2 = extents.get(segment2)
            if not extent1 or not extent2:
                continue
            overlap_start = max(float(extent1["start_ms"]), float(extent2["start_ms"]))
            overlap_end = min(float(extent1["end_ms"]), float(extent2["end_ms"]))
            if overlap_start >= overlap_end:
                continue
            x = relative_seconds((overlap_start + overlap_end) / 2.0, mission_span(events)[0])
            y1 = y_map.get(f"Segment {segment1}")
            y2 = y_map.get(f"Segment {segment2}")
            if y1 is None or y2 is None:
                continue
            hover = _hover_html("Parallel collaboration", [("segment1", segment1), ("segment2", segment2), ("overlapDuration", format_seconds(row.get("overlapDuration"))), ("robotCompetition", row.get("robotCompetition"))], row)
            fig.add_trace(go.Scatter(x=[x, x], y=[y1, y2], mode="lines", line=dict(color="#059669", width=2, dash="dot"), customdata=[[hover], [hover]], hovertemplate="%{customdata[0]}<extra></extra>", showlegend=False))
            fig.add_trace(go.Scatter(x=[x], y=[(float(y1) + float(y2)) / 2.0], mode="markers+text", marker=dict(color="white", size=14, symbol="diamond", line=dict(color="#059669", width=2)), text=[f"PC {segment1} || {segment2}"], textposition="middle right", textfont=dict(color="#059669", size=9), customdata=[[hover]], hovertemplate="%{customdata[0]}<extra></extra>", showlegend=False))
        elif kind == "sync":
            if not _item_visible("SYNC", emphasis_mode):
                continue
            downstream = _event_lookup(events).get(str(highlight.get("downstream_event_id", "")))
            if not downstream:
                continue
            time = float(downstream["start_s"])
            hover = _hover_html("Synchronization point", [("downstream_event", highlight.get("downstream_event_id")), ("downstream_activity", downstream.get("activity")), ("syncDelay", format_seconds(row.get("syncDelay"))), ("branchWait", format_seconds(row.get("branchWait")))], row)
            fig.add_vline(x=time, line_color="#D97706", line_width=2.5, line_dash="dash")
            fig.add_trace(go.Scatter(x=[time], y=[y_map[f"Mission {mission_id}"] + 0.25], mode="markers+text", marker=dict(color="white", size=13, symbol="square", line=dict(color="#D97706", width=2)), text=["SYNC"], textposition="middle right", textfont=dict(color="#D97706", size=9), customdata=[[hover]], hovertemplate="%{customdata[0]}<extra></extra>", showlegend=False))

    fig.update_layout(
        title=f"Mission {mission_id}: Mission and Segment Context",
        barmode="overlay",
        dragmode="pan",
        height=max(230, min(620, 110 + 48 * len(row_labels))) if large_view else max(200, min(520, 90 + 42 * len(row_labels))),
        margin=dict(l=140, r=35, t=55, b=35),
        xaxis=dict(title="", range=list(x_range), showgrid=True, gridcolor="rgba(148,163,184,0.18)", zeroline=False),
        yaxis=dict(title="Objective", tickmode="array", tickvals=[y_map[label] for label in row_labels], ticktext=row_labels, showgrid=False),
        template="plotly_white",
        hovermode="closest",
        showlegend=False,
    )
    return fig

def render_mission_detail_panel(
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
    """Render one mission as two coordinated plots: objective context + robot timeline."""
    _, pio = _plotly_required()
    events = prepare_mission_events_for_detail(mission_id, mission_events)
    if not events:
        st.info(f"Mission {mission_id} does not expose usable start/end timestamps.")
        return

    transitions = extract_multi_pattern_transitions(pattern_rows_by_name, events, selected_patterns)
    structural_highlights = extract_structural_highlights(pattern_rows_by_name, mission_id, events, selected_patterns)
    segment_ids = [str(event.get("segment_id") or "mission-level") for event in events]
    segment_colors = segment_color_map(segment_ids)
    segment_colors.setdefault("mission-level", "#CBD5E1")
    x_range = _mission_bounds(events)

    context_fig = render_mission_context_layer_plotly(
        mission_id=mission_id,
        events=events,
        structural_highlights=structural_highlights,
        segment_colors=segment_colors,
        x_range=x_range,
        emphasis_mode=emphasis_mode,
        large_view=large_view,
    )
    robot_fig = render_robot_event_timeline_plotly(
        mission_id=mission_id,
        events=events,
        transitions=transitions,
        structural_highlights=structural_highlights,
        selected_patterns=selected_patterns,
        segment_colors=segment_colors,
        x_range=x_range,
        emphasis_mode=emphasis_mode,
        show_activity_labels=show_activity_labels,
        show_df_backbone=show_df_backbone,
        large_view=large_view,
        enable_range_slider=enable_range_slider,
    )

    render_dashboard_cards([
        {"label": "Mission", "value": str(mission_id), "caption": "Detailed panel", "accent": "#2563EB"},
        {"label": "Events", "value": str(len(events)), "caption": "Concrete task executions", "accent": "#059669"},
        {"label": "Robots", "value": str(len({event['robot_id'] for event in events})), "caption": "Robot swimlanes", "accent": "#D97706"},
        {"label": "Pattern links", "value": str(len(transitions)), "caption": "HO, SW, CR edges", "accent": "#DC2626"},
    ])

    config = {
        "scrollZoom": True,
        "displayModeBar": True,
        "displaylogo": False,
        "responsive": True,
        "toImageButtonOptions": {
            "format": "png",
            "filename": f"mission_{mission_id}_collaboration_timeline",
            "height": int(robot_fig.layout.height or 900),
            "width": 2200,
            "scale": 2,
        },
        "modeBarButtonsToAdd": ["drawline", "drawrect", "eraseshape"],
    }

    html = (
        "<html><head><meta charset='utf-8'><title>Mission Collaboration Timeline</title>"
        "<style>body{font-family:Arial,sans-serif;background:#f8fafc;margin:0;}"
        ".wrap{padding:18px 24px 28px 24px;}"
        "h2{margin:0 0 6px 0;color:#0f172a;}"
        "p{margin:0 0 14px 0;color:#475569;}"
        "</style></head><body>"
        "<div class='wrap'>"
        f"<h2>Mission {mission_id}: Collaboration Timeline</h2>"
        "<p>Mission and segment context above; robot event timeline below. Use pan, zoom, autoscale, and image export from the Plotly toolbar.</p>"
        f"{pio.to_html(context_fig, full_html=False, include_plotlyjs='cdn', config=config)}"
        f"{pio.to_html(robot_fig, full_html=False, include_plotlyjs=False, config=config)}"
        "</div></body></html>"
    )

    st.markdown(f"#### Mission {mission_id}")
    render_open_html_button(html, button_label=f"Open Mission {mission_id} timeline in new browser tab", unique_id=f"mission_{mission_id}")
    st.plotly_chart(context_fig, width="stretch", config={"scrollZoom": True, "displaylogo": False, "responsive": True}, key=f"timeline_context_{_safe_key_text(mission_id)}")
    st.plotly_chart(robot_fig, width="stretch", config=config, key=f"timeline_robot_{_safe_key_text(mission_id)}")

    with st.expander(f"Mission {mission_id}: events and anchored patterns", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Events**")
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
        with c2:
            st.markdown("**Patterns**")
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
                st.info("No selected pattern occurrence could be anchored to this mission.")

def render_collaboration_timeline_page(
    mission_events_by_id: Mapping[str, List[Dict[str, Any]]],
    detail_mission_ids: List[str],
    pattern_rows_by_name: Dict[str, List[Dict[str, Any]]],
    selected_patterns: List[str],
    emphasis_mode: str,
    show_activity_labels: bool,
    show_df_backbone: bool,
    large_view: bool,
    enable_range_slider: bool,
) -> None:
    """Two-level collaboration timeline: all-missions overview + mission detail panels."""
    _, pio = _plotly_required()
    if not mission_events_by_id:
        st.info("Select at least one mission to draw the collaboration timeline.")
        return

    overview_fig = render_all_missions_overview_plotly(
        mission_events_by_id=mission_events_by_id,
        pattern_rows_by_name=pattern_rows_by_name,
        selected_patterns=selected_patterns,
        large_view=large_view,
    )
    robot_activity_lanes_fig = render_robot_activity_lanes_over_missions_plotly(
        mission_events_by_id=mission_events_by_id,
        large_view=large_view,
    )
    overview_config = {"scrollZoom": True, "displayModeBar": True, "displaylogo": False, "responsive": True}
    overview_html = (
        "<html><head><meta charset='utf-8'><title>All Missions Overview</title></head>"
        "<body style='font-family:Arial,sans-serif;background:#f8fafc;margin:0;padding:18px 24px;'>"
        "<h2>All-missions overview</h2>"
        "<p>Mission duration is plotted on a normalized global relative-time axis. The robot-lane chart shows concrete robot events per mission, so parallel robot work is visible as overlapping bars on different robot lanes.</p>"
        f"{pio.to_html(overview_fig, full_html=False, include_plotlyjs='cdn', config=overview_config)}"
        f"{pio.to_html(robot_activity_lanes_fig, full_html=False, include_plotlyjs=False, config=overview_config)}"
        "</body></html>"
    )

    render_dashboard_cards([
        {"label": "Overview missions", "value": str(len(mission_events_by_id)), "caption": "Compact mission rows", "accent": "#2563EB"},
        {"label": "Detail panels", "value": str(len(detail_mission_ids)), "caption": "Selected missions below", "accent": "#059669"},
        {"label": "Selected patterns", "value": str(len(selected_patterns)), "caption": "Markers, badges, and links", "accent": "#D97706"},
        {"label": "Encoding", "value": "2 views", "caption": "Mission duration + robot lanes", "accent": "#7C3AED"},
    ])

    st.markdown("### 1. All-missions overview")
    st.caption(
        "Each row is a mission. The first chart shows only the mission duration on a global relative-time axis. "
        "The second chart shows robot-event lanes per mission; parallel work is visible when events overlap horizontally across robot lanes of the same mission."
    )
    render_open_html_button(overview_html, button_label="Open all-missions overview in new browser tab", unique_id="all_missions_overview")
    st.plotly_chart(overview_fig, width="stretch", config=overview_config, key="timeline_all_missions_duration_overview")
    st.plotly_chart(robot_activity_lanes_fig, width="stretch", config=overview_config, key="timeline_all_missions_robot_activity_lanes")

    st.markdown("### 2. Mission detail panels")
    st.caption(
        "Each selected mission is shown separately. The first plot keeps mission and segment context; the second plot keeps robot_id as the y-axis."
    )
    if not detail_mission_ids:
        st.info("Select at least one mission in `Detailed mission panels` to inspect robot-level behavior.")
        return

    for mission_id in detail_mission_ids:
        mission_events = mission_events_by_id.get(str(mission_id), [])
        if not mission_events:
            st.warning(f"Mission {mission_id} is not available in the current overview selection.")
            continue
        render_mission_detail_panel(
            mission_id=str(mission_id),
            mission_events=mission_events,
            pattern_rows_by_name=pattern_rows_by_name,
            selected_patterns=selected_patterns,
            emphasis_mode=emphasis_mode,
            show_activity_labels=show_activity_labels,
            show_df_backbone=show_df_backbone,
            large_view=large_view,
            enable_range_slider=enable_range_slider,
        )

def render_timeline_tab(driver: Any, database: Optional[str], catalog: Dict[str, Dict[str, str]], log_name: Optional[str]) -> None:
    st.subheader("Collaboration Timeline")
    st.caption(
        "Two-level view: an all-missions overview for comparison, then separate mission-detail panels that keep mission, segment, and robot perspectives visible."
    )

    mission_ids = st.session_state.get("collab_mission_ids")
    if mission_ids is None:
        mission_ids = fetch_mission_ids(driver, database, log_name)
        st.session_state["collab_mission_ids"] = mission_ids

    if not mission_ids:
        st.info("No missions found for the current database/log filter.")
        return

    selected_overview_mission_ids = st.multiselect(
        "Missions in overview",
        mission_ids,
        default=mission_ids,
        key="collab_overview_missions_timeline",
        help="The overview is compact, so it can show all missions by default.",
    )
    if not selected_overview_mission_ids:
        st.info("Select at least one mission to draw the overview.")
        return

    default_detail = selected_overview_mission_ids[:1]
    detail_mission_ids = st.multiselect(
        "Detailed mission panels",
        selected_overview_mission_ids,
        default=default_detail,
        key="collab_detail_missions_timeline",
        help="Render selected missions as separate panels. This avoids flattening mission, robot, and segment into one crowded y-axis.",
    )

    timeline_pattern_names = list(catalog["Occurrences"].keys())
    if "sync_diagnostics_parallel_segments" in catalog.get("Diagnostics", {}):
        timeline_pattern_names.append("sync_diagnostics_parallel_segments")

    selected_timeline_patterns = st.multiselect(
        "Patterns to show",
        timeline_pattern_names,
        default=timeline_pattern_names,
        key="collab_selected_timeline_patterns",
        help="All pattern types can be shown. CP/PC appear as compact badges; SYNC appears as a vertical marker; HO/SW/CR appear as event links.",
    )

    c1, c2, c3, c4 = st.columns([1, 1, 1, 1.2])
    with c1:
        row_limit = st.slider("Rows per pattern", min_value=20, max_value=3000, value=1000, step=20, key="collab_timeline_pattern_limit")
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

    c5, c6 = st.columns([1, 1])
    with c5:
        large_view = st.checkbox("Large in-page view", value=True, key="collab_large_timeline")
    with c6:
        enable_range_slider = st.checkbox("Show x-axis range slider in robot timelines", value=True, key="collab_range_slider")

    mission_events_by_id: Dict[str, List[Dict[str, Any]]] = {
        str(mission_id): fetch_mission_events(driver, database, str(mission_id), log_name)
        for mission_id in selected_overview_mission_ids
    }

    pattern_rows_by_name: Dict[str, List[Dict[str, Any]]] = {}
    for pattern_name in selected_timeline_patterns:
        query = catalog["Occurrences"].get(pattern_name) or catalog.get("Diagnostics", {}).get(pattern_name)
        if query:
            pattern_rows_by_name[pattern_name] = run_pattern_query(driver, database, query, log_name, row_limit)

    try:
        render_collaboration_timeline_page(
            mission_events_by_id=mission_events_by_id,
            detail_mission_ids=[str(mission_id) for mission_id in detail_mission_ids],
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
