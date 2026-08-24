from __future__ import annotations
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path


def _parse_base_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _format_time(base_time: datetime, seconds: float) -> str:
    # Requested format YY:mm:ddTHH:mm:ss.ff (two fractional-second digits).
    return (base_time + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-4]


def export_run(out_dir, scenario, robots, missions, events, config):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    base_time = _parse_base_time(config.get("start_time", "2026-01-01T08:00:00"))

    with (out / "events.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["event_id", "event_type", "activity", "start_time", "end_time", "robot_id", "mission_id", "segment_id", "task_id"])
        for e in events:
            w.writerow([
                e.event_id,
                e.event_type,
                e.activity,
                _format_time(base_time, e.start_time),
                _format_time(base_time, e.end_time),
                e.robot_id,
                e.mission_id or "",
                e.segment_id or "",
                e.task_id or "",
            ])

    # Requested normalized task-requirement table.
    with (out / "task_requirements.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["task_name", "required_capability"])
        for task_name, spec in sorted(scenario.task_specs.items()):
            for capability in spec.required_capabilities:
                w.writerow([task_name, capability])

    with (out / "task_table.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["task_name", "required_capability"])
        for task_name, spec in sorted(scenario.task_specs.items()):
            for capability in spec.required_capabilities:
                w.writerow([task_name, capability])

    with (out / "robots.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["robot_id", "capability"])
        for r in robots:
            for capability in sorted(r.capabilities):
                w.writerow([r.robot_id, capability])

    # One-row-per-robot representation consumed directly by the OC-M3 loader.
    with (out / "robot_table.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["robot_id", "capabilities"])
        for r in robots:
            w.writerow([r.robot_id, ";".join(sorted(r.capabilities))])

    # Explicit mission entities.
    with (out / "missions.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["mission_id", "arrival_time"])
        for mission in missions:
            w.writerow([mission.mission_id, _format_time(base_time, mission.arrival_time)])

    # Explicit Segment -> Mission relation. This avoids deriving the parent
    # mission from the segment identifier when constructing the EKG.
    with (out / "segments.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["segment_id", "mission_id", "location_x", "location_y"])
        for mission in missions:
            for segment in mission.segments:
                w.writerow([
                    segment.segment_id,
                    segment.mission_id,
                    round(segment.location[0], 4),
                    round(segment.location[1], 4),
                ])

    with (out / "tasks.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["task_id", "task_name", "mission_id", "segment_id", "task_scope", "location_x", "location_y", "release_time"])
        for mission in missions:
            for t in mission.tasks:
                w.writerow([
                    t.task_id,
                    t.task_name,
                    t.mission_id,
                    t.segment_id or "",
                    "segment" if t.segment_id else "mission",
                    round(t.location[0], 4),
                    round(t.location[1], 4),
                    _format_time(base_time, t.release_time),
                ])

    with (out / "precedence.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["predecessor_task_id", "successor_task_id"])
        for mission in missions:
            for t in mission.tasks:
                for pred in t.precedence:
                    w.writerow([pred, t.task_id])

    with (out / "config.json").open("w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, sort_keys=True)

    # Ready-to-use loader configuration for the analysis repository. Absolute
    # paths are intentional for immediate local execution; regenerate a run if
    # the folder is moved to another machine.
    events_path = str((out / "events.csv").resolve())
    robot_path = str((out / "robot_table.csv").resolve())
    task_path = str((out / "task_table.csv").resolve())
    log_name = f"{config.get('scenario', scenario.name)}_{config.get('allocation', 'allocation')}_seed{config.get('seed', '')}"
    loader_config = {
        "log_name": log_name,
        "event_id": "event_id",
        "event_activity": "activity",
        "event_type": "event_type",
        "event_start": "start_time",
        "event_end": "end_time",
        "entity_id": "entity_id",
        "entity_type_id": "type",
        "events": {
            "path": events_path,
            "attr": ["event_id", "event_type", "activity", "start_time", "end_time", "robot_id", "mission_id", "segment_id", "task_id"],
            "attr_types": {
                "event_id": "String", "event_type": "String", "activity": "String",
                "start_time": "Datetime", "end_time": "Datetime", "robot_id": "String",
                "mission_id": "String", "segment_id": "String", "task_id": "String",
            },
            "entity_columns": {"Robot": "robot_id", "Mission": "mission_id", "Segment": "segment_id"},
        },
        "entities": {
            "robot_id": {
                "type": "Robot", "path": robot_path, "event_column": "robot_id",
                "entity_id": "robot_id", "attr": ["robot_id", "capabilities"],
                "attr_types": {"robot_id": "String", "capabilities": "String"},
                "from_event_table": False,
            },
            "mission_id": {
                "type": "Mission", "path": events_path, "event_column": "mission_id",
                "entity_id": "mission_id", "attr": ["mission_id"],
                "attr_types": {"mission_id": "String"}, "from_event_table": True,
            },
            "segment_id": {
                "type": "Segment", "path": events_path, "event_column": "segment_id",
                "entity_id": "segment_id", "attr": ["segment_id"],
                "attr_types": {"segment_id": "String"}, "from_event_table": True,
            },
        },
        "task_capabilities": {
            "path": task_path, "task_column": "task_name", "capability_column": "required_capability",
            "attr": ["task_name", "required_capability"],
            "attr_types": {"task_name": "String", "required_capability": "String"},
        },
    }
    with (out / "ekg_loader_config.json").open("w", encoding="utf-8") as f:
        json.dump(loader_config, f, indent=2, sort_keys=True)


def export_ground_truth(out_dir, assignment_rows, df_rows, pattern_rows, summary_rows, base_time_str):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    base_time = _parse_base_time(base_time_str)

    with (out / "ground_truth_assignments.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "task_id", "task_name", "mission_id", "segment_id", "task_scope",
            "robot_id", "allocation_strategy", "decision_time", "event_id",
            "start_time", "end_time",
        ])
        for r in assignment_rows:
            w.writerow([
                r["task_id"], r["task_name"], r["mission_id"], r["segment_id"],
                r["task_scope"], r["robot_id"], r["allocation_strategy"],
                _format_time(base_time, r["decision_time"]), r["event_id"],
                _format_time(base_time, r["start_time"]), _format_time(base_time, r["end_time"]),
            ])

    with (out / "ground_truth_df.csv").open("w", newline="", encoding="utf-8") as f:
        fields = [
            "perspective_type", "perspective_id", "source_event_id", "source_task_id",
            "source_robot_id", "target_event_id", "target_task_id", "target_robot_id",
        ]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(df_rows)

    # Generic union table retained for inspection/debugging.
    generic_fields = [
        "pattern_type", "objective_type", "objective_id",
        "prev_event_id", "next_event_id", "from_robot_id", "to_robot_id",
        "robot_id", "from_objective_id", "to_objective_id",
        "event_i", "event_j", "event_k", "returning_robot_id",
        "intermediate_robot_id", "reason_capability",
        "objective_1", "objective_2", "mission_id", "overlap_start", "overlap_end",
    ]
    with (out / "ground_truth_patterns.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=generic_fields, extrasaction="ignore")
        w.writeheader()
        for row in pattern_rows:
            exported = dict(row)
            for key in ("overlap_start", "overlap_end"):
                if key in exported and exported[key] != "":
                    exported[key] = _format_time(base_time, exported[key])
            w.writerow(exported)

    with (out / "ground_truth_summary.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["pattern_type", "objective_type", "occurrences"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(summary_rows)

    # Paper-facing files use exactly the schemas consumed by
    # query-lib/evaluate_pattern_correctness.py.
    specs = {
        "handover": (
            "handover_occurrences.csv",
            ["objective_type", "objective_id", "prev_event_id", "next_event_id", "from_robot_id", "to_robot_id"],
        ),
        "objective_switch": (
            "switch_occurrences.csv",
            ["objective_type", "robot_id", "prev_event_id", "next_event_id", "from_objective_id", "to_objective_id"],
        ),
        "capability_return": (
            "capability_return_occurrences.csv",
            ["objective_type", "objective_id", "event_i", "event_j", "event_k", "returning_robot_id", "intermediate_robot_id", "reason_capability"],
        ),
        "parallel_collaboration": (
            "parallel_occurrences.csv",
            ["objective_type", "objective_1", "objective_2", "mission_id", "overlap_start", "overlap_end"],
        ),
    }
    for pattern_type, (filename, fields) in specs.items():
        rows = [r for r in pattern_rows if r.get("pattern_type") == pattern_type]
        with (out / filename).open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for row in rows:
                exported = {k: row.get(k, "") for k in fields}
                for key in ("overlap_start", "overlap_end"):
                    if key in exported and exported[key] != "":
                        exported[key] = _format_time(base_time, exported[key])
                w.writerow(exported)
