from __future__ import annotations
import math
from .base import Allocator


class GreedyAllocator(Allocator):
    """Greedy nearest/earliest-feasible assignment baseline."""
    name = "greedy"

    @staticmethod
    def _cost(r, task, now):
        dist = math.dist(r.location, task.location)
        wait = max(0.0, r.available_at - now)
        return dist + wait

    def choose_robot(self, task, spec, robots, now):
        feasible = self.feasible(spec, robots)
        if not feasible:
            raise RuntimeError(f"No feasible robot for task {task.task_name}")
        return min(feasible, key=lambda r: self._cost(r, task, now))

    def assign_batch(self, tasks, specs, robots, now):
        remaining = list(robots)
        result = {}
        # Deterministic task order keeps the greedy heuristic reproducible.
        for task in tasks:
            feasible = self.feasible(specs[task.task_name], remaining)
            if not feasible:
                continue
            robot = min(feasible, key=lambda r: self._cost(r, task, now))
            result[task.task_id] = robot
            remaining.remove(robot)
        return result
