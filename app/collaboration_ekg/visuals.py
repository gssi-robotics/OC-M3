from __future__ import annotations

from typing import Any, Dict, List

from collaboration.collaboration_utils import format_seconds

from .data import PATTERN_COLORS


CLASS_COLOR = "#111111"
CONTEXT_COLOR = "#B9B9B9"


def _graphviz() -> Any:
    try:
        import graphviz
    except ImportError as exc:
        raise ImportError("Install the `graphviz` Python package and executable to render this inspector.") from exc
    return graphviz


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
        graph_attr={"rankdir": "LR", "bgcolor": "white", "splines": "polyline"},
    )
    dot.attr("node", shape="box", style="rounded,filled", fillcolor="white", fontname="Helvetica")
    dot.attr("edge", fontname="Helvetica", arrowsize="0.7")
    color = PATTERN_COLORS[pattern]
    if pattern == "Robot handover":
        r1, r2 = occurrence.get("from_robot", "?"), occurrence.get("to_robot", "?")
        e1, e2 = str(occurrence.get("from_event")), str(occurrence.get("to_event"))
        dot.node(f"lane1_{index}", label=f"Robot {r1}", shape="plaintext")
        dot.node(f"lane2_{index}", label=f"Robot {r2}", shape="plaintext")
        dot.node(e1, label=str(occurrence.get("from_activity")))
        dot.node(e2, label=str(occurrence.get("to_activity")))
        dot.edge(f"lane1_{index}", e1, style="dashed", color=CONTEXT_COLOR)
        dot.edge(e1, e2, label=f"handover\n{format_seconds(occurrence.get('duration_seconds'))}", color=color, fontcolor=color, penwidth="3")
        dot.edge(f"lane2_{index}", e2, style="dashed", color=CONTEXT_COLOR)
    elif pattern == "Objective switch":
        robot = occurrence.get("robot_id", "?")
        e1, e2 = str(occurrence.get("from_event")), str(occurrence.get("to_event"))
        dot.node(f"lane_{index}", label=f"Robot {robot}", shape="plaintext")
        dot.node(e1, label=f"{occurrence.get('from_activity')}\n{occurrence.get('from_objective')}")
        dot.node(e2, label=f"{occurrence.get('to_activity')}\n{occurrence.get('to_objective')}")
        dot.edge(f"lane_{index}", e1, style="dashed", color=CONTEXT_COLOR)
        dot.edge(e1, e2, label=f"objective switch\n{format_seconds(occurrence.get('duration_seconds'))}", color=color, fontcolor=color, penwidth="3")
    elif pattern == "Capability-driven return":
        e1 = str(occurrence.get("from_event"))
        e2 = str(occurrence.get("intermediate_event"))
        e3 = str(occurrence.get("return_event"))
        dot.node(e1, label=f"{occurrence.get('from_activity')}\nR:{occurrence.get('returning_robot')}")
        dot.node(e2, label=f"{occurrence.get('intermediate_activity')}\nR:{occurrence.get('intermediate_robot')}\ncap:{occurrence.get('capability')}")
        dot.node(e3, label=f"{occurrence.get('return_activity')}\nR:{occurrence.get('returning_robot')}")
        dot.edge(e1, e2, color=color, penwidth="3")
        dot.edge(e2, e3, label=f"return\n{format_seconds(occurrence.get('duration_seconds'))}", color=color, fontcolor=color, penwidth="3")
    elif pattern == "Co-participation":
        activities = occurrence.get("activities") or []
        robots = occurrence.get("robots") or []
        previous = None
        dot.node(f"team_{index}", label="Team: " + ", ".join(map(str, robots)), shape="plaintext")
        for pos, activity in enumerate(activities):
            node_id = f"cp_{index}_{pos}"
            dot.node(node_id, label=str(activity))
            if previous is None:
                dot.edge(f"team_{index}", node_id, style="dashed", color=CONTEXT_COLOR)
            else:
                dot.edge(previous, node_id, color=color)
            previous = node_id
    else:
        left = f"left_{index}"
        right = f"right_{index}"
        dot.node(left, label=f"{occurrence.get('left_objective')}\n{occurrence.get('start1')} -> {occurrence.get('end1')}")
        dot.node(right, label=f"{occurrence.get('right_objective')}\n{occurrence.get('start2')} -> {occurrence.get('end2')}")
        dot.edge(
            left,
            right,
            label=f"parallel overlap\n{format_seconds(occurrence.get('duration_seconds'))}",
            dir="none",
            style="dotted",
            color=color,
            fontcolor=color,
            penwidth="3",
        )
    return dot.source
