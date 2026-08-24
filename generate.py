#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
import random
from pathlib import Path

from allocation import ALLOCATORS
from allocation.hungarian import HungarianAllocator
from output import export_run
from output.exporter import export_ground_truth
from ground_truth import build_ground_truth
from scenarios import SCENARIOS
from scenarios.common import build_missions, build_robots
from simulation import Simulator


def parse_args():
    p = argparse.ArgumentParser(description="Synthetic multi-robot task/control log generator")
    p.add_argument("--scenario", choices=sorted(SCENARIOS), default="order_management")
    p.add_argument("--allocation", choices=sorted(ALLOCATORS), default="greedy")
    p.add_argument("--robots", type=int, default=8)
    p.add_argument("--missions", type=int, default=20)
    p.add_argument("--segments-per-mission", type=int, default=3)
    p.add_argument("--arrival-interval", type=float, default=10.0,
                   help="Time between mission releases; smaller values create more overlap")
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--battery-threshold", type=float, default=15.0)
    p.add_argument("--recharge-duration", type=float, default=30.0)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--start-time", default="2026-01-01T08:00:00",
                   help="Base timestamp for exported logs, ISO form YYYY-MM-DDTHH:MM:SS")
    p.add_argument("--control-context", action="store_true",
                   help="Also populate mission_id/segment_id on control events (off by default)")
    p.add_argument("--out", default="generated_run")
    return p.parse_args()


def main():
    args = parse_args()
    if args.robots < 1 or args.missions < 1 or args.segments_per_mission < 1:
        raise SystemExit("robots, missions, and segments-per-mission must be positive")

    # Separate random streams: workload geometry and allocator randomness must
    # not perturb execution-duration noise across strategies.
    workload_rng = random.Random(args.seed)
    allocation_rng = random.Random(args.seed ^ 0x5EED5EED)
    scenario = SCENARIOS[args.scenario]
    robots = build_robots(scenario, args.robots, workload_rng)
    missions = build_missions(
        scenario,
        args.missions,
        args.segments_per_mission,
        args.arrival_interval,
        workload_rng,
    )

    allocator_cls = ALLOCATORS[args.allocation]
    allocator = allocator_cls(allocation_rng) if args.allocation == "random" else allocator_cls()

    simulator = Simulator(
        scenario=scenario,
        robots=robots,
        allocator=allocator,
        rng=random.Random(args.seed ^ 0xC0FFEE),
        speed=args.speed,
        battery_threshold=args.battery_threshold,
        recharge_duration=args.recharge_duration,
        control_context=args.control_context,
        simulation_seed=args.seed,
    )
    events = simulator.run(missions)

    config = vars(args).copy()
    config["scenario_definition"] = scenario.name
    config["event_count"] = len(events)
    config["task_event_count"] = sum(e.event_type == "Task" for e in events)
    config["control_event_count"] = sum(e.event_type == "Control" for e in events)
    export_run(args.out, scenario, robots, missions, events, config)

    gt_assignments, gt_df, gt_patterns, gt_summary = build_ground_truth(
        scenario, robots, missions, events, simulator.assignments
    )
    export_ground_truth(
        args.out,
        gt_assignments,
        gt_df,
        gt_patterns,
        gt_summary,
        config.get("start_time", "2026-01-01T08:00:00"),
    )
    config["ground_truth_pattern_count"] = len(gt_patterns)
    # Refresh config after adding ground-truth metadata.
    with (Path(args.out) / "config.json").open("w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, sort_keys=True)

    print(json.dumps({
        "out": str(Path(args.out).resolve()),
        "scenario": args.scenario,
        "allocation": args.allocation,
        "events": len(events),
        "tasks": config["task_event_count"],
        "controls": config["control_event_count"],
    }, indent=2))


if __name__ == "__main__":
    main()
