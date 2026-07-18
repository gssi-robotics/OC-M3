from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import neo4j_shared

from .collaboration_utils import (
    filter_rows_by_log,
    format_seconds,
    node_id,
    event_id_from_mapping,
    normalize_value,
    pattern_edge_metric_label,
    pattern_family,
)


def fetch_logs(driver: Any, database: Optional[str]) -> List[str]:
    query = """
    MATCH (n)
    WHERE n.Log IS NOT NULL
    RETURN DISTINCT n.Log AS log
    ORDER BY log
    """
    with driver.session(**neo4j_shared.session_kwargs(database)) as session:
        return [str(record["log"]) for record in session.run(query) if record["log"] is not None]


def fetch_mission_ids(driver: Any, database: Optional[str], log_name: Optional[str]) -> List[str]:
    query = """
    MATCH (m:Entity)
    WHERE m.type = 'Mission'
      AND ($log_name IS NULL OR m.Log = $log_name)
    RETURN DISTINCT toString(m.id) AS mission_id
    ORDER BY mission_id
    """
    with driver.session(**neo4j_shared.session_kwargs(database)) as session:
        return [record["mission_id"] for record in session.run(query, log_name=log_name) if record["mission_id"] is not None]


def fetch_mission_events(driver: Any, database: Optional[str], mission_id: str, log_name: Optional[str]) -> List[Dict[str, Any]]:
    query = """
    MATCH (m:Entity {type: 'Mission', id: $mission_id})<-[:CORR]-(e:Event)
    WHERE $log_name IS NULL OR e.Log = $log_name OR m.Log = $log_name
    OPTIONAL MATCH (e)-[:CORR]->(ro:Entity {type: 'Robot'})
    OPTIONAL MATCH (e)-[:CORR]->(seg:Entity {type: 'Segment'})
    RETURN
      toString(e.event_id) AS event_id,
      coalesce(e.activity, toString(e.event_id)) AS activity,
      toString(e.start) AS start_text,
      toString(e.end) AS end_text,
      e.start.epochMillis AS start_ms,
      e.end.epochMillis AS end_ms,
      coalesce(toString(ro.id), 'unassigned') AS robot_id,
      coalesce(toString(seg.id), '') AS segment_id
    ORDER BY start_ms, end_ms, event_id
    """
    with driver.session(**neo4j_shared.session_kwargs(database)) as session:
        return [normalize_value(record.data()) for record in session.run(query, mission_id=mission_id, log_name=log_name)]


def run_query(driver: Any, database: Optional[str], query: str, parameters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    with driver.session(**neo4j_shared.session_kwargs(database)) as session:
        result = session.run(query, **(parameters or {}))
        return [normalize_value(record.data()) for record in result]


def run_pattern_query(
    driver: Any,
    database: Optional[str],
    query: str,
    log_name: Optional[str],
    row_limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    rows = run_query(driver, database, query)
    filtered = filter_rows_by_log(rows, log_name)
    if row_limit is not None and row_limit > 0:
        return filtered[:row_limit]
    return filtered


def occurrence_count_from_rows(driver: Any, database: Optional[str], query: str, log_name: Optional[str]) -> int:
    return len(run_pattern_query(driver, database, query, log_name))


def load_collaboration_patterns_module() -> Any:
    module_path = Path(__file__).resolve().parent.parent.parent / "query-lib" / "collab_patterns.py"
    if not module_path.exists():
        raise ImportError(f"Could not find collab_patterns.py at {module_path}")

    spec = importlib.util.spec_from_file_location("collab_patterns", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module spec for {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _maybe_call(obj: Any, name: str, *args: Any) -> Optional[str]:
    fn = getattr(obj, name, None)
    if callable(fn):
        try:
            return fn(*args)
        except Exception:
            return None
    return None


def build_pattern_catalog(factory: Any) -> Dict[str, Dict[str, str]]:
    occurrences: Dict[str, str] = {}
    diagnostics: Dict[str, str] = {}

    for objective in ("Mission", "Segment"):
        occurrence_specs = [
            (f"handover_{objective.lower()}", "robot_handover"),
            (f"co_participation_{objective.lower()}", "co_participation"),
            (f"objective_switch_{objective.lower()}", "objective_switch"),
            (f"capability_driven_return_{objective.lower()}", "capability_driven_return"),
        ]
        for key, method in occurrence_specs:
            query = _maybe_call(factory, method, objective)
            if query:
                occurrences[key] = query

    for key, method in (
        ("parallel_collaboration_mission", "parallel_collaboration_mission"),
        ("parallel_collaboration_segment", "parallel_collaboration_segment"),
    ):
        query = _maybe_call(factory, method)
        if query:
            occurrences[key] = query

    for key, method in (
        ("handover_time_diagnostics", "handover_time_diagnostics"),
        ("objective_switch_time_diagnostics", "objective_switch_time_diagnostics"),
        ("co_participation_team_diagnostics", "co_participation_team_diagnostics"),
        ("capability_return_diagnostics", "capability_return_diagnostics"),
        ("sync_diagnostics_parallel_segments", "synchronization_diagnostics_parallel_segments"),
    ):
        query = _maybe_call(factory, method)
        if query:
            diagnostics[key] = query

    return {"Occurrences": occurrences, "Diagnostics": diagnostics}


def segment_extents(mission_events: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    extents: Dict[str, Dict[str, Any]] = {}
    for event in mission_events:
        segment = str(event.get("segment_id") or "")
        if not segment:
            continue
        start_ms = event.get("start_ms")
        end_ms = event.get("end_ms")
        if not isinstance(start_ms, (int, float)) or not isinstance(end_ms, (int, float)):
            continue
        if segment not in extents:
            extents[segment] = {"start_ms": float(start_ms), "end_ms": float(end_ms), "events": []}
        extents[segment]["start_ms"] = min(float(start_ms), extents[segment]["start_ms"])
        extents[segment]["end_ms"] = max(float(end_ms), extents[segment]["end_ms"])
        extents[segment]["events"].append(event)
    return extents


def mission_span(mission_events: List[Dict[str, Any]]) -> Tuple[float, float]:
    min_ms = min(float(event["start_ms"]) for event in mission_events)
    max_ms = max(float(event["end_ms"]) for event in mission_events)
    return min_ms, max_ms


def relative_seconds(value_ms: float, base_ms: float) -> float:
    return (float(value_ms) - float(base_ms)) / 1000.0


def prepare_events_for_timeline(mission_events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    valid = [
        event.copy()
        for event in mission_events
        if isinstance(event.get("start_ms"), (int, float)) and isinstance(event.get("end_ms"), (int, float))
    ]
    if not valid:
        return []
    min_ms, _ = mission_span(valid)
    for idx, event in enumerate(valid, start=1):
        event["seq"] = idx
        event["robot_id"] = str(event.get("robot_id") or "unassigned")
        event["segment_id"] = str(event.get("segment_id") or "")
        event["start_s"] = relative_seconds(float(event["start_ms"]), min_ms)
        event["end_s"] = relative_seconds(float(event["end_ms"]), min_ms)
        event["duration_s"] = max(0.05, event["end_s"] - event["start_s"])
        event["activity_short"] = str(event.get("activity") or "")[:28]
    return valid


def extract_pattern_transitions(rows: List[Dict[str, Any]], mission_events: List[Dict[str, Any]], pattern_name: str) -> List[Dict[str, Any]]:
    mission_event_ids = {str(event["event_id"]) for event in mission_events}
    transitions: List[Dict[str, Any]] = []
    event_pairs = [("e_i", "e_j"), ("e_j", "e_k"), ("e1", "e2"), ("e2", "e3")]

    for index, row in enumerate(rows, start=1):
        for left_key, right_key in event_pairs:
            pair = f"{left_key}->{right_key}"
            left_id = event_id_from_mapping(row.get(left_key))
            right_id = event_id_from_mapping(row.get(right_key))
            if left_id and right_id and left_id in mission_event_ids and right_id in mission_event_ids:
                label = pattern_edge_metric_label(row, pair)
                transitions.append({
                    "from_event_id": left_id,
                    "to_event_id": right_id,
                    "label": label if label != "occurrence" else f"occurrence {index}",
                    "pair": pair,
                    "pattern_name": pattern_name,
                    "row": row,
                })
    return transitions


def extract_multi_pattern_transitions(
    pattern_rows_by_name: Dict[str, List[Dict[str, Any]]],
    mission_events: List[Dict[str, Any]],
    selected_patterns: List[str],
) -> List[Dict[str, Any]]:
    transitions: List[Dict[str, Any]] = []
    for pattern_name in selected_patterns:
        transitions.extend(extract_pattern_transitions(pattern_rows_by_name.get(pattern_name, []), mission_events, pattern_name))
    return transitions


def extract_structural_highlights(
    pattern_rows_by_name: Dict[str, List[Dict[str, Any]]],
    mission_id: str,
    mission_events: List[Dict[str, Any]],
    selected_patterns: List[str],
) -> List[Dict[str, Any]]:
    highlights: List[Dict[str, Any]] = []
    seg_ext = segment_extents(mission_events)
    mission_event_ids = {str(event.get("event_id")) for event in mission_events}

    for pattern_name in selected_patterns:
        for row in pattern_rows_by_name.get(pattern_name, []):
            if pattern_name.startswith("co_participation"):
                obj_id = node_id(row.get("objective"))
                if obj_id == mission_id:
                    highlights.append({"kind": "mission_team", "pattern_name": pattern_name, "label": f"mission team={row.get('teamSize', '?')}", "row": row})
                elif obj_id in seg_ext:
                    highlights.append({"kind": "segment_team", "segment": obj_id, "pattern_name": pattern_name, "label": f"{obj_id}: team={row.get('teamSize', '?')}", "row": row})
            elif pattern_name.startswith("parallel_collaboration_segment"):
                segment1 = node_id(row.get("segment1"))
                segment2 = node_id(row.get("segment2"))
                if segment1 in seg_ext and segment2 in seg_ext:
                    highlights.append({"kind": "parallel_segments", "segment1": segment1, "segment2": segment2, "pattern_name": pattern_name, "label": f"{segment1} || {segment2}", "row": row})
            elif pattern_name.startswith("parallel_collaboration_mission"):
                mission1 = node_id(row.get("mission1"))
                mission2 = node_id(row.get("mission2"))
                if mission_id in {mission1, mission2}:
                    other = mission2 if mission1 == mission_id else mission1
                    highlights.append({"kind": "parallel_mission", "pattern_name": pattern_name, "label": f"parallel with {other}", "row": row})
            elif pattern_name.startswith("sync_diagnostics"):
                downstream_event = event_id_from_mapping(row.get("downstreamEvent")) or event_id_from_mapping(row.get("e_d"))
                if downstream_event in mission_event_ids:
                    highlights.append({"kind": "sync", "downstream_event_id": downstream_event, "pattern_name": pattern_name, "label": f"sync={format_seconds(row.get('syncDelay'))}", "row": row})
    return highlights


def build_timeline_summary(events: List[Dict[str, Any]], transitions: List[Dict[str, Any]], structural_highlights: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    robots = {str(event.get("robot_id") or "unassigned") for event in events}
    segments = {str(event.get("segment_id")) for event in events if event.get("segment_id")}
    families = Counter(pattern_family(item["pattern_name"]) for item in transitions + structural_highlights)
    return [
        {"label": "Mission events", "value": str(len(events)), "caption": "Concrete task executions", "accent": "#2563EB"},
        {"label": "Robots", "value": str(len(robots)), "caption": "Y-axis swimlanes", "accent": "#059669"},
        {"label": "Segments", "value": str(len(segments)), "caption": "Color-coded objective units", "accent": "#D97706"},
        {"label": "Pattern overlays", "value": str(sum(families.values())), "caption": "Links, bands, and markers", "accent": "#DC2626"},
    ]


def diagnostic_count(rows: List[Dict[str, Any]], count_key_candidates: List[str]) -> Optional[float]:
    if not rows:
        return 0
    for key in count_key_candidates:
        if key in rows[0]:
            total = 0.0
            found = False
            for row in rows:
                value = row.get(key)
                if isinstance(value, (int, float)):
                    total += float(value)
                    found = True
            if found:
                return total
    return None
