#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path


DEFAULT_SCENARIOS = ["agriculture", "cleaning", "order_management"]
DEFAULT_ALLOCATIONS = ["random", "greedy", "hungarian", "auction"]
DEFAULT_SEEDS = list(range(1, 11))


def parse_args():
    p = argparse.ArgumentParser(
        description="Run the complete scenario/allocation/seed experiment matrix."
    )
    p.add_argument("--scenarios", nargs="+", choices=DEFAULT_SCENARIOS, default=DEFAULT_SCENARIOS)
    p.add_argument("--allocations", nargs="+", choices=DEFAULT_ALLOCATIONS, default=DEFAULT_ALLOCATIONS)
    p.add_argument(
        "--seeds", nargs="+", type=int, default=DEFAULT_SEEDS,
        help="Repeated seeds (default: 1..10).",
    )
    p.add_argument("--robots", type=int, default=8)
    p.add_argument("--missions", type=int, default=20)
    p.add_argument("--segments-per-mission", type=int, default=3)
    p.add_argument("--arrival-interval", type=float, default=10.0)
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--battery-threshold", type=float, default=15.0)
    p.add_argument("--recharge-duration", type=float, default=30.0)
    p.add_argument("--start-time", default="2026-01-01T08:00:00")
    p.add_argument("--control-context", action="store_true")
    p.add_argument("--out", default="output/all_runs")
    p.add_argument("--continue-on-error", action="store_true")
    return p.parse_args()


def _collect_run_summary(run_out: Path, scenario: str, allocation: str, seed: int) -> dict:
    config = json.loads((run_out / "config.json").read_text(encoding="utf-8"))
    row = {
        "run": run_out.name,
        "scenario": scenario,
        "allocation": allocation,
        "seed": seed,
        "robots": config["robots"],
        "missions": config["missions"],
        "segments_per_mission": config["segments_per_mission"],
        "arrival_interval": config["arrival_interval"],
        "task_events": config["task_event_count"],
        "control_events": config["control_event_count"],
    }
    with (run_out / "ground_truth_summary.csv").open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            key = f"{r['pattern_type']}_{r['objective_type']}"
            row[key] = int(r["occurrences"])
    return row


def main():
    args = parse_args()
    if args.robots < 1 or args.missions < 1 or args.segments_per_mission < 1:
        raise SystemExit("robots, missions, and segments-per-mission must be positive")

    root = Path(__file__).resolve().parent
    generate_py = root / "generate.py"
    out_root = Path(args.out)
    if not out_root.is_absolute():
        out_root = root / out_root
    out_root.mkdir(parents=True, exist_ok=True)

    total = len(args.scenarios) * len(args.allocations) * len(args.seeds)
    completed = 0
    failed = []
    manifest_rows = []
    print(f"Running {total} configurations...")

    for scenario in args.scenarios:
        for allocation in args.allocations:
            for seed in args.seeds:
                completed += 1
                run_name = f"{scenario}_{allocation}_seed{seed}"
                run_out = out_root / run_name
                cmd = [
                    sys.executable, str(generate_py),
                    "--scenario", scenario,
                    "--allocation", allocation,
                    "--robots", str(args.robots),
                    "--missions", str(args.missions),
                    "--segments-per-mission", str(args.segments_per_mission),
                    "--arrival-interval", str(args.arrival_interval),
                    "--speed", str(args.speed),
                    "--battery-threshold", str(args.battery_threshold),
                    "--recharge-duration", str(args.recharge_duration),
                    "--seed", str(seed),
                    "--start-time", args.start_time,
                    "--out", str(run_out),
                ]
                if args.control_context:
                    cmd.append("--control-context")

                print(f"[{completed}/{total}] {scenario} | {allocation} | seed={seed} -> {run_out}")
                result = subprocess.run(cmd, cwd=root)
                if result.returncode != 0:
                    failed.append(run_name)
                    if not args.continue_on_error:
                        raise SystemExit(
                            f"Run failed: {run_name}. Use --continue-on-error to keep running."
                        )
                    continue
                manifest_rows.append(_collect_run_summary(run_out, scenario, allocation, seed))

    if manifest_rows:
        all_fields = [
            "run", "scenario", "allocation", "seed", "robots", "missions",
            "segments_per_mission", "arrival_interval", "task_events", "control_events",
        ]
        extra = sorted({k for r in manifest_rows for k in r if k not in all_fields})
        with (out_root / "experiment_summary.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=all_fields + extra)
            w.writeheader()
            w.writerows(manifest_rows)

    experiment_config = {
        "scenarios": args.scenarios,
        "allocations": args.allocations,
        "seeds": args.seeds,
        "robots": args.robots,
        "missions": args.missions,
        "segments_per_mission": args.segments_per_mission,
        "arrival_interval": args.arrival_interval,
        "speed": args.speed,
        "battery_threshold": args.battery_threshold,
        "recharge_duration": args.recharge_duration,
        "start_time": args.start_time,
        "control_context": args.control_context,
        "failed_runs": failed,
    }
    (out_root / "experiment_config.json").write_text(
        json.dumps(experiment_config, indent=2, sort_keys=True), encoding="utf-8"
    )

    print("\nFinished.")
    print(f"Successful runs: {total - len(failed)}/{total}")
    print(f"Output root: {out_root.resolve()}")
    if failed:
        print("Failed runs:")
        for name in failed:
            print(f"  - {name}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
