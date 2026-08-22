from __future__ import annotations

from typing import Any, Dict, List

from collaboration.collaboration_utils import format_seconds

from .data import PATTERN_COLORS


CLASS_COLOR = "#111111"
CONTEXT_COLOR = "#B9B9B9"
SURFACE_COLOR = "#F8FAFC"
LANE_COLOR = "#E2E8F0"
TEXT_MUTED = "#475569"


def _graphviz() -> Any:
    try:
        import graphviz
    except ImportError as exc:
        raise ImportError("Install the `graphviz` Python package and executable to render this inspector.") from exc
    return graphviz


def _event_label(title: Any, subtitle: Any = None, meta: Any = None) -> str:
    parts = [str(title or "event")]
    if subtitle:
        parts.append(str(subtitle))
    if meta:
        parts.append(str(meta))
    return "\n".join(parts)


def _lane_node(dot: Any, node_id: str, label: str) -> None:
    dot.node(
        node_id,
        label=label,
        shape="box",
        style="rounded,filled",
        fillcolor=LANE_COLOR,
        color="#CBD5E1",
        fontcolor="#0F172A",
        fontsize="11",
        margin="0.16,0.08",
    )


def _activity_node(dot: Any, node_id: str, label: str, color: str, tooltip: str = "") -> None:
    dot.node(
        node_id,
        label=label,
        tooltip=tooltip,
        shape="box",
        style="rounded,filled",
        fillcolor=SURFACE_COLOR,
        color=color,
        penwidth="1.8",
        fontcolor="#0F172A",
        fontsize="11",
        margin="0.18,0.1",
    )


def build_overview_graph(rows: List[Dict[str, Any]]) -> str:
    graphviz = _graphviz()
    dot = graphviz.Digraph(
        "overview",
        graph_attr={"rankdir": "LR", "bgcolor": "white", "splines": "spline"},
    )
    dot.attr("node", shape="box", style="filled", fillcolor=CLASS_COLOR, fontcolor="white", fontname="Helvetica")
    dot.attr("edge", fontname="Helvetica", arrowsize="0.8")
    nodes: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        nodes[str(row["source_id"])] = {
            "activity": row["source_activity"],
            "details": row.get("source_details") or {},
        }
        nodes[str(row["target_id"])] = {
            "activity": row["target_activity"],
            "details": row.get("target_details") or {},
        }
    for node_id, info in nodes.items():
        tooltip = "\n".join(f"{k}: {v}" for k, v in sorted(info["details"].items()))
        dot.node(node_id, label=str(info["activity"]), tooltip=tooltip)
    for row in rows:
        label = f"{row['perspective']} ({row['frequency']})"
        if row.get("avg_seconds") is not None:
            label += f"\n{format_seconds(row['avg_seconds'])}"
        dot.edge(str(row["source_id"]), str(row["target_id"]), label=label)
    return dot.source


def build_occurrence_lane_graph(pattern: str, occurrence: Dict[str, Any], index: int) -> str:
    graphviz = _graphviz()
    dot = graphviz.Digraph(
        f"occurrence_{index}",
        graph_attr={
            "rankdir": "LR",
            "bgcolor": "white",
            "splines": "ortho",
            "pad": "0.25",
            "nodesep": "0.55",
            "ranksep": "0.75",
        },
    )
    dot.attr("node", shape="box", style="rounded,filled", fillcolor="white", fontname="Helvetica")
    dot.attr("edge", fontname="Helvetica", arrowsize="0.75", color=TEXT_MUTED, penwidth="1.4")
    color = PATTERN_COLORS[pattern]
    if pattern == "Robot handover":
        r1, r2 = occurrence.get("from_robot", "?"), occurrence.get("to_robot", "?")
        e1, e2 = str(occurrence.get("from_event")), str(occurrence.get("to_event"))
        objective = occurrence.get("objective_id", "?")
        _lane_node(dot, f"lane1_{index}", f"Robot {r1}")
        _lane_node(dot, f"lane2_{index}", f"Robot {r2}")
        _activity_node(
            dot,
            e1,
            _event_label(occurrence.get("from_activity"), f"event {e1}", f"segment {occurrence.get('from_segment') or '?'}"),
            color,
        )
        _activity_node(
            dot,
            e2,
            _event_label(occurrence.get("to_activity"), f"event {e2}", f"segment {occurrence.get('to_segment') or '?'}"),
            color,
        )
        dot.edge(f"lane1_{index}", e1, style="dashed", color=CONTEXT_COLOR, arrowhead="none")
        dot.edge(
            e1,
            e2,
            label=f"Handover | objective {objective}\ntransition {format_seconds(occurrence.get('duration_seconds'))}",
            color=color,
            fontcolor=color,
            penwidth="3.6",
        )
        dot.edge(f"lane2_{index}", e2, style="dashed", color=CONTEXT_COLOR, arrowhead="none")
    elif pattern == "Objective switch":
        robot = occurrence.get("robot_id", "?")
        e1, e2 = str(occurrence.get("from_event")), str(occurrence.get("to_event"))
        _lane_node(dot, f"lane_{index}", f"Robot {robot}")
        _activity_node(
            dot,
            e1,
            _event_label(occurrence.get("from_activity"), f"objective {occurrence.get('from_objective')}"),
            color,
        )
        _activity_node(
            dot,
            e2,
            _event_label(occurrence.get("to_activity"), f"objective {occurrence.get('to_objective')}"),
            color,
        )
        dot.edge(f"lane_{index}", e1, style="dashed", color=CONTEXT_COLOR, arrowhead="none")
        dot.edge(
            e1,
            e2,
            label=f"Objective switch\ntransition {format_seconds(occurrence.get('duration_seconds'))}",
            color=color,
            fontcolor=color,
            penwidth="3.6",
        )
    elif pattern == "Capability-driven return":
        e1 = str(occurrence.get("from_event"))
        e2 = str(occurrence.get("intermediate_event"))
        e3 = str(occurrence.get("return_event"))
        returning_robot = occurrence.get("returning_robot")
        intermediate_robot = occurrence.get("intermediate_robot")
        capabilities = ", ".join(str(value) for value in occurrence.get("capabilities") or []) or "?"
        _lane_node(dot, f"return_lane_{index}", f"Returning robot {returning_robot}")
        _lane_node(dot, f"intermediate_lane_{index}", f"Intermediate robot {intermediate_robot}")
        _activity_node(
            dot,
            e1,
            _event_label(occurrence.get("from_activity"), f"event {e1}"),
            color,
        )
        _activity_node(
            dot,
            e2,
            _event_label(occurrence.get("intermediate_activity"), f"capabilities {capabilities}", f"event {e2}"),
            color,
        )
        _activity_node(
            dot,
            e3,
            _event_label(occurrence.get("return_activity"), f"event {e3}"),
            color,
        )
        dot.edge(f"return_lane_{index}", e1, style="dashed", color=CONTEXT_COLOR, arrowhead="none")
        dot.edge(f"intermediate_lane_{index}", e2, style="dashed", color=CONTEXT_COLOR, arrowhead="none")
        dot.edge(
            e1,
            e2,
            label="delegation",
            color=color,
            fontcolor=color,
            penwidth="3.2",
        )
        dot.edge(
            e2,
            e3,
            label=f"capability-driven return\ncycle time {format_seconds(occurrence.get('duration_seconds'))}",
            color=color,
            fontcolor=color,
            penwidth="3.8",
        )
    elif pattern == "Co-participation":
        activities = occurrence.get("activities") or []
        robots = occurrence.get("robots") or []
        previous = None
        _lane_node(dot, f"team_{index}", "Team " + " | ".join(map(str, robots)))
        for pos, activity in enumerate(activities):
            node_id = f"cp_{index}_{pos}"
            meta = None
            events = occurrence.get("events") or []
            if pos < len(events):
                meta = f"event {events[pos]}"
            _activity_node(dot, node_id, _event_label(activity, meta), color)
            if previous is None:
                dot.edge(f"team_{index}", node_id, style="dashed", color=CONTEXT_COLOR, arrowhead="none")
            else:
                dot.edge(previous, node_id, color=color, penwidth="2.8")
            previous = node_id
        if previous is not None:
            dot.attr(
                label=f"Co-participation | team size {occurrence.get('team_size', '?')} | duration {format_seconds(occurrence.get('duration_seconds'))}",
                labelloc="t",
                fontsize="12",
                fontcolor=color,
            )
    else:
        left = f"left_{index}"
        right = f"right_{index}"
        _activity_node(
            dot,
            left,
            _event_label(occurrence.get("left_objective"), f"{occurrence.get('start1')} -> {occurrence.get('end1')}"),
            color,
        )
        _activity_node(
            dot,
            right,
            _event_label(occurrence.get("right_objective"), f"{occurrence.get('start2')} -> {occurrence.get('end2')}"),
            color,
        )
        dot.edge(
            left,
            right,
            label=f"Parallel overlap\nwindow {format_seconds(occurrence.get('duration_seconds'))}",
            dir="none",
            style="dotted",
            color=color,
            fontcolor=color,
            penwidth="3.6",
        )
    return dot.source
