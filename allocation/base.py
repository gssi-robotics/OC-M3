from __future__ import annotations
from abc import ABC, abstractmethod
from typing import List
from models import RobotState, TaskInstance, TaskSpec


class Allocator(ABC):
    name = "base"

    @abstractmethod
    def choose_robot(self, task: TaskInstance, spec: TaskSpec, robots: List[RobotState], now: float) -> RobotState:
        raise NotImplementedError

    @staticmethod
    def feasible(spec: TaskSpec, robots: List[RobotState]) -> List[RobotState]:
        req = set(spec.required_capabilities)
        return [r for r in robots if req.issubset(r.capabilities)]
