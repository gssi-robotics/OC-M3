from __future__ import annotations

from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Tuple

from models import Event, Mission, RobotState, ScenarioDefinition

EPS = 1e-9


def _derive_interval_df(events: Iterable[Event]) -> List[Tuple[Event, Event]]:
    """Reproduce init_ekg.py's interval-based DF semantics exactly."""
    evs = list(events)
    result: List[Tuple[Event, Event]] = []
    for e1 in evs:
        for e2 in evs:
            if e1.event_id == e2.event_id or e1.end_time > e2.start_time + EPS:
                continue
            blocked = False
            for e3 in evs:
                if e3.event_id in {e1.event_id, e2.event_id}:
                    continue
                if e1.end_time <= e3.start_time + EPS and e3.end_time <= e2.start_time + EPS:
                    blocked = True
                    break
            if not blocked:
                result.append((e1, e2))
    result.sort(key=lambda x: (x[0].start_time, x[0].event_id, x[1].start_time, x[1].event_id))
    return result


def _overlap(a: Event, b: Event) -> bool:
    return a.start_time < b.end_time - EPS and b.start_time < a.end_time - EPS


def build_ground_truth(
    scenario: ScenarioDefinition,
    robots: List[RobotState],
    missions: List[Mission],
    events: List[Event],
    assignments: List[dict],
):
    """Build latent ground truth using the same semantics as OC-M3's EKG queries.

    Ground truth is derived from simulator state/events before EKG construction.
    It deliberately mirrors the EKG DF definition and the four formal analysis
    structures used by the paper: robot handover, objective switch,
    capability-driven return, and parallel collaboration.
    """
    task_events = [e for e in events if e.event_type == "Task"]
    event_by_id = {e.event_id: e for e in task_events}
    task_event_by_task = {e.task_id: e for e in task_events if e.task_id}
    robot_caps = {r.robot_id: set(r.capabilities) for r in robots}

    assignment_rows = []
    for a in assignments:
        event = task_event_by_task[a["task_id"]]
        assignment_rows.append({
            **a,
            "event_id": event.event_id,
            "start_time": event.start_time,
            "end_time": event.end_time,
        })

    # Perspective memberships exactly as loaded into the EKG.
    objective_events: Dict[Tuple[str, str], List[Event]] = defaultdict(list)
    robot_events: Dict[str, List[Event]] = defaultdict(list)
    for e in task_events:
        objective_events[("mission", e.mission_id)].append(e)
        if e.segment_id:
            objective_events[("segment", e.segment_id)].append(e)
        robot_events[e.robot_id].append(e)

    objective_df: Dict[Tuple[str, str], List[Tuple[Event, Event]]] = {
        key: _derive_interval_df(evs) for key, evs in objective_events.items()
    }
    robot_df: Dict[str, List[Tuple[Event, Event]]] = {
        rid: _derive_interval_df(evs) for rid, evs in robot_events.items()
    }

    df_rows = []
    for (scope, objective_id), edges in sorted(objective_df.items()):
        for src, dst in edges:
            df_rows.append({
                "perspective_type": scope,
                "perspective_id": objective_id,
                "source_event_id": src.event_id,
                "source_task_id": src.task_id,
                "source_robot_id": src.robot_id,
                "target_event_id": dst.event_id,
                "target_task_id": dst.task_id,
                "target_robot_id": dst.robot_id,
            })
    for rid, edges in sorted(robot_df.items()):
        for src, dst in edges:
            df_rows.append({
                "perspective_type": "robot",
                "perspective_id": rid,
                "source_event_id": src.event_id,
                "source_task_id": src.task_id,
                "source_robot_id": src.robot_id,
                "target_event_id": dst.event_id,
                "target_task_id": dst.task_id,
                "target_robot_id": dst.robot_id,
            })

    pattern_rows: List[dict] = []

    # --- Robot handover -------------------------------------------------
    # Includes the two non-overlap guards from robot_handover() in
    # collab_patterns.py, not merely a different-robot objective DF edge.
    robot_df_out = defaultdict(list)
    robot_df_in = defaultdict(list)
    for rid, edges in robot_df.items():
        for src, dst in edges:
            robot_df_out[(rid, src.event_id)].append(dst)
            robot_df_in[(rid, dst.event_id)].append(src)

    for (scope, objective_id), edges in objective_df.items():
        obj_event_ids = {e.event_id for e in objective_events[(scope, objective_id)]}
        for e1, e2 in edges:
            if e1.robot_id == e2.robot_id:
                continue
            source_conflict = any(
                ek.event_id in obj_event_ids and _overlap(ek, e2)
                for ek in robot_df_out.get((e1.robot_id, e1.event_id), [])
            )
            target_conflict = any(
                el.event_id in obj_event_ids and _overlap(el, e1)
                for el in robot_df_in.get((e2.robot_id, e2.event_id), [])
            )
            if source_conflict or target_conflict:
                continue
            pattern_rows.append({
                "pattern_type": "handover",
                "objective_type": scope,
                "objective_id": objective_id,
                "prev_event_id": e1.event_id,
                "next_event_id": e2.event_id,
                "from_robot_id": e1.robot_id,
                "to_robot_id": e2.robot_id,
            })

    # --- Objective switch -----------------------------------------------
    for rid, edges in robot_df.items():
        for e1, e2 in edges:
            for scope in ("mission", "segment"):
                if scope == "mission":
                    o1, o2 = e1.mission_id, e2.mission_id
                else:
                    o1, o2 = e1.segment_id, e2.segment_id
                if not o1 or not o2 or o1 == o2:
                    continue
                pattern_rows.append({
                    "pattern_type": "objective_switch",
                    "objective_type": scope,
                    "robot_id": rid,
                    "prev_event_id": e1.event_id,
                    "next_event_id": e2.event_id,
                    "from_objective_id": o1,
                    "to_objective_id": o2,
                })

    # --- Capability-driven return ---------------------------------------
    for (scope, objective_id), edges in objective_df.items():
        outgoing = defaultdict(list)
        for e1, e2 in edges:
            outgoing[e1.event_id].append(e2)
        seen = set()
        for e1, e2 in edges:
            for e3 in outgoing.get(e2.event_id, []):
                if e1.robot_id != e3.robot_id or e1.robot_id == e2.robot_id:
                    continue
                required = set(scenario.task_specs[e2.activity].required_capabilities)
                missing = sorted(required - robot_caps[e1.robot_id])
                if not missing:
                    continue
                key = (scope, objective_id, e1.event_id, e2.event_id, e3.event_id)
                if key in seen:
                    continue
                seen.add(key)
                pattern_rows.append({
                    "pattern_type": "capability_return",
                    "objective_type": scope,
                    "objective_id": objective_id,
                    "event_i": e1.event_id,
                    "event_j": e2.event_id,
                    "event_k": e3.event_id,
                    "returning_robot_id": e1.robot_id,
                    "intermediate_robot_id": e2.robot_id,
                    "reason_capability": "|".join(missing),
                })

    # --- Parallel collaboration -----------------------------------------
    # Same envelope/team semantics as parallel_collaboration_*() queries.
    mission_by_id = {m.mission_id: m for m in missions}
    mission_ids = sorted(mission_by_id)
    for i, m1 in enumerate(mission_ids):
        ev1 = objective_events[("mission", m1)]
        if not ev1:
            continue
        start1, end1 = min(e.start_time for e in ev1), max(e.end_time for e in ev1)
        team1 = {e.robot_id for e in ev1}
        for m2 in mission_ids[i + 1:]:
            ev2 = objective_events[("mission", m2)]
            if not ev2:
                continue
            start2, end2 = min(e.start_time for e in ev2), max(e.end_time for e in ev2)
            team2 = {e.robot_id for e in ev2}
            if start1 < end2 - EPS and start2 < end1 - EPS and len(team1 | team2) > 1:
                pattern_rows.append({
                    "pattern_type": "parallel_collaboration",
                    "objective_type": "mission",
                    "objective_1": m1,
                    "objective_2": m2,
                    "mission_id": "",
                    "overlap_start": max(start1, start2),
                    "overlap_end": min(end1, end2),
                })

    for mission in missions:
        segment_ids = sorted(s.segment_id for s in mission.segments)
        for i, s1 in enumerate(segment_ids):
            ev1 = objective_events[("segment", s1)]
            if not ev1:
                continue
            start1, end1 = min(e.start_time for e in ev1), max(e.end_time for e in ev1)
            team1 = {e.robot_id for e in ev1}
            for s2 in segment_ids[i + 1:]:
                ev2 = objective_events[("segment", s2)]
                if not ev2:
                    continue
                start2, end2 = min(e.start_time for e in ev2), max(e.end_time for e in ev2)
                team2 = {e.robot_id for e in ev2}
                if start1 < end2 - EPS and start2 < end1 - EPS and len(team1 | team2) > 1:
                    pattern_rows.append({
                        "pattern_type": "parallel_collaboration",
                        "objective_type": "segment",
                        "objective_1": s1,
                        "objective_2": s2,
                        "mission_id": mission.mission_id,
                        "overlap_start": max(start1, start2),
                        "overlap_end": min(end1, end2),
                    })

    pattern_rows.sort(key=lambda r: (
        r.get("pattern_type", ""), r.get("objective_type", ""),
        r.get("objective_id", ""), r.get("robot_id", ""),
        r.get("prev_event_id", r.get("event_i", r.get("objective_1", ""))),
        r.get("next_event_id", r.get("event_j", r.get("objective_2", ""))),
    ))
    summary_counter = Counter((r["pattern_type"], r["objective_type"]) for r in pattern_rows)
    summary_rows = [
        {"pattern_type": p, "objective_type": s, "occurrences": n}
        for (p, s), n in sorted(summary_counter.items())
    ]
    return assignment_rows, df_rows, pattern_rows, summary_rows
