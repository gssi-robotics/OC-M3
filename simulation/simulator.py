from __future__ import annotations
import hashlib
import math
import random
from typing import Dict, List, Set

from models import Event, Mission, RobotState, ScenarioDefinition, TaskInstance


class Simulator:
    def __init__(
        self,
        scenario: ScenarioDefinition,
        robots: List[RobotState],
        allocator,
        rng: random.Random,
        speed: float = 1.0,
        battery_threshold: float = 15.0,
        recharge_duration: float = 30.0,
        control_context: bool = False,
        simulation_seed: int = 42,
    ):
        self.scenario = scenario
        self.robots = robots
        self.allocator = allocator
        self.rng = rng
        self.speed = speed
        self.battery_threshold = battery_threshold
        self.recharge_duration = recharge_duration
        self.control_context = control_context
        self.simulation_seed = int(simulation_seed)
        self.events: List[Event] = []
        self._event_counter = 0
        self.completed: Set[str] = set()
        self.task_end: Dict[str, float] = {}
        self.assignments: List[dict] = []

    def _next_event_id(self) -> str:
        self._event_counter += 1
        return f"e{self._event_counter}"

    def _add_event(self, event_type, activity, start, end, robot, task=None):
        mission_id = task.mission_id if (task is not None and (event_type == "Task" or self.control_context)) else None
        segment_id = task.segment_id if (task is not None and (event_type == "Task" or self.control_context)) else None
        event = Event(
            event_id=self._next_event_id(),
            event_type=event_type,
            activity=activity,
            start_time=start,
            end_time=end,
            robot_id=robot.robot_id,
            mission_id=mission_id,
            segment_id=segment_id,
            task_id=task.task_id if event_type == "Task" and task is not None else None,
        )
        self.events.append(event)
        return event

    def _uniform_for_key(self, key: str) -> float:
        """Stable U[0,1) variate used for common-random-number experiments.

        A task/control duration therefore depends on the run seed and the logical
        event identity, not on allocator-dependent execution order. Comparing two
        allocation strategies with the same seed keeps exogenous duration noise
        fixed while still allowing travel/waiting to change endogenously.
        """
        payload = f"{self.simulation_seed}|{key}".encode("utf-8")
        digest = hashlib.sha256(payload).digest()
        value = int.from_bytes(digest[:8], "big")
        return value / float(1 << 64)

    def _duration(self, mean: float, jitter: float = 0.2, *, key: str) -> float:
        lo, hi = mean * (1 - jitter), mean * (1 + jitter)
        u = self._uniform_for_key(key)
        return round(max(0.1, lo + (hi - lo) * u), 2)

    def _execute_control(
        self,
        robot: RobotState,
        activity: str,
        task: TaskInstance,
        current: float,
        sequence_index: int,
    ) -> float:
        key = f"control|{task.task_id}|{sequence_index}|{activity}"
        if activity == "navigate":
            distance = math.dist(robot.location, task.location)
            duration = max(0.5, distance / self.speed)
            robot.battery -= 0.05 * distance
            robot.location = task.location
        elif activity in {"clean_path"}:
            duration = self._duration(8.0, key=key)
            robot.battery -= 1.0
        elif activity in {"grasp", "release", "activate_sprayer"}:
            duration = self._duration(3.0, key=key)
            robot.battery -= 0.4
        else:
            duration = self._duration(2.0, key=key)
            robot.battery -= 0.2
        self._add_event("Control", activity, current, current + duration, robot, task)
        return current + duration

    def _maybe_recharge(self, robot: RobotState, task: TaskInstance, current: float) -> float:
        if robot.battery >= self.battery_threshold:
            return current
        self._add_event("Control", "dock", current, current + 2.0, robot, task)
        current += 2.0
        self._add_event("Control", "recharge", current, current + self.recharge_duration, robot, task)
        current += self.recharge_duration
        self._add_event("Control", "undock", current, current + 2.0, robot, task)
        current += 2.0
        robot.battery = 100.0
        return current

    def _earliest_task_time(self, task: TaskInstance) -> float:
        if not task.precedence:
            return task.release_time
        return max(task.release_time, max(self.task_end[p] for p in task.precedence))

    def _execute_task(self, task: TaskInstance, robot: RobotState):
        spec = self.scenario.task_specs[task.task_name]
        decision_time = self._earliest_task_time(task)
        current = max(robot.available_at, decision_time)
        current = self._maybe_recharge(robot, task, current)
        for i, ctrl in enumerate(spec.control_sequence):
            current = self._execute_control(robot, ctrl, task, current, i)
        task_duration = self._duration(
            spec.duration_mean,
            spec.duration_jitter,
            key=f"task|{task.task_id}|{task.task_name}",
        )
        task_event = self._add_event("Task", task.task_name, current, current + task_duration, robot, task)
        self.assignments.append({
            "task_id": task.task_id,
            "task_name": task.task_name,
            "mission_id": task.mission_id,
            "segment_id": task.segment_id or "",
            "task_scope": "segment" if task.segment_id else "mission",
            "robot_id": robot.robot_id,
            "decision_time": decision_time,
            "allocation_strategy": getattr(self.allocator, "name", self.allocator.__class__.__name__),
        })
        current += task_duration
        robot.available_at = current
        robot.current_mission = task.mission_id
        robot.current_segment = task.segment_id
        robot.battery -= max(0.2, task_duration * 0.03)
        self.completed.add(task.task_id)
        self.task_end[task.task_id] = current

    def run(self, missions: List[Mission]) -> List[Event]:
        all_tasks = [t for m in missions for t in m.tasks]
        pending = {t.task_id: t for t in all_tasks}

        while pending:
            ready = [
                t for t in pending.values()
                if all(p in self.completed for p in t.precedence)
            ]
            if not ready:
                raise RuntimeError("No ready tasks; check precedence graph for cycles")

            ready.sort(key=lambda t: (self._earliest_task_time(t), t.mission_id, t.segment_id or "", t.task_id))
            now = self._earliest_task_time(ready[0])
            simultaneous = [t for t in ready if abs(self._earliest_task_time(t) - now) < 1e-9]

            assigned_ids = set()
            if hasattr(self.allocator, "assign_batch"):
                # Use the same decision-epoch interface for every allocator that
                # supports it. This avoids giving only Hungarian a one-to-one
                # batch semantics while others are evaluated sequentially.
                batch = simultaneous[: len(self.robots)]
                assignments = self.allocator.assign_batch(batch, self.scenario.task_specs, self.robots, now)
                for task in batch:
                    robot = assignments.get(task.task_id)
                    if robot is not None:
                        self._execute_task(task, robot)
                        assigned_ids.add(task.task_id)
            else:
                for task in simultaneous:
                    spec = self.scenario.task_specs[task.task_name]
                    robot = self.allocator.choose_robot(task, spec, self.robots, now)
                    self._execute_task(task, robot)
                    assigned_ids.add(task.task_id)

            if not assigned_ids:
                task = ready[0]
                spec = self.scenario.task_specs[task.task_name]
                robot = self.allocator.choose_robot(task, spec, self.robots, now)
                self._execute_task(task, robot)
                assigned_ids.add(task.task_id)

            for task_id in assigned_ids:
                pending.pop(task_id, None)

        self.events.sort(key=lambda e: (e.start_time, e.end_time, e.event_id))
        return self.events
