from models import ScenarioDefinition, TaskSpec

SCENARIO = ScenarioDefinition(
    name="order_management",
    task_specs={
        "stage_order": TaskSpec("stage_order", ("mobile_base",), 8, control_sequence=("navigate", "localize")),
        "pick": TaskSpec("pick", ("gripper",), 14, control_sequence=("navigate", "localize", "align_shelf", "grasp")),
        "transport": TaskSpec("transport", ("mobile_base",), 20, control_sequence=("navigate", "localize")),
        "place": TaskSpec("place", ("gripper",), 10, control_sequence=("navigate", "align_station", "release")),
        "pack": TaskSpec("pack", ("packing",), 18, control_sequence=("navigate", "position")),
        "deliver_order": TaskSpec("deliver_order", ("mobile_base",), 16, control_sequence=("navigate", "localize")),
    },
    robot_profiles=[("mobile_base", "gripper"), ("mobile_base", "heavy_lift"), ("mobile_base",), ("packing",)],
    segment_templates=[("pick", "transport", "place"), ("pick", "transport", "pack")],
    mission_prefix_tasks=("stage_order",),
    mission_suffix_tasks=("deliver_order",),
    world_size=120.0,
)
