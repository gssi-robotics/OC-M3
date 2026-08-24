from __future__ import annotations
import math
from .base import Allocator


class AuctionAllocator(Allocator):
    """Simple market/Contract-Net-style allocator using minimum feasible bid."""
    name = "auction"

    @staticmethod
    def _bid(r, task, now):
        travel = math.dist(r.location, task.location)
        waiting = max(0.0, r.available_at - now)
        switching = 10.0 if r.current_mission not in (None, task.mission_id) else 0.0
        return travel + waiting + switching

    def choose_robot(self, task, spec, robots, now):
        feasible = self.feasible(spec, robots)
        if not feasible:
            raise RuntimeError(f"No feasible robot for task {task.task_name}")
        return min(feasible, key=lambda r: self._bid(r, task, now))

    def assign_batch(self, tasks, specs, robots, now):
        remaining_tasks = list(tasks)
        remaining_robots = list(robots)
        result = {}
        while remaining_tasks and remaining_robots:
            bids = []
            for task in remaining_tasks:
                for robot in self.feasible(specs[task.task_name], remaining_robots):
                    bids.append((self._bid(robot, task, now), task.task_id, robot.robot_id, task, robot))
            if not bids:
                break
            _, _, _, task, robot = min(bids, key=lambda x: (x[0], x[1], x[2]))
            result[task.task_id] = robot
            remaining_tasks.remove(task)
            remaining_robots.remove(robot)
        return result
