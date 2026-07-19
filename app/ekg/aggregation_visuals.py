from __future__ import annotations

from typing import Any, Dict, List

import streamlit as st


CLASS_COLOR = "#8EC5FC"
PERSPECTIVE_COLORS = [
    "#D1495B",
    "#2E86AB",
    "#3C8D40",
    "#8E6C8A",
    "#F18F01",
    "#0B6E4F",
    "#6C5CE7",
    "#C44536",
]


def _tooltip(details: Dict[str, Any], count: int) -> str:
    ordered = sorted((str(key), value) for key, value in (details or {}).items())
    lines = [f"{key}: {value}" for key, value in ordered]
    if not any(key == "Count" for key, _ in ordered):
        lines.append(f"Count: {count}")
    return "\n".join(lines)


def perspective_colors(rows: List[Dict[str, Any]]) -> Dict[str, str]:
    perspectives = sorted({str(row.get("perspective") or "DF") for row in rows})
    return {
        perspective: PERSPECTIVE_COLORS[index % len(PERSPECTIVE_COLORS)]
        for index, perspective in enumerate(perspectives)
    }


def build_graphviz(
    rows: List[Dict[str, Any]],
    show_time: bool,
    color_by_perspective: Dict[str, str],
) -> str:
    try:
        import graphviz
    except ImportError as exc:
        raise ImportError(
            "Install the `graphviz` Python package and the Graphviz executable."
        ) from exc

    dot = graphviz.Digraph(
        "aggregated_ekg",
        graph_attr={
            "rankdir": "LR",
            "bgcolor": "white",
            "overlap": "false",
        },
    )
    dot.attr(
        "node",
        shape="box",
        style="rounded,filled",
        fillcolor=CLASS_COLOR,
        fontname="Helvetica",
    )
    dot.attr("edge", fontname="Helvetica", arrowsize="0.8")

    nodes: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        nodes[str(row["source_id"])] = {
            "activity": str(row.get("source_activity") or "n/a"),
            "details": dict(row.get("source_details") or {}),
            "count": int(row.get("source_count") or 0),
        }
        nodes[str(row["target_id"])] = {
            "activity": str(row.get("target_activity") or "n/a"),
            "details": dict(row.get("target_details") or {}),
            "count": int(row.get("target_count") or 0),
        }

    for node_id, node in nodes.items():
        dot.node(
            node_id,
            label=node["activity"],
            tooltip=_tooltip(node["details"], node["count"]),
        )

    for row in rows:
        perspective = str(row.get("perspective") or "DF")
        color = color_by_perspective.get(perspective, "#4A4A4A")
        label = f"{perspective} | n={row['frequency']}"
        if show_time and row.get("avg_transition_seconds") is not None:
            label += f"\navg={float(row['avg_transition_seconds']):.2f}s"
        dot.edge(
            str(row["source_id"]),
            str(row["target_id"]),
            label=label,
            color=color,
            fontcolor=color,
            penwidth=str(1.0 + min(float(row["frequency"]), 20.0) / 4.0),
            tooltip=(
                f"Perspective: {perspective}\n"
                f"Frequency: {row['frequency']}\n"
                f"Average transition: {row.get('avg_transition_seconds')} seconds"
            ),
        )

    return dot.source


def render_perspective_legend(color_by_perspective: Dict[str, str]) -> None:
    """Render a compact perspective legend outside the Graphviz canvas."""
    if not color_by_perspective:
        return

    chips = []
    for perspective, color in color_by_perspective.items():
        chips.append(
            '<span style="display:inline-flex;align-items:center;gap:0.4rem;'
            'padding:0.25rem 0.55rem;margin:0.15rem 0.25rem 0.15rem 0;'
            'border:1px solid #dddddd;border-radius:999px;background:#ffffff;'
            'font-size:0.88rem;">'
            f'<span style="width:0.8rem;height:0.22rem;background:{color};'
            'display:inline-block;border-radius:2px;"></span>'
            f'{perspective}</span>'
        )

    st.markdown(
        '<div style="margin:0.25rem 0 0.8rem 0;">' + ''.join(chips) + '</div>',
        unsafe_allow_html=True,
    )
