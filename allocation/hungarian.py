from __future__ import annotations
import math
from .base import Allocator


class HungarianAllocator(Allocator):
    """
    Rolling-horizon single-task wrapper.

    The simulator asks for one assignment at a time; to preserve a clear
    Hungarian-style global cost interpretation without changing the simulator
    interface, this allocator uses the same centralized cost model as a
    one-row assignment matrix. Batch assignment is exposed in assign_batch().
    """
    name = "hungarian"

    def choose_robot(self, task, spec, robots, now):
        feasible = self.feasible(spec, robots)
        if not feasible:
            raise RuntimeError(f"No feasible robot for task {task.task_name}")
        return min(feasible, key=lambda r: math.dist(r.location, task.location) + max(0.0, r.available_at - now))

    def assign_batch(self, tasks, specs, robots, now):
        from scipy.optimize import linear_sum_assignment
        import numpy as np

        big = 1e9
        matrix = np.full((len(tasks), len(robots)), big, dtype=float)
        for i, task in enumerate(tasks):
            req = set(specs[task.task_name].required_capabilities)
            for j, robot in enumerate(robots):
                if req.issubset(robot.capabilities):
                    matrix[i, j] = math.dist(robot.location, task.location) + max(0.0, robot.available_at - now)
        rows, cols = linear_sum_assignment(matrix)
        return {tasks[i].task_id: robots[j] for i, j in zip(rows, cols) if matrix[i, j] < big}
