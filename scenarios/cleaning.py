from models import ScenarioDefinition, TaskSpec

SCENARIO = ScenarioDefinition(
    name="cleaning",
    task_specs={
        "inspect_floor": TaskSpec("inspect_floor", ("camera",), 8, control_sequence=("navigate", "position")),
        "inspect": TaskSpec("inspect", ("camera",), 8, control_sequence=("navigate", "enter_room", "position")),
        "vacuum": TaskSpec("vacuum", ("vacuum",), 25, control_sequence=("navigate", "enter_room", "localize", "clean_path")),
        "mop": TaskSpec("mop", ("mop",), 22, control_sequence=("navigate", "enter_room", "position", "clean_path")),
        "disinfect": TaskSpec("disinfect", ("disinfect",), 16, control_sequence=("navigate", "enter_room", "position")),
        "empty_bin": TaskSpec("empty_bin", ("bin_handler",), 10, control_sequence=("navigate", "position")),
        "final_inspection": TaskSpec("final_inspection", ("camera",), 8, control_sequence=("navigate", "position")),
    },
    robot_profiles=[("camera", "vacuum"), ("mop",), ("vacuum", "mop"), ("disinfect", "bin_handler")],
    segment_templates=[("inspect", "vacuum", "mop", "disinfect"), ("vacuum", "empty_bin")],
    mission_prefix_tasks=("inspect_floor",),
    mission_suffix_tasks=("final_inspection",),
    world_size=80.0,
)
