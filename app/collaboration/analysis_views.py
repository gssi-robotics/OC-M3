from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, List, Mapping, Optional, Tuple

import streamlit as st

from .collaboration_utils import (
    ALL_OPTION,
    format_seconds,
    humanize_name,
    node_id,
    normalize_value,
    segment_color_map,
    table_safe_rows,
)
from .timeline_views import _hover_html, _plotly_required
from .collaboration_visuals import render_dashboard_cards
from .collaboration_data import fetch_mission_events, fetch_mission_ids, mission_span, prepare_events_for_timeline, relative_seconds, run_pattern_query, segment_extents

def _session_kwargs(database: Optional[str]) -> Dict[str, str]:
    if database and str(database).strip():
        return {"database": str(database).strip()}
    return {}

def _run_cypher(driver: Any, database: Optional[str], query: str, parameters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    with driver.session(**_session_kwargs(database)) as session:
        result = session.run(query, parameters or {})
        return [normalize_value(record.data()) for record in result]

def _safe_number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            if math.isnan(float(value)):
                return default
        except Exception:
            pass
        return float(value)
    return default

def _node_id_list(values: Any) -> List[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        values = [values]
    result: List[str] = []
    for value in values:
        item = node_id(value)
        if item:
            result.append(item)
    return result

def _metric_value(row: Dict[str, Any], keys: List[str]) -> Optional[float]:
    for key in keys:
        value = row.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None

def _plot_table(rows: List[Dict[str, Any]], empty_message: str = "No data returned for the selected filters.") -> None:
    if rows:
        st.dataframe(table_safe_rows(rows), width="stretch", hide_index=True)
    else:
        st.info(empty_message)

def _duration_expr(event_var: str = "e") -> str:
    return (
        f"CASE WHEN {event_var}.start IS NOT NULL AND {event_var}.end IS NOT NULL "
        f"THEN toFloat(duration.inSeconds({event_var}.start, {event_var}.end).seconds) ELSE 0.0 END"
    )


def _collect_occurrence_rows(
    catalog: Dict[str, Dict[str, str]],
    driver: Any,
    database: Optional[str],
    log_name: Optional[str],
    prefixes: Tuple[str, ...],
    row_limit: int,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for name, query in catalog.get("Occurrences", {}).items():
        if name.startswith(prefixes):
            for row in run_pattern_query(driver, database, query, log_name, row_limit):
                row["_pattern_view"] = name
                rows.append(row)
    return rows

def fetch_robot_mission_rows(driver: Any, database: Optional[str], log_name: Optional[str]) -> List[Dict[str, Any]]:
    query = f"""
    MATCH (m:Entity {{type: 'Mission'}})<-[:CORR]-(e:Event)-[:CORR]->(r:Entity {{type: 'Robot'}})
    WHERE $log_name IS NULL OR e.Log = $log_name OR m.Log = $log_name OR r.Log = $log_name
    WITH r, m, collect(DISTINCT e) AS events, collect(DISTINCT coalesce(e.activity, toString(e.event_id))) AS activities
    RETURN
      toString(r.id) AS robot,
      toString(m.id) AS mission,
      size(events) AS eventCount,
      reduce(total = 0.0, ev IN events | total + {_duration_expr('ev')}) AS activeTime,
      activities[0..8] AS activities
    ORDER BY robot, mission
    """
    return _run_cypher(driver, database, query, {"log_name": log_name})

def fetch_mission_segment_rows(driver: Any, database: Optional[str], log_name: Optional[str]) -> List[Dict[str, Any]]:
    query = f"""
    MATCH (s:Entity {{type: 'Segment'}})-[:PART_OF]->(m:Entity {{type: 'Mission'}})
    WHERE $log_name IS NULL OR s.Log = $log_name OR m.Log = $log_name
    OPTIONAL MATCH (s)<-[:CORR]-(e:Event)
    WHERE e IS NULL OR $log_name IS NULL OR e.Log = $log_name
    WITH m, s, collect(DISTINCT e) AS maybeEvents
    WITH m, s, [ev IN maybeEvents WHERE ev IS NOT NULL] AS events
    OPTIONAL MATCH (s)<-[:CORR]-(:Event)-[:CORR]->(r:Entity {{type: 'Robot'}})
    WHERE $log_name IS NULL OR r.Log = $log_name
    WITH m, s, events, collect(DISTINCT r) AS maybeTeam
    WITH m, s, events, [ro IN maybeTeam WHERE ro IS NOT NULL] AS team
    RETURN
      toString(m.id) AS mission,
      toString(s.id) AS segment,
      size(events) AS eventCount,
      reduce(total = 0.0, ev IN events | total + {_duration_expr('ev')}) AS activeTime,
      size(team) AS teamSize,
      [ro IN team | toString(ro.id)][0..12] AS robots
    ORDER BY mission, segment
    """
    return _run_cypher(driver, database, query, {"log_name": log_name})

def fetch_robot_segment_rows(driver: Any, database: Optional[str], log_name: Optional[str]) -> List[Dict[str, Any]]:
    query = f"""
    MATCH (s:Entity {{type: 'Segment'}})<-[:CORR]-(e:Event)-[:CORR]->(r:Entity {{type: 'Robot'}})
    OPTIONAL MATCH (s)-[:PART_OF]->(m:Entity {{type: 'Mission'}})
    WHERE $log_name IS NULL OR e.Log = $log_name OR s.Log = $log_name OR r.Log = $log_name OR m.Log = $log_name
    WITH r, s, m, collect(DISTINCT e) AS events, collect(DISTINCT coalesce(e.activity, toString(e.event_id))) AS activities
    RETURN
      toString(r.id) AS robot,
      toString(s.id) AS segment,
      toString(m.id) AS mission,
      size(events) AS eventCount,
      reduce(total = 0.0, ev IN events | total + {_duration_expr('ev')}) AS activeTime,
      activities[0..8] AS activities
    ORDER BY robot, mission, segment
    """
    return _run_cypher(driver, database, query, {"log_name": log_name})

def _label_total(rows: List[Dict[str, Any]], key: str, value_key: str) -> Dict[str, float]:
    totals: Dict[str, float] = defaultdict(float)
    for row in rows:
        label = str(row.get(key) or "unknown")
        totals[label] += _safe_number(row.get(value_key))
    return dict(totals)

def _top_labels(rows: List[Dict[str, Any]], key: str, value_key: str, top_n: int) -> List[str]:
    totals = _label_total(rows, key, value_key)
    ordered = [label for label, _ in sorted(totals.items(), key=lambda item: (-item[1], item[0]))]
    return ordered[:top_n] if top_n > 0 else ordered

def render_pairwise_heatmap(
    rows: List[Dict[str, Any]],
    row_key: str,
    col_key: str,
    value_key: str,
    title: str,
    row_title: str,
    col_title: str,
    colorscale: str = "Blues",
    top_rows: int = 40,
    top_cols: int = 40,
    hover_fields: Optional[List[str]] = None,
) -> None:
    go, _ = _plotly_required()
    if not rows:
        st.info("No data available for this object pair.")
        return

    row_labels = _top_labels(rows, row_key, value_key, top_rows)
    col_labels = _top_labels(rows, col_key, value_key, top_cols)
    row_set = set(row_labels)
    col_set = set(col_labels)

    cell_rows: Dict[Tuple[str, str], Dict[str, Any]] = {}
    matrix: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in rows:
        r = str(row.get(row_key) or "unknown")
        c = str(row.get(col_key) or "unknown")
        if r not in row_set or c not in col_set:
            continue
        matrix[r][c] += _safe_number(row.get(value_key))
        cell_rows[(r, c)] = row

    if not row_labels or not col_labels:
        st.info("No cells remain after the selected filters.")
        return

    z = [[matrix[r].get(c, 0.0) for c in col_labels] for r in row_labels]
    hover_text: List[List[str]] = []
    for r in row_labels:
        hover_row = []
        for c in col_labels:
            source_row = cell_rows.get((r, c), {})
            fields = [(row_title, r), (col_title, c), (value_key, matrix[r].get(c, 0.0))]
            for field in hover_fields or []:
                if field in source_row:
                    value = source_row.get(field)
                    if field.lower().endswith("time") or field in {"activeTime", "overlapDuration", "avgTransitionTime", "avgSwitchTime", "syncDelay", "branchWait"}:
                        value = format_seconds(value)
                    fields.append((field, value))
            hover_row.append(_hover_html(title, fields, source_row if source_row else None))
        hover_text.append(hover_row)

    fig = go.Figure(go.Heatmap(
        z=z,
        x=col_labels,
        y=row_labels,
        colorscale=colorscale,
        text=z,
        texttemplate="%{text:.0f}",
        hovertext=hover_text,
        hovertemplate="%{hovertext}<extra></extra>",
        colorbar=dict(title=value_key),
    ))
    fig.update_layout(
        title=title,
        height=max(420, min(1050, 160 + 28 * len(row_labels))),
        margin=dict(l=140, r=30, t=70, b=110),
        xaxis=dict(title=col_title, tickangle=-35, automargin=True),
        yaxis=dict(title=row_title, automargin=True),
        template="plotly_white",
    )
    st.plotly_chart(fig, width="stretch", config={"scrollZoom": True, "displaylogo": False}, key=f"heatmap_{_safe_key_text(title)}_{_safe_key_text(row_key)}_{_safe_key_text(col_key)}_{_safe_key_text(value_key)}")

def _aggregate_handover_edges(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    aggregated: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        source = node_id(row.get("fromRobot")) or "unknown"
        target = node_id(row.get("toRobot")) or "unknown"
        key = (source, target)
        item = aggregated.setdefault(key, {"fromRobot": source, "toRobot": target, "count": 0, "times": []})
        item["count"] += 1
        value = _metric_value(row, ["transitionTime", "transitionTimeSeconds"])
        if value is not None:
            item["times"].append(value)
    edges: List[Dict[str, Any]] = []
    for item in aggregated.values():
        times = item.pop("times")
        item["avgTransitionTime"] = sum(times) / len(times) if times else None
        edges.append(item)
    return sorted(edges, key=lambda item: (-item["count"], item["fromRobot"], item["toRobot"]))

def _aggregate_switch_edges(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    aggregated: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        source = node_id(row.get("fromObjective")) or "unknown"
        target = node_id(row.get("toObjective")) or "unknown"
        robot = node_id(row.get("robot")) or "unknown"
        key = (source, target)
        item = aggregated.setdefault(key, {"fromObjective": source, "toObjective": target, "count": 0, "times": [], "robots": set()})
        item["count"] += 1
        item["robots"].add(robot)
        value = _metric_value(row, ["switchTime", "transitionTime", "transitionTimeSeconds"])
        if value is not None:
            item["times"].append(value)
    edges: List[Dict[str, Any]] = []
    for item in aggregated.values():
        times = item.pop("times")
        robots = sorted(item.pop("robots"))
        item["avgSwitchTime"] = sum(times) / len(times) if times else None
        item["robots"] = robots
        edges.append(item)
    return sorted(edges, key=lambda item: (-item["count"], item["fromObjective"], item["toObjective"]))

def _short_node_label(value: Any, max_len: int = 28) -> str:
    text = str(value)
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"

def _safe_key_text(value: Any, max_len: int = 80) -> str:
    text = str(value)
    safe = "".join(ch.lower() if ch.isalnum() else "_" for ch in text)
    safe = "_".join(part for part in safe.split("_") if part)
    return (safe or "plot")[:max_len]

def _format_optional_metric_for_hover(key: str, value: Any) -> Any:
    if value is None:
        return ""
    if key in {
        "avgTransitionTime",
        "avgSwitchTime",
        "transitionTime",
        "transitionTimeSeconds",
        "overlapDuration",
        "avgReturnTime",
        "returnTime",
        "syncDelay",
        "branchWait",
        "activeTime",
    }:
        return format_seconds(value)
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value)
    return value

def _dot_escape(value: Any) -> str:
    """Escape a value for a quoted DOT attribute."""
    text = str(value if value is not None else "")
    return (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "")
    )

def _html_escape(value: Any) -> str:
    text = str(value if value is not None else "")
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#039;")
    )

def _graphviz_payload_from_edges(
    rows: List[Dict[str, Any]],
    source_key: str,
    target_key: str,
    value_key: str,
    title: str,
    source_label: str,
    target_label: str,
    max_edges: int,
) -> Tuple[str, Dict[str, str], List[Dict[str, Any]]]:
    """Build a Graphviz DOT process map from an edge table."""
    prepared = [row for row in rows if row.get(source_key) is not None and row.get(target_key) is not None]
    prepared = sorted(prepared, key=lambda item: -_safe_number(item.get(value_key)))[:max_edges]
    if not prepared:
        return "", {}, []

    node_totals: Dict[str, float] = defaultdict(float)
    node_in: Dict[str, float] = defaultdict(float)
    node_out: Dict[str, float] = defaultdict(float)
    node_type_by_label: Dict[str, str] = {}
    for row in prepared:
        source = str(row.get(source_key) or "unknown")
        target = str(row.get(target_key) or "unknown")
        value = max(1.0, _safe_number(row.get(value_key), 1.0))
        node_totals[source] += value
        node_totals[target] += value
        node_out[source] += value
        node_in[target] += value
        node_type_by_label.setdefault(source, source_label)
        node_type_by_label.setdefault(target, target_label)

    max_node_value = max(node_totals.values()) if node_totals else 1.0
    max_edge_value = max(max(1.0, _safe_number(row.get(value_key), 1.0)) for row in prepared)
    node_id_by_label = {label: f"node_{idx}" for idx, label in enumerate(sorted(node_totals))}
    details_by_id: Dict[str, str] = {}
    edge_pairs = {(str(row.get(source_key) or "unknown"), str(row.get(target_key) or "unknown")) for row in prepared}

    def node_fill(total: float) -> str:
        ratio = total / max_node_value if max_node_value else 0.0
        if ratio >= 0.82:
            return "#DBEAFE"
        if ratio >= 0.56:
            return "#E0F2FE"
        if ratio >= 0.30:
            return "#F8FAFC"
        return "#FFFFFF"

    def edge_color(ratio: float) -> str:
        if ratio >= 0.78:
            return "#1D4ED8"
        if ratio >= 0.48:
            return "#3B82F6"
        if ratio >= 0.24:
            return "#60A5FA"
        return "#94A3B8"

    rank_score: Dict[str, float] = {
        label: node_out.get(label, 0.0) - node_in.get(label, 0.0)
        for label in node_totals
    }
    ordered_labels = sorted(node_totals, key=lambda label: (-rank_score[label], -node_totals[label], label))
    rank_groups: List[List[str]] = []
    chunk_size = max(3, min(6, int(len(ordered_labels) ** 0.5) + 1))
    for index in range(0, len(ordered_labels), chunk_size):
        rank_groups.append(ordered_labels[index:index + chunk_size])

    dot_lines = [
        "digraph G {",
        "  graph [rankdir=LR, newrank=true, splines=polyline, overlap=false, concentrate=true, nodesep=0.95, ranksep=1.10, pad=0.28, bgcolor=\"transparent\", outputorder=edgesfirst, labelloc=t];",
        "  node [shape=box, style=\"rounded,filled\", fillcolor=\"#F8FAFC\", color=\"#334155\", fontname=\"Helvetica\", fontsize=12, penwidth=1.4, margin=\"0.18,0.12\"];",
        "  edge [fontname=\"Helvetica\", fontsize=10, arrowsize=0.78, color=\"#2563EB\", fontcolor=\"#0F172A\", labelfloat=true];",
    ]

    for label, total in sorted(node_totals.items(), key=lambda item: (-item[1], item[0])):
        node_id = node_id_by_label[label]
        ratio = total / max_node_value if max_node_value else 0.0
        penwidth = 1.2 + 3.0 * ratio
        label_text = _short_node_label(label, 26)
        has_in = node_in.get(label, 0.0) > 0
        has_out = node_out.get(label, 0.0) > 0
        if has_out and not has_in:
            node_kind = f"{node_type_by_label.get(label, 'node')} · start-like"
        elif has_in and not has_out:
            node_kind = f"{node_type_by_label.get(label, 'node')} · end-like"
        else:
            node_kind = node_type_by_label.get(label, "node")
        details = _hover_html(
            str(label),
            [
                ("type", node_kind),
                ("total involvement", round(total, 3)),
                ("incoming", round(node_in.get(label, 0.0), 3)),
                ("outgoing", round(node_out.get(label, 0.0), 3)),
            ],
        )
        details_by_id[node_id] = details
        dot_lines.append(
            f'  {node_id} [id="{node_id}", label="{_dot_escape(label_text)}", tooltip="{_dot_escape(details)}", penwidth={penwidth:.2f}, fillcolor="{node_fill(total)}"];'
        )

    for idx, labels in enumerate(rank_groups):
        if len(labels) < 2:
            continue
        ids = "; ".join(node_id_by_label[label] for label in labels)
        dot_lines.append(f"  subgraph rank_group_{idx} {{ rank=same; {ids}; }}")

    optional_keys = [
        "avgTransitionTime",
        "avgSwitchTime",
        "transitionTime",
        "transitionTimeSeconds",
        "overlapDuration",
        "frequency",
        "avgReturnTime",
        "returnTime",
        "robots",
        "sharedRobots",
        "robotCompetition",
    ]
    table_rows: List[Dict[str, Any]] = []

    for idx, row in enumerate(prepared):
        source = str(row.get(source_key) or "unknown")
        target = str(row.get(target_key) or "unknown")
        value = max(1.0, _safe_number(row.get(value_key), 1.0))
        ratio = value / max_edge_value if max_edge_value else 0.0
        penwidth = 1.0 + 4.8 * ratio
        edge_id = f"edge_{idx}"
        edge_label = str(int(value)) if abs(value - int(value)) < 0.0001 else f"{value:.2f}"
        transition_metric = None
        for metric_key in ("avgTransitionTime", "avgSwitchTime", "transitionTime", "transitionTimeSeconds", "avgReturnTime", "returnTime"):
            if metric_key in row and row.get(metric_key) not in (None, ""):
                transition_metric = _format_optional_metric_for_hover(metric_key, row.get(metric_key))
                break
        fields = [(source_label, source), (target_label, target), (value_key, row.get(value_key))]
        for optional in optional_keys:
            if optional in row:
                fields.append((optional, _format_optional_metric_for_hover(optional, row.get(optional))))
        details = _hover_html(title, fields, row)
        details_by_id[edge_id] = details
        reverse_exists = (target, source) in edge_pairs and source != target
        minlen = 1 if ratio >= 0.72 else 2 if ratio >= 0.35 else 3
        attrs = [
            f'id="{edge_id}"',
            f'label="{_dot_escape(edge_label)}"',
            f'tooltip="{_dot_escape(details)}"',
            f'penwidth={penwidth:.2f}',
            f'color="{edge_color(ratio)}"',
            f'fontcolor="{edge_color(ratio)}"',
            f'weight={max(1, int(round(1 + ratio * 9)))}',
            f'minlen={minlen}',
            f'labeldistance={1.4 + (1.0 - ratio) * 0.8:.2f}',
        ]
        if transition_metric:
            attrs.append(f'xlabel="{_dot_escape(str(transition_metric))}"')
        if ratio < 0.18:
            attrs.append('style="dashed"')
        elif ratio < 0.42:
            attrs.append('style="solid"')
        else:
            attrs.append('style="bold"')
        if source == target:
            attrs.extend(['constraint=false', 'color="#7C3AED"', 'fontcolor="#7C3AED"', 'loopdir="right"'])
        elif reverse_exists:
            attrs.extend(['constraint=false', 'dir="forward"', 'decorate=true'])
        dot_lines.append(
            f"  {node_id_by_label[source]} -> {node_id_by_label[target]} [{', '.join(attrs)}];"
        )
        table_row = {source_key: source, target_key: target, value_key: row.get(value_key)}
        for optional in optional_keys:
            if optional in row:
                table_row[optional] = _format_optional_metric_for_hover(optional, row.get(optional))
        table_rows.append(table_row)

    dot_lines.append("}")
    return "\n".join(dot_lines), details_by_id, table_rows

def _graphviz_svg_html(
    dot: str,
    details_by_id: Dict[str, str],
    title: str,
    element_id: str,
    height: int,
) -> str:
    dot_json = json.dumps(dot)
    details_json = json.dumps(details_by_id)
    title_json = json.dumps(title)
    element_id_json = json.dumps(element_id)
    html_template = r'''
<div id="__ELEMENT_ID__" class="ocpm-gv-wrap">
  <div class="ocpm-gv-toolbar">
    <div>
      <div class="ocpm-gv-title">__TITLE_TEXT__</div>
      <div class="ocpm-gv-subtitle">Graphviz DOT layout. Pan, zoom, click a node/edge, or open the full-screen version.</div>
    </div>
    <div class="ocpm-gv-buttons">
      <button type="button" data-action="fit">Fit</button>
      <button type="button" data-action="reset">Reset</button>
      <button type="button" data-action="fullscreen">Open full-screen graph</button>
      <button type="button" data-action="dot">Download DOT</button>
    </div>
  </div>
  <div class="ocpm-gv-main">
    <div class="ocpm-gv-graph"></div>
    <div class="ocpm-gv-panel">
      <div class="ocpm-panel-title">Selection</div>
      <div class="ocpm-panel-body">Click a node or an edge to inspect it. Hover also updates this panel.</div>
    </div>
  </div>
</div>
<script src="https://cdn.jsdelivr.net/npm/@viz-js/viz@3.7.0/lib/viz-standalone.js"></script>
<script src="https://cdn.jsdelivr.net/npm/svg-pan-zoom@3.6.1/dist/svg-pan-zoom.min.js"></script>
<script>
(function() {
  const dot = __DOT_JSON__;
  const details = __DETAILS_JSON__;
  const title = __TITLE_JSON__;
  const wrapper = document.getElementById(__ELEMENT_ID_JSON__);
  if (!wrapper) return;
  const graph = wrapper.querySelector('.ocpm-gv-graph');
  const panel = wrapper.querySelector('.ocpm-panel-body');
  let panZoom = null;

  function setPanel(html) { panel.innerHTML = html || 'No details available.'; }
  function download(filename, text, mime) {
    const blob = new Blob([text], {type: mime});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename; a.click();
    setTimeout(function(){ URL.revokeObjectURL(url); }, 500);
  }
  function makeStandaloneHtml() {
    const payload = JSON.stringify(dot).replace(/</g, '\\u003c');
    const detailsPayload = JSON.stringify(details).replace(/</g, '\\u003c');
    const titlePayload = JSON.stringify(title).replace(/</g, '\\u003c');
    return '<!doctype html><html><head><meta charset="utf-8"><title>' + title + '</title>' +
      '<script src="https://cdn.jsdelivr.net/npm/@viz-js/viz@3.7.0/lib/viz-standalone.js"><\\/script>' +
      '<script src="https://cdn.jsdelivr.net/npm/svg-pan-zoom@3.6.1/dist/svg-pan-zoom.min.js"><\\/script>' +
      '<style>body{margin:0;font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif;background:#f8fafc;color:#0f172a}.bar{height:56px;display:flex;align-items:center;justify-content:space-between;padding:0 18px;background:white;border-bottom:1px solid #e2e8f0}.graph{height:calc(100vh - 56px);width:100vw;overflow:hidden}.hint{font-size:12px;color:#64748b}</style>' +
      '</head><body><div class="bar"><b>' + title + '</b><span class="hint">Pan, zoom, fit with mouse/touch. Hover elements for native tooltips.</span></div><div id="graph" class="graph"></div>' +
      '<script>const dot=' + payload + '; const title=' + titlePayload + '; const details=' + detailsPayload + '; ' +
      'Viz.instance().then(function(viz){const svg=viz.renderSVGElement(dot); const el=document.getElementById("graph"); el.innerHTML=""; el.appendChild(svg); svg.setAttribute("width","100%"); svg.setAttribute("height","100%"); if(window.svgPanZoom){svgPanZoom(svg,{zoomEnabled:true,controlIconsEnabled:true,fit:true,center:true,minZoom:0.05,maxZoom:20});}}).catch(function(err){document.getElementById("graph").innerText=String(err);});' +
      '<\\/script></body></html>';
  }

  function attachInteractions(svg) {
    svg.setAttribute('width', '100%');
    svg.setAttribute('height', '100%');
    svg.style.maxWidth = 'none';
    svg.querySelectorAll('g.node, g.edge').forEach(function(group) {
      const id = group.getAttribute('id');
      const detail = details[id] || group.textContent || 'No details available.';
      group.style.cursor = 'pointer';
      group.addEventListener('mouseover', function() { setPanel(detail); });
      group.addEventListener('click', function(evt) {
        evt.stopPropagation();
        svg.querySelectorAll('g.node polygon, g.node path, g.edge path').forEach(function(el) { el.classList.remove('ocpm-selected-shape'); });
        group.querySelectorAll('polygon, path').forEach(function(el) { el.classList.add('ocpm-selected-shape'); });
        setPanel(detail);
      });
    });
    if (window.svgPanZoom) {
      panZoom = svgPanZoom(svg, {
        zoomEnabled: true,
        controlIconsEnabled: true,
        fit: true,
        center: true,
        minZoom: 0.05,
        maxZoom: 20,
      });
    }
  }

  function render() {
    if (!window.Viz) {
      graph.innerHTML = 'Viz.js could not be loaded. Check internet access or vendor the JS files locally.';
      return;
    }
    graph.innerHTML = '<div class="ocpm-loading">Rendering Graphviz layout...</div>';
    Viz.instance().then(function(viz) {
      const svg = viz.renderSVGElement(dot);
      graph.innerHTML = '';
      graph.appendChild(svg);
      attachInteractions(svg);
    }).catch(function(error) {
      graph.innerHTML = '<pre style="white-space:pre-wrap;color:#991b1b;padding:12px;">' + String(error) + '</pre>';
    });
  }

  wrapper.querySelector('[data-action="fit"]').addEventListener('click', function() { if (panZoom) { panZoom.fit(); panZoom.center(); } });
  wrapper.querySelector('[data-action="reset"]').addEventListener('click', function() { if (panZoom) { panZoom.resetZoom(); panZoom.fit(); panZoom.center(); } });
  wrapper.querySelector('[data-action="fullscreen"]').addEventListener('click', function() {
    const win = window.open('', '_blank');
    if (!win) { setPanel('The browser blocked the new tab. Allow popups or use the embedded graph.'); return; }
    win.document.open(); win.document.write(makeStandaloneHtml()); win.document.close();
  });
  wrapper.querySelector('[data-action="dot"]').addEventListener('click', function() { download(title.replace(/[^a-zA-Z0-9_-]+/g, '_') + '.dot', dot, 'text/vnd.graphviz'); });
  render();
})();
</script>
<style>
#__ELEMENT_ID__ { width: 100%; border: 1px solid #E2E8F0; border-radius: 16px; overflow: hidden; background: #FFFFFF; }
#__ELEMENT_ID__ .ocpm-gv-toolbar { display:flex; justify-content:space-between; align-items:center; gap:16px; padding:12px 14px; border-bottom:1px solid #E2E8F0; background:linear-gradient(135deg,#ffffff 0%,#f8fafc 100%); }
#__ELEMENT_ID__ .ocpm-gv-title { font-weight:800; color:#0F172A; font-size:15px; }
#__ELEMENT_ID__ .ocpm-gv-subtitle { color:#64748B; font-size:12px; margin-top:2px; }
#__ELEMENT_ID__ .ocpm-gv-buttons { display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end; }
#__ELEMENT_ID__ button { border:1px solid #CBD5E1; background:#FFFFFF; color:#0F172A; border-radius:999px; padding:6px 10px; font-size:12px; cursor:pointer; }
#__ELEMENT_ID__ button:hover { background:#EFF6FF; border-color:#93C5FD; }
#__ELEMENT_ID__ .ocpm-gv-main { display:grid; grid-template-columns:minmax(0, 1fr) 310px; min-height: __HEIGHT_PX__px; }
#__ELEMENT_ID__ .ocpm-gv-graph { height: __HEIGHT_PX__px; width:100%; overflow:hidden; background:#FFFFFF; }
#__ELEMENT_ID__ .ocpm-gv-panel { border-left:1px solid #E2E8F0; padding:12px; background:#F8FAFC; overflow:auto; font-size:12px; color:#334155; }
#__ELEMENT_ID__ .ocpm-panel-title { font-weight:800; color:#0F172A; font-size:13px; margin-bottom:8px; }
#__ELEMENT_ID__ .ocpm-panel-body { line-height:1.45; word-break:break-word; }
#__ELEMENT_ID__ .ocpm-loading { padding: 18px; color:#64748B; font-size:13px; }
#__ELEMENT_ID__ .ocpm-selected-shape { stroke:#DC2626 !important; stroke-width:3px !important; }
@media (max-width: 900px) { #__ELEMENT_ID__ .ocpm-gv-main { grid-template-columns: 1fr; } #__ELEMENT_ID__ .ocpm-gv-panel { border-left:none; border-top:1px solid #E2E8F0; max-height:180px; } }
</style>
'''
    # Replace the longest/specific placeholders first. Replacing __ELEMENT_ID__
    # before __ELEMENT_ID_JSON__ corrupts the JavaScript placeholder and prevents
    # the Graphviz component from rendering in Streamlit.
    return (
        html_template
        .replace("__ELEMENT_ID_JSON__", element_id_json)
        .replace("__ELEMENT_ID__", element_id)
        .replace("__DOT_JSON__", dot_json)
        .replace("__DETAILS_JSON__", details_json)
        .replace("__TITLE_JSON__", title_json)
        .replace("__TITLE_TEXT__", _html_escape(title))
        .replace("__HEIGHT_PX__", str(int(height)))
    )

def render_graphviz_graph(
    dot: str,
    details_by_id: Dict[str, str],
    title: str,
    height: int = 650,
    key_suffix: Optional[str] = None,
) -> None:
    """Render a Graphviz DOT graph using Streamlit native rendering.

    The previous Viz.js component depended on external CDN scripts and could fail
    silently in locked-down/browser-blocked environments. The native Streamlit
    renderer is less interactive, but it is reliable and makes the DFG visible.
    """
    if not dot:
        st.info("No graph elements found for the selected filters.")
        return

    st.markdown(f"#### {title}")
    st.caption(
        "Graphviz DOT process layout. Use the browser zoom for inspection; "
        "download the DOT file for external Graphviz/VS Code inspection."
    )

    try:
        st.graphviz_chart(dot, width="stretch")
    except Exception as exc:  # noqa: BLE001
        st.error(f"Streamlit could not render the Graphviz chart: {exc}")
        with st.expander("DOT source", expanded=True):
            st.code(dot, language="dot")
        return

    c1, c2 = st.columns([1, 1])
    with c1:
        st.download_button(
            "Download DOT",
            data=dot,
            file_name=f"{_safe_key_text(key_suffix or title)}.dot",
            mime="text/vnd.graphviz",
            key=f"download_dot_{_safe_key_text(key_suffix or title)}",
        )
    with c2:
        with st.popover("Open DOT source"):
            st.code(dot, language="dot")

def render_dfg_network(
    rows: List[Dict[str, Any]],
    source_key: str,
    target_key: str,
    value_key: str,
    title: str,
    source_label: str,
    target_label: str,
    max_edges: int = 50,
    key_suffix: Optional[str] = None,
) -> None:
    """Render a Graphviz-based process map with pan/zoom HTML inspection."""
    if not rows:
        st.info("No directed edges found for the selected filters.")
        return
    dot, details_by_id, table_rows = _graphviz_payload_from_edges(
        rows,
        source_key,
        target_key,
        value_key,
        title,
        source_label,
        target_label,
        max_edges,
    )
    if not dot:
        st.info("No directed edges remain after filtering.")
        return
    render_graphviz_graph(
        dot,
        details_by_id,
        title,
        height=max(620, min(980, 500 + 5 * dot.count("node_") + 2 * dot.count("edge_"))),
        key_suffix=key_suffix or title,
    )
    with st.expander(f"{title} edge table", expanded=False):
        _plot_table(table_rows)

def render_graphviz_dfg(
    rows: List[Dict[str, Any]],
    source_key: str,
    target_key: str,
    value_key: str,
    title: str,
    source_label: str,
    target_label: str,
    max_links: int = 35,
) -> None:
    """Render directed flow requests as Graphviz DFG maps."""
    render_dfg_network(
        rows,
        source_key,
        target_key,
        value_key,
        title,
        source_label,
        target_label,
        max_edges=max_links,
        key_suffix=title,
    )

def _rows_for_occurrence_prefixes(
    catalog: Dict[str, Dict[str, str]],
    driver: Any,
    database: Optional[str],
    log_name: Optional[str],
    prefixes: Tuple[str, ...],
    row_limit: int,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for name, query in catalog.get("Occurrences", {}).items():
        if name.startswith(prefixes):
            for row in run_pattern_query(driver, database, query, log_name, row_limit):
                row["_pattern_view"] = name
                rows.append(row)
    return rows

def render_robot_mission_view(driver: Any, database: Optional[str], log_name: Optional[str], top_n: int) -> None:
    st.markdown("#### Robot × Mission")
    st.caption("Cells encode how much each robot contributes to each mission. This is the main resource-to-case view.")
    rows = fetch_robot_mission_rows(driver, database, log_name)
    metric = st.selectbox("Cell value", ["eventCount", "activeTime"], key="ocp_robot_mission_metric")
    render_pairwise_heatmap(
        rows, "robot", "mission", metric,
        "Robot × Mission participation matrix",
        "Robot", "Mission", "Blues", top_n, top_n,
        hover_fields=["eventCount", "activeTime", "activities"],
    )
    with st.expander("Robot × Mission table", expanded=False):
        _plot_table(rows)

def render_mission_segment_view(driver: Any, database: Optional[str], log_name: Optional[str], top_n: int) -> None:
    st.markdown("#### Mission × Segment")
    st.caption("Cells show how segments are nested in missions and how much execution each segment contributes.")
    rows = fetch_mission_segment_rows(driver, database, log_name)
    metric = st.selectbox("Cell value", ["eventCount", "activeTime", "teamSize"], key="ocp_mission_segment_metric")
    render_pairwise_heatmap(
        rows, "mission", "segment", metric,
        "Mission × Segment decomposition matrix",
        "Mission", "Segment", "Oranges", top_n, top_n,
        hover_fields=["eventCount", "activeTime", "teamSize", "robots"],
    )
    with st.expander("Mission × Segment table", expanded=False):
        _plot_table(rows)

def render_robot_segment_view(driver: Any, database: Optional[str], log_name: Optional[str], top_n: int) -> None:
    st.markdown("#### Robot × Segment")
    st.caption("Cells encode which robots execute which mission segments. This reveals specialization and repeated collaboration units.")
    rows = fetch_robot_segment_rows(driver, database, log_name)
    metric = st.selectbox("Cell value", ["eventCount", "activeTime"], key="ocp_robot_segment_metric")
    render_pairwise_heatmap(
        rows, "robot", "segment", metric,
        "Robot × Segment participation matrix",
        "Robot", "Segment", "Purples", top_n, top_n,
        hover_fields=["mission", "eventCount", "activeTime", "activities"],
    )
    with st.expander("Robot × Segment table", expanded=False):
        _plot_table(rows)

def render_robot_robot_view(driver: Any, database: Optional[str], catalog: Dict[str, Dict[str, str]], log_name: Optional[str], row_limit: int, top_n: int) -> None:
    st.markdown("#### Robot × Robot")
    st.caption("Cells summarize directed robot-to-robot collaboration, mainly handovers.")
    raw_rows = _rows_for_occurrence_prefixes(catalog, driver, database, log_name, ("handover_",), row_limit)
    edges = _aggregate_handover_edges(raw_rows)
    metric = st.selectbox("Cell value", ["count", "avgTransitionTime"], key="ocp_robot_robot_metric")
    render_pairwise_heatmap(
        edges, "fromRobot", "toRobot", metric,
        "Robot × Robot handover matrix",
        "From robot", "To robot", "Reds", top_n, top_n,
        hover_fields=["count", "avgTransitionTime"],
    )
    with st.expander("Handover DFG network", expanded=False):
        render_graphviz_dfg(edges, "fromRobot", "toRobot", "count", "Robot handover DFG", "fromRobot", "toRobot")
    with st.expander("Robot × Robot handover table", expanded=False):
        _plot_table(edges)

def render_mission_mission_view(driver: Any, database: Optional[str], catalog: Dict[str, Dict[str, str]], log_name: Optional[str], row_limit: int, top_n: int) -> None:
    st.markdown("#### Mission × Mission")
    st.caption("Cells summarize mission-level parallel collaboration and competition for shared robots.")
    query = catalog.get("Occurrences", {}).get("parallel_collaboration_mission")
    rows = run_pattern_query(driver, database, query, log_name, row_limit) if query else []
    matrix_rows: List[Dict[str, Any]] = []
    for row in rows:
        m1 = node_id(row.get("mission1")) or "unknown"
        m2 = node_id(row.get("mission2")) or "unknown"
        matrix_rows.append({
            "mission1": m1,
            "mission2": m2,
            "overlapDuration": row.get("overlapDuration"),
            "robotCompetition": row.get("robotCompetition"),
            "sharedRobots": _node_id_list(row.get("sharedRobots")),
            "raw": row,
        })
        matrix_rows.append({
            "mission1": m2,
            "mission2": m1,
            "overlapDuration": row.get("overlapDuration"),
            "robotCompetition": row.get("robotCompetition"),
            "sharedRobots": _node_id_list(row.get("sharedRobots")),
            "raw": row,
        })
    metric = st.selectbox("Cell value", ["overlapDuration", "robotCompetition"], key="ocp_mission_mission_metric")
    render_pairwise_heatmap(
        matrix_rows, "mission1", "mission2", metric,
        "Mission × Mission parallel collaboration matrix",
        "Mission", "Mission", "Greens", top_n, top_n,
        hover_fields=["overlapDuration", "robotCompetition", "sharedRobots"],
    )
    with st.expander("Mission × Mission table", expanded=False):
        _plot_table(matrix_rows)

def render_segment_segment_view(driver: Any, database: Optional[str], catalog: Dict[str, Dict[str, str]], log_name: Optional[str], row_limit: int, top_n: int) -> None:
    st.markdown("#### Segment × Segment")
    st.caption("Cells summarize parallel segment overlaps within the same mission. Use the mission filter to avoid mixing unrelated segment pairs.")
    mission_ids = st.session_state.get("collab_mission_ids")
    if mission_ids is None:
        mission_ids = fetch_mission_ids(driver, database, log_name)
        st.session_state["collab_mission_ids"] = mission_ids
    selected_mission = st.selectbox("Mission filter", [ALL_OPTION] + mission_ids, key="ocp_segment_segment_mission")
    query = catalog.get("Occurrences", {}).get("parallel_collaboration_segment")
    rows = run_pattern_query(driver, database, query, log_name, row_limit) if query else []
    if selected_mission != ALL_OPTION:
        rows = [row for row in rows if node_id(row.get("mission")) == selected_mission]
    matrix_rows: List[Dict[str, Any]] = []
    for row in rows:
        s1 = node_id(row.get("segment1")) or "unknown"
        s2 = node_id(row.get("segment2")) or "unknown"
        mission = node_id(row.get("mission")) or "unknown"
        common = {
            "mission": mission,
            "overlapDuration": row.get("overlapDuration"),
            "robotCompetition": row.get("robotCompetition"),
            "sharedRobots": _node_id_list(row.get("sharedRobots")),
            "raw": row,
        }
        matrix_rows.append({"segment1": s1, "segment2": s2, **common})
        matrix_rows.append({"segment1": s2, "segment2": s1, **common})
    sync_rows: List[Dict[str, Any]] = []
    sync_query = catalog.get("Diagnostics", {}).get("sync_diagnostics_parallel_segments")
    if sync_query:
        sync_rows = run_pattern_query(driver, database, sync_query, log_name, row_limit)
        if selected_mission != ALL_OPTION:
            sync_rows = [row for row in sync_rows if node_id(row.get("mission")) == selected_mission]
    metric = st.selectbox("Cell value", ["overlapDuration", "robotCompetition"], key="ocp_segment_segment_metric")
    render_pairwise_heatmap(
        matrix_rows, "segment1", "segment2", metric,
        "Segment × Segment parallel collaboration matrix",
        "Segment", "Segment", "Greens", top_n, top_n,
        hover_fields=["mission", "overlapDuration", "robotCompetition", "sharedRobots"],
    )
    c1, c2 = st.columns(2)
    with c1:
        with st.expander("Segment × Segment overlap table", expanded=False):
            _plot_table(matrix_rows)
    with c2:
        with st.expander("Synchronization diagnostics for selected mission", expanded=True):
            _plot_table(sync_rows)

def render_switch_flow_pairwise_view(driver: Any, database: Optional[str], catalog: Dict[str, Dict[str, str]], log_name: Optional[str], row_limit: int) -> None:
    st.markdown("#### Objective-switch flow")
    st.caption("DFG-style view for switches between missions or segments. Limit the edges to keep labels readable.")
    raw_rows = _rows_for_occurrence_prefixes(catalog, driver, database, log_name, ("objective_switch_",), row_limit)
    scope = st.radio("Objective scope", ["All", "Mission", "Segment"], horizontal=True, key="ocp_switch_scope")
    if scope != "All":
        raw_rows = [row for row in raw_rows if str(row.get("_pattern_view", "")).endswith(scope.lower())]
    max_links = st.slider("Maximum DFG edges", min_value=5, max_value=80, value=30, step=5, key="ocp_switch_dfg_edges")
    edges = _aggregate_switch_edges(raw_rows)
    render_graphviz_dfg(edges, "fromObjective", "toObjective", "count", "Objective-switch DFG", "fromObjective", "toObjective", max_links=max_links)
    with st.expander("Objective-switch edge table", expanded=False):
        _plot_table(edges)

def render_object_centric_pairwise_tab(driver: Any, database: Optional[str], catalog: Dict[str, Dict[str, str]], log_name: Optional[str]) -> None:
    st.subheader("Object-Centric Pairwise Views")
    st.caption(
        "A 2D visualization can only place two object types on its axes. These views therefore inspect each pair of object perspectives separately: Robot×Mission, Mission×Segment, Robot×Segment, Robot×Robot, Mission×Mission, and Segment×Segment."
    )
    c1, c2 = st.columns([1, 1])
    with c1:
        top_n = st.slider("Maximum labels per axis", min_value=5, max_value=80, value=35, step=5, key="ocp_top_labels")
    with c2:
        row_limit = st.slider("Rows per pattern query", min_value=50, max_value=5000, value=1500, step=50, key="ocp_pattern_row_limit")

    render_dashboard_cards([
        {"label": "Cross-perspective views", "value": "3", "caption": "Robot×Mission, Mission×Segment, Robot×Segment", "accent": "#2563EB"},
        {"label": "Within-perspective views", "value": "3", "caption": "Robot×Robot, Mission×Mission, Segment×Segment", "accent": "#059669"},
        {"label": "Interpretation", "value": "2D", "caption": "Each plot fixes two object dimensions", "accent": "#D97706"},
    ])

    tab_rm, tab_ms, tab_rs, tab_rr, tab_mm, tab_ss, tab_flow = st.tabs([
        "Robot × Mission",
        "Mission × Segment",
        "Robot × Segment",
        "Robot × Robot",
        "Mission × Mission",
        "Segment × Segment",
        "Switch Flow",
    ])
    with tab_rm:
        render_robot_mission_view(driver, database, log_name, top_n)
    with tab_ms:
        render_mission_segment_view(driver, database, log_name, top_n)
    with tab_rs:
        render_robot_segment_view(driver, database, log_name, top_n)
    with tab_rr:
        render_robot_robot_view(driver, database, catalog, log_name, row_limit, top_n)
    with tab_mm:
        render_mission_mission_view(driver, database, catalog, log_name, row_limit, top_n)
    with tab_ss:
        render_segment_segment_view(driver, database, catalog, log_name, row_limit, top_n)
    with tab_flow:
        render_switch_flow_pairwise_view(driver, database, catalog, log_name, row_limit)

def fetch_activity_dfg(driver: Any, database: Optional[str], log_name: Optional[str], perspective: str, limit: int, min_frequency: int) -> List[Dict[str, Any]]:
    relationship = "DF_Control" if perspective == "Robot (all events)" else "DF"
    relationship_perspective = "Robot" if perspective == "Robot (all events)" else perspective
    query = f"""
    MATCH (e1:Event)-[df:{relationship}]->(e2:Event)
    WHERE coalesce(df.type, df.perspective_type) = $perspective
      AND ($log_name IS NULL OR e1.Log = $log_name OR e2.Log = $log_name)
    WITH
      coalesce(toString(e1.activity), toString(e1.event_id)) AS source,
      coalesce(toString(e2.activity), toString(e2.event_id)) AS target,
      CASE
        WHEN df.transitionTimeSeconds IS NOT NULL THEN toFloat(df.transitionTimeSeconds)
        WHEN e1.end IS NOT NULL AND e2.start IS NOT NULL THEN toFloat(duration.inSeconds(e1.end, e2.start).seconds)
        ELSE null
      END AS transitionTime
    WITH source, target, count(*) AS frequency, avg(transitionTime) AS avgTransitionTime
    WHERE frequency >= $min_frequency
    RETURN source, target, frequency, avgTransitionTime
    ORDER BY frequency DESC, source, target
    LIMIT $limit
    """
    return _run_cypher(
        driver,
        database,
        query,
        {"log_name": log_name, "perspective": relationship_perspective, "limit": int(limit), "min_frequency": int(min_frequency)},
    )

def render_activity_dfg_graphviz(rows: List[Dict[str, Any]], perspective: str) -> None:
    if not rows:
        st.info("No directly-follows activity edges found for this perspective.")
        return
    render_graphviz_dfg(
        rows,
        "source",
        "target",
        "frequency",
        f"Activity DFG ({perspective} perspective)",
        "source activity",
        "target activity",
        max_links=min(80, len(rows)),
    )

def render_process_maps_tab(driver: Any, database: Optional[str], catalog: Dict[str, Dict[str, str]], log_name: Optional[str]) -> None:
    st.subheader("Process Maps")
    st.caption(
        "Discover activity-level directly-follows maps from the EKG. This is the process-mining view: activities are aggregated, while collaboration metrics can be inspected in the other tabs."
    )
    c1, c2, c3 = st.columns([1, 1, 1])
    with c1:
        perspective = st.selectbox(
            "DF perspective",
            ["Mission", "Segment", "Robot", "Robot (all events)"],
            index=0,
            key="pm_dfg_perspective",
            help="Robot (all events) uses DF_Control and includes both Task and Control events.",
        )
    with c2:
        min_frequency = st.slider("Minimum edge frequency", min_value=1, max_value=50, value=1, step=1, key="pm_dfg_min_frequency")
    with c3:
        limit = st.slider("Maximum edges", min_value=10, max_value=300, value=80, step=10, key="pm_dfg_limit")

    rows = fetch_activity_dfg(driver, database, log_name, perspective, limit, min_frequency)
    render_activity_dfg_graphviz(rows, perspective)
    with st.expander("DFG edge table", expanded=False):
        _plot_table(rows)

def _capability_ids(value: Any) -> List[str]:
    return _node_id_list(value)

def render_capability_demand_chart(rows: List[Dict[str, Any]]) -> None:
    go, _ = _plotly_required()
    prepared = []
    for row in rows:
        capability = node_id(row.get("capability")) or "unknown"
        objective = node_id(row.get("objective")) or "unknown"
        prepared.append({
            "label": f"{objective} / {capability}",
            "requirementCount": _safe_number(row.get("requirementCount")),
            "availability": _safe_number(row.get("availability")),
            "demandPerProvider": _safe_number(row.get("demandPerProvider")),
        })
    prepared = sorted(
        prepared,
        key=lambda item: (-item["demandPerProvider"], -item["requirementCount"]),
    )
    if not prepared:
        st.info("No capability demand/availability diagnostics found.")
        return
    fig = go.Figure(go.Bar(
        x=[item["demandPerProvider"] for item in prepared],
        y=[item["label"] for item in prepared],
        orientation="h",
        customdata=[[item["requirementCount"], item["availability"]] for item in prepared],
        hovertemplate="objective / capability=%{y}<br>tasks per provider=%{x:.3f}<br>requirements=%{customdata[0]}<br>providers=%{customdata[1]}<extra></extra>",
    ))
    fig.update_layout(
        title="Capability demand per provider",
        height=max(380, min(850, 110 + 32 * len(prepared))),
        xaxis_title="task requirements per available provider",
        yaxis_title="Objective / capability",
        template="plotly_white",
    )
    st.plotly_chart(fig, width="stretch", config={"scrollZoom": True, "displaylogo": False}, key="capability_pressure_chart")

def render_capability_return_motifs(rows: List[Dict[str, Any]]) -> None:
    aggregated: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for row in rows:
        returning = node_id(row.get("returningRobot")) or "unknown"
        intermediate = node_id(row.get("intermediateRobot")) or "unknown"
        capabilities = _capability_ids(row.get("capabilities")) or ["unknown"]
        for capability in capabilities:
            key = (returning, intermediate, capability)
            item = aggregated.setdefault(key, {"returningRobot": returning, "intermediateRobot": intermediate, "capability": capability, "count": 0, "returnTimes": []})
            item["count"] += 1
            value = _metric_value(row, ["returnTime"])
            if value is not None:
                item["returnTimes"].append(value)
    motifs = []
    for item in aggregated.values():
        times = item.pop("returnTimes")
        item["avgReturnTime"] = sum(times) / len(times) if times else None
        motifs.append(item)
    motifs = sorted(motifs, key=lambda item: (-item["count"], item["capability"]))
    _plot_table(motifs, "No capability-return motifs found.")

def render_capability_diagnostics_tab(driver: Any, database: Optional[str], catalog: Dict[str, Dict[str, str]], log_name: Optional[str]) -> None:
    st.subheader("Capability Diagnostics")
    st.caption("Compare objective-level capability demand with provider availability and inspect capability-driven returns.")
    row_limit = st.slider("Rows per capability query", min_value=50, max_value=3000, value=800, step=50, key="capability_row_limit")
    diagnostic_rows: List[Dict[str, Any]] = []
    demand_rows: List[Dict[str, Any]] = []
    for name, query in catalog.get("Diagnostics", {}).items():
        if name.startswith("cap_return_diagnostics"):
            for row in run_pattern_query(driver, database, query, log_name, row_limit):
                row["_diagnostic_view"] = name
                diagnostic_rows.append(row)
        elif name.startswith("capability_demand_availability"):
            for row in run_pattern_query(driver, database, query, log_name, row_limit):
                row["_diagnostic_view"] = name
                demand_rows.append(row)
    occurrence_rows = _collect_occurrence_rows(catalog, driver, database, log_name, ("capability_driven_return_",), row_limit)
    render_dashboard_cards([
        {"label": "Capability returns", "value": str(len(occurrence_rows)), "caption": "Return structures explained by missing capabilities", "accent": "#7C3AED"},
        {"label": "Demand contexts", "value": str(len(demand_rows)), "caption": "Objective-capability demand/availability rows", "accent": "#2563EB"},
    ])
    c1, c2 = st.columns([1.15, 1])
    with c1:
        render_capability_demand_chart(demand_rows)
    with c2:
        st.markdown("#### Capability-return motifs")
        render_capability_return_motifs(occurrence_rows)
    with st.expander("Capability indicator tables", expanded=False):
        st.markdown("**Demand and availability**")
        _plot_table(demand_rows)
        st.markdown("**Capability-driven return summary**")
        _plot_table(diagnostic_rows)
