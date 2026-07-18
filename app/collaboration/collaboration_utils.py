from __future__ import annotations

import json
import math
from collections.abc import Mapping
from html import escape
from typing import Any, Dict, List, Optional, Tuple


ALL_OPTION = "All"

PATTERN_PALETTE = [
    "#DC2626", "#2563EB", "#059669", "#D97706", "#7C3AED",
    "#DB2777", "#0F766E", "#B91C1C", "#1D4ED8", "#65A30D",
]

SEGMENT_PALETTE = [
    "#1f77b4", "#2ca02c", "#ff7f0e", "#9467bd", "#e377c2",
    "#8c564b", "#17becf", "#bcbd22", "#d62728", "#7f7f7f",
]

PATTERN_METADATA: Dict[str, Dict[str, str]] = {
    "handover": {
        "family": "Coordination intensity",
        "short": "HO",
        "question": "Where is work transferred between robots?",
        "paper_link": "Robot handover: transfer of work within the same mission or segment.",
    },
    "co_participation": {
        "family": "Team composition",
        "short": "CP",
        "question": "Which missions or segments require several robots?",
        "paper_link": "Co-participation: shared contribution to the same objective context.",
    },
    "objective_switch": {
        "family": "Allocation dynamics",
        "short": "SW",
        "question": "Where do robots move between missions or segments?",
        "paper_link": "Objective switch: reallocation from the robot perspective.",
    },
    "capability_driven_return": {
        "family": "Capability pressure",
        "short": "CR",
        "question": "Which returns are explained by missing or scarce capabilities?",
        "paper_link": "Capability-driven return: return structure explained by capability requirements.",
    },
    "parallel_collaboration": {
        "family": "Parallelism and synchronization",
        "short": "PC",
        "question": "Which missions or segments overlap in time?",
        "paper_link": "Parallel collaboration: concurrent objective instances involving robots.",
    },
    "sync_diagnostics": {
        "family": "Parallelism and synchronization",
        "short": "SYNC",
        "question": "Do completed segments wait before downstream mission execution?",
        "paper_link": "Synchronization diagnostics for parallel segments.",
    },
}


def normalize_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if hasattr(value, "labels") and hasattr(value, "items"):
        props = {str(key): normalize_value(item) for key, item in dict(value.items()).items()}
        props["__labels"] = sorted(str(label) for label in value.labels)
        return props

    if hasattr(value, "type") and hasattr(value, "items"):
        props = {str(key): normalize_value(item) for key, item in dict(value.items()).items()}
        props["__type"] = str(value.type)
        return props

    if isinstance(value, Mapping):
        return {str(key): normalize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [normalize_value(item) for item in value]
    return str(value)


def table_safe_value(value: Any) -> Any:
    normalized = normalize_value(value)
    if isinstance(normalized, (dict, list)):
        return json.dumps(normalized, ensure_ascii=True, indent=2)
    return normalized


def table_safe_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{key: table_safe_value(value) for key, value in row.items()} for row in rows]


def humanize_name(name: str) -> str:
    name = name.replace("cap_return", "capability_driven_return")
    return name.replace("_", " ").strip().title()


def pattern_base_name(pattern_name: str) -> str:
    for base in PATTERN_METADATA:
        if pattern_name.startswith(base):
            return base
    if "cap_return" in pattern_name:
        return "capability_driven_return"
    if "parallel" in pattern_name:
        return "parallel_collaboration"
    return pattern_name


def pattern_family(pattern_name: str) -> str:
    base = pattern_base_name(pattern_name)
    return PATTERN_METADATA.get(base, {}).get("family", "Other")


def pattern_short(pattern_name: str) -> str:
    base = pattern_base_name(pattern_name)
    return PATTERN_METADATA.get(base, {}).get("short", base[:3].upper())


def pattern_color_map(pattern_names: List[str]) -> Dict[str, str]:
    ordered = sorted(set(pattern_names))
    return {name: PATTERN_PALETTE[index % len(PATTERN_PALETTE)] for index, name in enumerate(ordered)}


def segment_color_map(segment_ids: List[str]) -> Dict[str, str]:
    ordered = sorted({segment for segment in segment_ids if segment})
    return {segment: SEGMENT_PALETTE[index % len(SEGMENT_PALETTE)] for index, segment in enumerate(ordered)}


def node_id(value: Any) -> Optional[str]:
    if isinstance(value, Mapping):
        for key in ("id", "event_id", "name"):
            nested = value.get(key)
            if nested is not None:
                return str(nested)
    if value is not None and not isinstance(value, (dict, list, tuple, set)):
        return str(value)
    return None


def event_id_from_mapping(value: Any) -> Optional[str]:
    if isinstance(value, Mapping):
        for key in ("event_id", "id"):
            event_id = value.get(key)
            if event_id is not None:
                return str(event_id)
    return None


def row_contains_log(value: Any, log_name: Optional[str]) -> bool:
    if log_name is None:
        return True
    if isinstance(value, Mapping):
        if value.get("Log") == log_name:
            return True
        return any(row_contains_log(item, log_name) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(row_contains_log(item, log_name) for item in value)
    return False


def filter_rows_by_log(rows: List[Dict[str, Any]], log_name: Optional[str]) -> List[Dict[str, Any]]:
    if log_name is None:
        return rows
    return [row for row in rows if row_contains_log(row, log_name)]


def format_seconds(value: Any) -> str:
    if not isinstance(value, (int, float)) or math.isnan(float(value)):
        return ""
    seconds = float(value)
    if abs(seconds) < 60:
        return f"{seconds:.0f}s"
    if abs(seconds) < 3600:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.1f}h"


def compact_metric_label(row: Dict[str, Any]) -> str:
    for key in (
        "transitionTimeSeconds", "transitionTime", "switchTime", "transitionToIntermediate",
        "transitionBack", "returnTime", "overlapDuration", "syncDelay",
        "branchWait", "objectiveDuration",
    ):
        value = row.get(key)
        if isinstance(value, (int, float)):
            return f"{key}={format_seconds(value)}"
    return "occurrence"


def _numeric_metric_items(row: Dict[str, Any], keys: List[str]) -> List[Tuple[str, float]]:
    items: List[Tuple[str, float]] = []
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            continue
        value = row.get(key)
        if isinstance(value, (int, float)) and not math.isnan(float(value)):
            items.append((key, float(value)))
            seen.add(key)
    return items


def pattern_edge_metric_items(row: Dict[str, Any], pair: str) -> List[Tuple[str, float]]:
    if pair == "e_i->e_j":
        preferred = [
            "transitionToIntermediate",
            "transitionTimeSeconds",
            "transitionTime",
            "switchTime",
            "returnTime",
        ]
    elif pair == "e_j->e_k":
        preferred = [
            "transitionBack",
            "transitionTimeSeconds",
            "transitionTime",
            "returnTime",
        ]
    else:
        preferred = [
            "transitionTimeSeconds",
            "transitionTime",
            "switchTime",
            "returnTime",
            "avgTransitionTime",
            "avgSwitchTime",
            "avgReturnTime",
        ]

    fallback = [
        "transitionTimeSeconds",
        "transitionTime",
        "transitionToIntermediate",
        "transitionBack",
        "switchTime",
        "returnTime",
        "overlapDuration",
        "syncDelay",
        "branchWait",
        "objectiveDuration",
    ]
    return _numeric_metric_items(row, preferred + fallback)


def pattern_edge_metric_label(row: Dict[str, Any], pair: str) -> str:
    items = pattern_edge_metric_items(row, pair)
    if not items:
        return "occurrence"
    key, value = items[0]
    return f"{key}={format_seconds(value)}"


def pattern_edge_hover_html(
    pattern_name: str,
    transition: Dict[str, Any],
    left_event: Dict[str, Any],
    right_event: Dict[str, Any],
) -> str:
    row = transition.get("row", {})
    pair = str(transition.get("pair", ""))
    metric_items = pattern_edge_metric_items(row, pair)
    if metric_items:
        metric_lines = "".join(
            f"<br>{escape(key)}: {escape(format_seconds(value))} ({value:.3f}s)"
            for key, value in metric_items
        )
    else:
        metric_lines = f"<br>metric: {escape(str(transition.get('label', 'occurrence')))}"

    return (
        f"<b>{escape(humanize_name(pattern_name))}</b>"
        f"<br>edge: {escape(str(transition.get('from_event_id')))} -> {escape(str(transition.get('to_event_id')))}"
        f"<br>activities: {escape(str(left_event.get('activity')))} -> {escape(str(right_event.get('activity')))}"
        f"<br>robots: {escape(str(left_event.get('robot_id')))} -> {escape(str(right_event.get('robot_id')))}"
        f"<br>segments: {escape(str(left_event.get('segment_id') or 'mission-level'))} -> {escape(str(right_event.get('segment_id') or 'mission-level'))}"
        f"<br>pair: {escape(pair)}"
        f"{metric_lines}"
    )


def candidate_numeric_keys(rows: List[Dict[str, Any]]) -> List[str]:
    if not rows:
        return []
    keys: Dict[str, int] = {}
    for row in rows:
        for key, value in row.items():
            if isinstance(value, (int, float)):
                keys[key] = keys.get(key, 0) + 1
    return [key for key, count in sorted(keys.items(), key=lambda item: (-item[1], item[0])) if count > 0]


def preferred_timeline_metric(rows: List[Dict[str, Any]]) -> Optional[str]:
    preferred = [
        "transitionTimeSeconds", "transitionTime", "avgTransitionTime", "switchTime",
        "avgSwitchTime", "returnTime", "avgReturnTime", "syncDelay",
        "overlapDuration", "objectiveDuration", "activeTime", "branchWait", "rate",
    ]
    available = set(candidate_numeric_keys(rows))
    for key in preferred:
        if key in available:
            return key
    return next(iter(available), None) if available else None
