from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

Location = Tuple[float, float]


@dataclass(frozen=True)
class TaskSpec:
    name: str
    required_capabilities: Tuple[str, ...]
    duration_mean: float
    duration_jitter: float = 0.2
    control_sequence: Tuple[str, ...] = ()


@dataclass
class TaskInstance:
    task_id: str
    task_name: str
    mission_id: str
    # None means that the task is correlated directly with the mission and
    # does not belong to any segment, as allowed by the paper's data model.
    segment_id: Optional[str]
    location: Location
    release_time: float
    precedence: List[str] = field(default_factory=list)


@dataclass
class Segment:
    segment_id: str
    mission_id: str
    location: Location
    tasks: List[TaskInstance] = field(default_factory=list)


@dataclass
class Mission:
    mission_id: str
    arrival_time: float
    direct_tasks: List[TaskInstance] = field(default_factory=list)
    segments: List[Segment] = field(default_factory=list)

    @property
    def tasks(self) -> List[TaskInstance]:
        """All mission-correlated tasks, including segment and direct tasks."""
        result = list(self.direct_tasks)
        for segment in self.segments:
            result.extend(segment.tasks)
        return result


@dataclass
class RobotState:
    robot_id: str
    capabilities: set[str]
    location: Location
    battery: float = 100.0
    available_at: float = 0.0
    current_mission: Optional[str] = None
    current_segment: Optional[str] = None


@dataclass
class Event:
    event_id: str
    event_type: str  # Task | Control
    activity: str
    start_time: float
    end_time: float
    robot_id: str
    mission_id: Optional[str] = None
    segment_id: Optional[str] = None
    task_id: Optional[str] = None


@dataclass
class ScenarioDefinition:
    name: str
    task_specs: Dict[str, TaskSpec]
    robot_profiles: Sequence[Tuple[str, ...]]
    segment_templates: Sequence[Sequence[str]]
    # Mission-level task templates are intentionally separate from segment
    # templates. Their generated TaskInstances have segment_id=None.
    mission_prefix_tasks: Sequence[str] = ()
    mission_suffix_tasks: Sequence[str] = ()
    world_size: float = 100.0
