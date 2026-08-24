from __future__ import annotations
import random
from typing import List
from models import Mission, RobotState, ScenarioDefinition, Segment, TaskInstance


def build_robots(scenario: ScenarioDefinition, n_robots: int, rng: random.Random) -> List[RobotState]:
    robots = []
    for i in range(n_robots):
        profile = scenario.robot_profiles[i % len(scenario.robot_profiles)]
        robots.append(
            RobotState(
                robot_id=f"r{i+1}",
                capabilities=set(profile),
                location=(rng.uniform(0, scenario.world_size), rng.uniform(0, scenario.world_size)),
            )
        )
    return robots


def build_missions(
    scenario: ScenarioDefinition,
    n_missions: int,
    segments_per_mission: int,
    arrival_interval: float,
    rng: random.Random,
) -> List[Mission]:
    """Build missions matching the Mission/Segment semantics used in the paper.

    Each mission can contain:
      1. mission-level task executions (segment_id=None), and
      2. task executions belonging to a segment (segment_id set).

    Segment entities are explicitly represented and are always associated with
    exactly one mission. Prefix mission tasks precede all segment work; suffix
    mission tasks follow completion of all segments. Segment task sequences are
    ordered internally but distinct segments may execute concurrently.
    """
    missions: List[Mission] = []
    task_counter = 0

    def new_task(
        task_name: str,
        mission_id: str,
        segment_id: str | None,
        location,
        release_time: float,
        precedence: List[str],
    ) -> TaskInstance:
        nonlocal task_counter
        task_counter += 1
        return TaskInstance(
            task_id=f"t{task_counter}",
            task_name=task_name,
            mission_id=mission_id,
            segment_id=segment_id,
            location=location,
            release_time=release_time,
            precedence=list(precedence),
        )

    for m in range(n_missions):
        mission_id = f"m{m+1}"
        arrival = m * arrival_interval
        mission_location = (
            rng.uniform(0, scenario.world_size),
            rng.uniform(0, scenario.world_size),
        )

        direct_tasks: List[TaskInstance] = []
        prefix_last: str | None = None

        # Direct mission-level work: these tasks belong to the mission but not
        # to any segment.
        for task_name in scenario.mission_prefix_tasks:
            task = new_task(
                task_name,
                mission_id,
                None,
                mission_location,
                arrival,
                [prefix_last] if prefix_last else [],
            )
            direct_tasks.append(task)
            prefix_last = task.task_id

        segments: List[Segment] = []
        segment_last_tasks: List[str] = []
        for s in range(segments_per_mission):
            template = scenario.segment_templates[(m + s) % len(scenario.segment_templates)]
            segment_id = f"{mission_id}_s{s+1}"
            loc = (
                rng.uniform(0, scenario.world_size),
                rng.uniform(0, scenario.world_size),
            )
            segment = Segment(
                segment_id=segment_id,
                mission_id=mission_id,
                location=loc,
            )

            prev_task_id = prefix_last
            for task_name in template:
                task = new_task(
                    task_name,
                    mission_id,
                    segment_id,
                    loc,
                    arrival,
                    [prev_task_id] if prev_task_id else [],
                )
                segment.tasks.append(task)
                prev_task_id = task.task_id

            if prev_task_id:
                segment_last_tasks.append(prev_task_id)
            segments.append(segment)

        # Mission-level closing work depends on all segment branches, making it
        # directly mission-correlated without forcing it into an artificial
        # segment.
        suffix_predecessors = list(segment_last_tasks)
        for task_name in scenario.mission_suffix_tasks:
            task = new_task(
                task_name,
                mission_id,
                None,
                mission_location,
                arrival,
                suffix_predecessors,
            )
            direct_tasks.append(task)
            suffix_predecessors = [task.task_id]

        missions.append(
            Mission(
                mission_id=mission_id,
                arrival_time=arrival,
                direct_tasks=direct_tasks,
                segments=segments,
            )
        )

    return missions
