from __future__ import annotations

from typing import Any, Dict, List, Optional

import streamlit as st

from collaboration.collaboration_utils import table_safe_rows


CLASS_COLOR = "#E4E4E4"
PERSPECTIVE_COLORS = [
    "#6C5CE7",
    "#E2641C",
    "#1FA5DE",
    "#3C8D40",
    "#D1495B",
    "#8E6C8A",
    "#F18F01",
    "#0B6E4F",
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
    start_rows: List[Dict[str, Any]],
    show_time: bool,
    color_by_perspective: Dict[str, str],
    rank_direction: str = "TB",
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
            "rankdir": rank_direction,
            "bgcolor": "white",
            "overlap": "false",
            "splines": "spline",
            "concentrate": "false",
            "ordering": "out",
            "outputorder": "edgesfirst",
            "nodesep": "0.65",
            "ranksep": "1.15",
            "pad": "0.3",
            "newrank": "true",
        },
    )
    dot.attr(
        "node",
        shape="box",
        style="rounded,filled",
        fillcolor=CLASS_COLOR,
        fontname="Helvetica",
    )
    dot.attr("edge", fontname="Helvetica", fontsize="9", arrowsize="0.7")

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

    for row in start_rows:
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

    start_perspectives = sorted({
        str(row.get("perspective") or "DF") for row in start_rows
    })
    with dot.subgraph(name="start_rank") as start_rank:
        start_rank.attr(rank="source")
        for perspective in start_perspectives:
            color = color_by_perspective.get(perspective, "#4A4A4A")
            start_rank.node(
                f"__start__{perspective}",
                label=perspective,
                shape="ellipse",
                style="filled",
                fillcolor=color,
                color=color,
                fontcolor="white",
                tooltip=f"{perspective} object perspective start",
            )

    for row in start_rows:
        perspective = str(row.get("perspective") or "DF")
        target_id = str(row["target_id"])
        dot.edge(
            f"__start__{perspective}",
            target_id,
            xlabel=f"start | n={row['frequency']}",
            color="#8A8A8A",
            fontcolor="#6F6F6F",
            style="dashed",
            penwidth="1.4",
            constraint="true",
            minlen="1",
            weight="4",
            tooltip=(
                f"Perspective: {perspective}\n"
                f"Start occurrences: {row['frequency']}"
            ),
        )

    for row in rows:
        perspective = str(row.get("perspective") or "DF")
        color = color_by_perspective.get(perspective, "#4A4A4A")
        label = f"{perspective} | n={row['frequency']}"
        avg_seconds = row.get("avg_transition_seconds")
        if avg_seconds is None:
            avg_seconds = row.get("avg_seconds")
        if show_time and avg_seconds is not None:
            label += f"\navg={float(avg_seconds):.2f}s"
        dot.edge(
            str(row["source_id"]),
            str(row["target_id"]),
            xlabel=label,
            color=color,
            fontcolor=color,
            penwidth=str(0.9 + min(float(row["frequency"]), 20.0) / 12.0),
            tooltip=(
                f"Perspective: {perspective}\n"
                f"Frequency: {row['frequency']}\n"
                f"Average transition: {avg_seconds} seconds"
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


def render_class_dfg_panel(
    rows: List[Dict[str, Any]],
    start_rows: Optional[List[Dict[str, Any]]] = None,
    *,
    show_time: bool,
    show_start_nodes: bool = True,
    rank_direction: str = "TB",
    key_prefix: str,
    empty_message: str,
    detail_expander_label: str = "DF_C details",
) -> None:
    start_rows = start_rows or []
    if not rows and not start_rows:
        st.info(empty_message)
        return

    perspectives = sorted({
        str(row.get("perspective") or "DF")
        for row in [*rows, *start_rows]
    })
    selected_perspectives = st.multiselect(
        "Visible perspectives",
        options=perspectives,
        default=perspectives,
        help="Hide or show DF_C edges by perspective without changing Neo4j.",
        key=f"{key_prefix}_visible_perspectives",
    )

    filtered_rows = [
        row for row in rows if str(row.get("perspective") or "DF") in selected_perspectives
    ]
    filtered_start_rows = (
        [
            row
            for row in start_rows
            if str(row.get("perspective") or "DF") in selected_perspectives
        ]
        if show_start_nodes
        else []
    )
    color_by_perspective = perspective_colors([*rows, *start_rows])
    visible_colors = {
        perspective: color_by_perspective[perspective]
        for perspective in selected_perspectives
        if perspective in color_by_perspective
    }

    st.caption(
        f"Showing {len(filtered_start_rows)} start and {len(filtered_rows)} directly-follows edges across "
        f"{len(selected_perspectives)} perspective(s)."
    )
    render_perspective_legend(visible_colors)

    if filtered_rows or filtered_start_rows:
        st.graphviz_chart(
            build_graphviz(
                filtered_rows,
                filtered_start_rows,
                show_time,
                color_by_perspective,
                rank_direction,
            ),
            width="stretch",
            height=900,
        )
    else:
        st.info("Select at least one perspective to display its edges.")

    with st.expander(detail_expander_label):
        st.dataframe(table_safe_rows(filtered_rows), width="stretch", hide_index=True)

    with st.expander("Start-edge details"):
        st.dataframe(table_safe_rows(filtered_start_rows), width="stretch", hide_index=True)
