from __future__ import annotations
import random
from .base import Allocator


class RandomAllocator(Allocator):
    """Random feasible baseline, with one robot per task in a decision epoch."""
    name = "random"

    def __init__(self, rng: random.Random):
        self.rng = rng

    def choose_robot(self, task, spec, robots, now):
        feasible = self.feasible(spec, robots)
        if not feasible:
            raise RuntimeError(f"No feasible robot for task {task.task_name}")
        return self.rng.choice(feasible)

    def assign_batch(self, tasks, specs, robots, now):
        remaining = list(robots)
        result = {}
        for task in tasks:
            feasible = self.feasible(specs[task.task_name], remaining)
            if not feasible:
                continue
            robot = self.rng.choice(feasible)
            result[task.task_id] = robot
            remaining.remove(robot)
        return result
