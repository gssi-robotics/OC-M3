from models import ScenarioDefinition, TaskSpec

SCENARIO = ScenarioDefinition(
    name="agriculture",
    task_specs={
        "survey_field": TaskSpec("survey_field", ("camera",), 10, control_sequence=("navigate", "position", "align_camera")),
        "inspect": TaskSpec("inspect", ("camera",), 12, control_sequence=("navigate", "position", "align_camera")),
        "spray": TaskSpec("spray", ("sprayer",), 18, control_sequence=("navigate", "position", "activate_sprayer")),
        "harvest": TaskSpec("harvest", ("gripper",), 24, control_sequence=("navigate", "position", "align_gripper", "grasp")),
        "final_inspection": TaskSpec("final_inspection", ("camera",), 8, control_sequence=("navigate", "position", "align_camera")),
    },
    robot_profiles=[("camera", "gripper"), ("sprayer",), ("camera", "sprayer"), ("gripper",)],
    segment_templates=[("inspect", "spray", "harvest"), ("inspect", "harvest")],
    mission_prefix_tasks=("survey_field",),
    mission_suffix_tasks=("final_inspection",),
    world_size=200.0,
)
