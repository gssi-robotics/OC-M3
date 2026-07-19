"""
Streamlit app to build a backend loader configuration for an EKG/Neo4j import.

The app does NOT create normalized CSVs and does NOT precompute CORR, PART_OF,
or REQ relationships. It only:

1. saves uploaded files to a backend-readable folder;
2. lets the user map event columns and entity columns;
3. lets the user optionally upload attribute files for Robot, Mission, Segment;
4. lets the user upload a task -> required capability table;
5. outputs a JSON configuration that the backend can use to load Neo4j.

Run:
    streamlit run streamlit_ekg_loader_config_builder.py
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
import streamlit as st

import neo4j_shared

SUPPORTED_EXTENSIONS = {"csv", "xls", "xlsx"}
NONE_OPTION = "-- none --"
TYPE_OPTIONS = ["String", "Integer", "Float", "Boolean", "Datetime"]
APP_DIR = Path(__file__).resolve().parent
ENTITY_SPECS = {
    "Robot": "Robot entity column",
    "Mission": "Mission entity column",
    "Segment": "Segment entity column",
}


# -----------------------------------------------------------------------------
# File handling
# -----------------------------------------------------------------------------


def get_extension(file_name: str) -> str:
    return file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""


def clean_file_name(file_name: str) -> str:
    cleaned = file_name.replace(" ", "_")
    cleaned = "".join(ch for ch in cleaned if ch.isalnum() or ch in {"_", "-", "."})
    return cleaned or "uploaded_file"


def resolve_app_path(path_like: str | Path) -> Path:
    path = Path(path_like).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (APP_DIR / path).resolve()


def save_upload(uploaded_file: Any, upload_dir: Path, prefix: str) -> str:
    """Save a Streamlit UploadedFile and return an absolute path."""
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / f"{prefix}_{clean_file_name(uploaded_file.name)}"

    with open(target, "wb") as handle:
        handle.write(uploaded_file.getbuffer())

    try:
        uploaded_file.seek(0)
    except Exception:
        pass

    return str(target.resolve())


def read_uploaded_table(uploaded_file: Any) -> pd.DataFrame:
    ext = get_extension(uploaded_file.name)
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError("Unsupported file type. Upload CSV, XLS, or XLSX.")
    if ext == "csv":
        return pd.read_csv(uploaded_file)
    return pd.read_excel(uploaded_file)


def uniquify_columns(df: pd.DataFrame) -> pd.DataFrame:
    counts: Dict[str, int] = {}
    cols: List[str] = []
    for col in df.columns:
        base = str(col)
        if base not in counts:
            counts[base] = 0
            cols.append(base)
        else:
            counts[base] += 1
            cols.append(f"{base}_{counts[base]}")
    out = df.copy()
    out.columns = cols
    return out


def normalize_loader_config_paths(config: Dict[str, Any]) -> Dict[str, Any]:
    normalized = json.loads(json.dumps(config))

    events = normalized.get("events", {})
    if isinstance(events, dict) and events.get("path"):
        events["path"] = str(resolve_app_path(events["path"]))

    entities = normalized.get("entities", {})
    if isinstance(entities, dict):
        for entity in entities.values():
            if isinstance(entity, dict) and entity.get("path"):
                entity["path"] = str(resolve_app_path(entity["path"]))

    task_capabilities = normalized.get("task_capabilities", {})
    if isinstance(task_capabilities, dict) and task_capabilities.get("path"):
        task_capabilities["path"] = str(resolve_app_path(task_capabilities["path"]))

    return normalized


def validate_config_paths(config: Dict[str, Any]) -> List[str]:
    missing: List[str] = []
    candidate_paths = []

    events = config.get("events", {})
    if isinstance(events, dict) and events.get("path"):
        candidate_paths.append(("events", events["path"]))

    entities = config.get("entities", {})
    if isinstance(entities, dict):
        for entity_key, entity in entities.items():
            if isinstance(entity, dict) and entity.get("path"):
                candidate_paths.append((f"entities.{entity_key}", entity["path"]))

    task_capabilities = config.get("task_capabilities", {})
    if isinstance(task_capabilities, dict) and task_capabilities.get("path"):
        candidate_paths.append(("task_capabilities", task_capabilities["path"]))

    for label, path_str in candidate_paths:
        if not Path(path_str).exists():
            missing.append(f"{label}: {path_str}")
    return missing


def upload_loader_config() -> Optional[Dict[str, Any]]:
    uploaded = st.file_uploader(
        "Optional loader config JSON",
        type=["json"],
        key="loader_config_json_file",
        help="Upload a previously downloaded loader configuration to reuse it directly.",
    )
    if uploaded is None:
        return None

    try:
        raw = json.load(uploaded)
        config = normalize_loader_config_paths(raw)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not read loader config JSON: {exc}")
        return None

    st.success("Loader config JSON loaded.")
    missing_paths = validate_config_paths(config)
    if missing_paths:
        st.warning("Some configured files do not exist at the resolved absolute paths:")
        for item in missing_paths:
            st.caption(item)
    else:
        st.caption("All configured paths were resolved successfully.")

    return config


# -----------------------------------------------------------------------------
# Type inference and attribute helpers
# -----------------------------------------------------------------------------


def infer_type(series: pd.Series) -> str:
    non_null = series.dropna()
    if non_null.empty:
        return "String"
    if pd.api.types.is_bool_dtype(series):
        return "Boolean"
    if pd.api.types.is_integer_dtype(series):
        return "Integer"
    if pd.api.types.is_float_dtype(series):
        return "Float"

    parsed = pd.to_datetime(series, errors="coerce")
    if parsed.notna().mean() >= 0.8:
        return "Datetime"
    return "String"


def infer_types(df: pd.DataFrame, attrs: Iterable[str], forced: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    forced = forced or {}
    result: Dict[str, str] = {}
    for attr in attrs:
        if attr not in df.columns:
            continue
        result[attr] = forced.get(attr, infer_type(df[attr]))
    return result


def add_type_overrides_ui(
    df: pd.DataFrame,
    attrs: List[str],
    defaults: Dict[str, str],
    key_prefix: str,
) -> Dict[str, str]:
    """Let users override inferred types without making the UI too heavy."""
    types: Dict[str, str] = {}
    with st.expander("Attribute types", expanded=False):
        st.caption("Types are inferred automatically. Override them only if needed.")
        for attr in attrs:
            if attr not in df.columns:
                continue
            inferred = defaults.get(attr, infer_type(df[attr]))
            default_index = TYPE_OPTIONS.index(inferred) if inferred in TYPE_OPTIONS else 0
            types[attr] = st.selectbox(
                f"{attr}",
                TYPE_OPTIONS,
                index=default_index,
                key=f"{key_prefix}_type_{attr}",
            )
    return types


def select_column(label: str, columns: List[str], key: str, optional: bool = False) -> Optional[str]:
    options = [NONE_OPTION] + columns if optional else columns
    value = st.selectbox(label, options, key=key)
    if optional and value == NONE_OPTION:
        return None
    return value


# -----------------------------------------------------------------------------
# UI sections
# -----------------------------------------------------------------------------


def upload_event_log(upload_dir: Path) -> Optional[Tuple[pd.DataFrame, str]]:
    st.header("1. Event log")
    uploaded = st.file_uploader(
        "Upload event log",
        type=sorted(SUPPORTED_EXTENSIONS),
        key="event_log_file",
    )

    if uploaded is None:
        st.info("Upload an event log to start.")
        return None

    try:
        path = save_upload(uploaded, upload_dir, "events")
        df = uniquify_columns(read_uploaded_table(uploaded))
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not read event log: {exc}")
        return None

    st.success(f"Loaded {len(df):,} events and {len(df.columns):,} columns.")
    st.caption(f"Saved path: `{path}`")
    with st.expander("Preview", expanded=True):
        st.dataframe(df.head(50), width="stretch")
    return df, path


def map_event_log(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    st.header("2. Event and entity mapping")
    columns = list(df.columns)

    with st.form("mapping_form"):
        st.subheader("Core event fields")
        c1, c2 = st.columns(2)
        with c1:
            event_id = select_column("Event ID", columns, "event_id")
            start_time = select_column("Start time", columns, "start_time")
        with c2:
            activity = select_column("Activity name", columns, "activity")
            end_time = select_column("End time", columns, "end_time")

        st.subheader("Entity columns in event log")
        entity_columns: Dict[str, str] = {}
        c3, c4, c5 = st.columns(3)
        col_widgets = [("Robot", c3), ("Mission", c4), ("Segment", c5)]
        for entity_type, container in col_widgets:
            with container:
                selected = select_column(
                    ENTITY_SPECS[entity_type],
                    columns,
                    f"entity_col_{entity_type}",
                    optional=True,
                )
                if selected:
                    entity_columns[entity_type] = selected

        st.subheader("Event attributes to import")
        protected = {event_id, activity, start_time, end_time, *entity_columns.values()}
        default_attrs = [col for col in columns if col in protected]
        event_attrs = st.multiselect(
            "Event properties",
            columns,
            default=default_attrs,
            help="Keep the selected event and entity columns. Add extra event attributes if needed.",
        )

        submitted = st.form_submit_button("Apply mapping")

    if not submitted and "event_mapping" not in st.session_state:
        return None

    if submitted:
        core = [event_id, activity, start_time, end_time]
        if len(set(core)) != len(core):
            st.error("Event ID, activity, start time, and end time must be distinct columns.")
            return None
        if not entity_columns:
            st.error("Select at least one entity column.")
            return None
        missing = protected - set(event_attrs)
        if missing:
            st.error("Event attributes must include: " + ", ".join(sorted(missing)))
            return None

        mapping = {
            "event_id": event_id,
            "event_activity": activity,
            "event_start": start_time,
            "event_end": end_time,
            "entity_columns": entity_columns,
            "event_attributes": event_attrs,
        }
        st.session_state["event_mapping"] = mapping
        st.success("Mapping applied.")

    return st.session_state.get("event_mapping")


def map_entity_files(
    entity_columns: Dict[str, str],
    event_df: pd.DataFrame,
    event_path: str,
    upload_dir: Path,
) -> Dict[str, Any]:
    st.header("3. Optional entity attribute files")
    st.write(
        "For each selected entity, optionally upload a table with additional attributes. "
        "If no table is uploaded, the backend will create entities from the event-log column."
    )

    entities: Dict[str, Any] = {}

    for entity_type, event_column in entity_columns.items():
        with st.expander(f"{entity_type}: `{event_column}`", expanded=False):
            uploaded = st.file_uploader(
                f"Optional {entity_type} attribute table",
                type=sorted(SUPPORTED_EXTENSIONS),
                key=f"entity_file_{entity_type}",
            )

            if uploaded is None:
                attrs = [event_column]
                entities[event_column] = {
                    "type": entity_type,
                    "path": event_path,
                    "event_column": event_column,
                    "entity_id": event_column,
                    "attr": attrs,
                    "attr_types": {event_column: "String"},
                    "from_event_table": True,
                }
                st.info("No attribute file uploaded. Entity nodes will be created from the event log.")
                continue

            try:
                entity_path = save_upload(uploaded, upload_dir, f"entity_{entity_type}")
                entity_df = uniquify_columns(read_uploaded_table(uploaded))
            except Exception as exc:  # noqa: BLE001
                st.error(f"Could not read {entity_type} table: {exc}")
                continue

            st.caption(f"Saved path: `{entity_path}`")
            st.dataframe(entity_df.head(30), width="stretch")

            columns = list(entity_df.columns)
            guessed_index = columns.index(event_column) if event_column in columns else 0
            entity_id = st.selectbox(
                f"Unique ID column for {entity_type}",
                columns,
                index=guessed_index,
                key=f"entity_id_{entity_type}",
                help="This column must match the IDs contained in the selected event-log entity column.",
            )

            available_attrs = columns
            default_attrs = columns
            selected_attrs = st.multiselect(
                f"Attributes to import for {entity_type}",
                available_attrs,
                default=default_attrs,
                key=f"entity_attrs_{entity_type}",
            )
            if entity_id not in selected_attrs:
                selected_attrs = [entity_id] + selected_attrs

            inferred = infer_types(entity_df, selected_attrs, forced={entity_id: "String"})
            attr_types = add_type_overrides_ui(
                entity_df,
                selected_attrs,
                inferred,
                key_prefix=f"entity_{entity_type}",
            )

            entities[event_column] = {
                "type": entity_type,
                "path": entity_path,
                "event_column": event_column,
                "entity_id": entity_id,
                "attr": selected_attrs,
                "attr_types": attr_types,
                "from_event_table": False,
            }

    return entities


def map_task_capabilities(upload_dir: Path) -> Optional[Dict[str, Any]]:
    st.header("4. Task-required capabilities")
    st.write("Upload a table associating task/activity names with required capabilities.")

    uploaded = st.file_uploader(
        "Task-capability table",
        type=sorted(SUPPORTED_EXTENSIONS),
        key="task_capability_file",
    )
    if uploaded is None:
        st.warning("This table is required for capability-aware EKG loading.")
        return None

    try:
        path = save_upload(uploaded, upload_dir, "task_capabilities")
        df = uniquify_columns(read_uploaded_table(uploaded))
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not read task-capability table: {exc}")
        return None

    st.caption(f"Saved path: `{path}`")
    st.dataframe(df.head(50), width="stretch")

    columns = list(df.columns)
    c1, c2 = st.columns(2)
    with c1:
        task_col = st.selectbox("Task/activity column", columns, key="task_col")
    with c2:
        cap_col = st.selectbox("Required capability column", columns, key="cap_col")

    if task_col == cap_col:
        st.error("Task column and capability column must be different.")
        return None

    attrs = [task_col, cap_col]
    inferred = infer_types(df, attrs, forced={task_col: "String", cap_col: "String"})

    return {
        "path": path,
        "task_column": task_col,
        "capability_column": cap_col,
        "attr": attrs,
        "attr_types": inferred,
    }


# -----------------------------------------------------------------------------
# Config builder
# -----------------------------------------------------------------------------


def validate_mapping(event_df: pd.DataFrame, mapping: Dict[str, Any]) -> None:
    st.header("Validation")

    event_id = mapping["event_id"]
    duplicate_ids = event_df[event_id].astype(str).duplicated().sum()
    if duplicate_ids:
        st.warning(f"Found {duplicate_ids:,} duplicated event IDs.")
    else:
        st.success("Event IDs are unique.")

    start = pd.to_datetime(event_df[mapping["event_start"]], errors="coerce")
    end = pd.to_datetime(event_df[mapping["event_end"]], errors="coerce")
    invalid = (start.isna() | end.isna() | (end < start)).sum()
    if invalid:
        st.warning(f"Found {invalid:,} rows with invalid timestamps or end before start.")
    else:
        st.success("Start/end timestamps are parseable and ordered.")

    for entity_type, column in mapping["entity_columns"].items():
        missing = event_df[column].isna().sum()
        if missing:
            st.warning(f"{entity_type} column `{column}` has {missing:,} missing values.")


def build_loader_config(
    log_name: str,
    event_path: str,
    event_df: pd.DataFrame,
    mapping: Dict[str, Any],
    entities: Dict[str, Any],
    task_capabilities: Dict[str, Any],
) -> Dict[str, Any]:
    forced_event_types = {
        mapping["event_id"]: "String",
        mapping["event_activity"]: "String",
        mapping["event_start"]: "Datetime",
        mapping["event_end"]: "Datetime",
    }
    for col in mapping["entity_columns"].values():
        forced_event_types[col] = "String"

    event_attrs = mapping["event_attributes"]
    event_types = infer_types(event_df, event_attrs, forced=forced_event_types)

    return {
        "log_name": log_name,
        "event_id": mapping["event_id"],
        "event_activity": mapping["event_activity"],
        "event_start": mapping["event_start"],
        "event_end": mapping["event_end"],
        "entity_id": "entity_id",
        "entity_type_id": "type",
        "events": {
            "path": event_path,
            "attr": event_attrs,
            "attr_types": event_types,
            "entity_columns": mapping["entity_columns"],
        },
        "entities": entities,
        "task_capabilities": task_capabilities,
    }


def render_config(config: Dict[str, Any]) -> None:
    st.header("5. Loader configuration")
    st.session_state["loader_config"] = config

    st.subheader("Summary")
    st.json(
        {
            "log_name": config["log_name"],
            "event_file": config["events"]["path"],
            "event_attributes": len(config["events"]["attr"]),
            "entity_sources": list(config["entities"].keys()),
            "task_capability_file": config["task_capabilities"]["path"],
        }
    )

    st.subheader("Full JSON config")
    st.json(config)

    config_json = json.dumps(config, indent=2, default=str)
    st.download_button(
        "Download loader config JSON",
        data=config_json,
        file_name=f"{config['log_name']}_loader_config.json",
        mime="application/json",
        key="download_loader_config",
    )

    st.caption(
        "Backend responsibility: load Event and Entity nodes from the configured paths, "
        "infer CORR from events.entity_columns, infer PART_OF from Segment/Mission columns, "
        "and create REQ edges from task_capabilities."
    )


def load_ekg_query_builder() -> Any:
    module_path = Path(__file__).resolve().parent.parent / "query-lib" / "init_ekg.py"
    spec = importlib.util.spec_from_file_location("init_ekg", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load query builder from {module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_ekg_steps(config: Dict[str, Any], include_cleanup: bool) -> List[Dict[str, str]]:
    builder = load_ekg_query_builder()
    return builder.build_load_plan(config, include_cleanup=include_cleanup)


def run_ekg_creation(
    steps: List[Dict[str, str]],
    uri: str,
    user: str,
    password: str,
) -> bool:
    driver, error = neo4j_shared.get_neo4j_driver(uri, user, password)
    if driver is None:
        st.warning(error)
        return False

    try:
        with driver.session() as session:
            for step in steps:
                session.run(step["query"]).consume()
        st.success(f"EKG created in Neo4j with {len(steps)} query steps.")
        return True
    except Exception as exc:  # noqa: BLE001
        st.error(f"EKG creation started but failed while running queries: {exc}")
        return False
    finally:
        driver.close()


def render_ekg_creation(config: Dict[str, Any]) -> None:
    st.header("6. Create EKG")
    st.write(
        "Generate the full EKG load plan from the selected data-model mapping and run it in Neo4j. "
        "If Neo4j is unavailable, the app will show the queries instead."
    )
    neo4j_shared.render_connection_summary()

    c1, c2 = st.columns([1, 2])
    with c1:
        include_cleanup = st.checkbox(
            "Delete existing nodes for this log first",
            value=False,
            key="ekg_cleanup",
        )
    with c2:
        st.caption("Cleanup adds a first query that clears the current Neo4j database before loading.")

    if st.button("Create EKG", type="primary", key="create_ekg_button"):
        connection = neo4j_shared.get_connection_settings()
        try:
            steps = build_ekg_steps(config, include_cleanup=include_cleanup)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not generate the EKG queries: {exc}")
            return

        executed_in_neo4j = run_ekg_creation(
            steps,
            uri=connection["uri"],
            user=connection["user"],
            password=connection["password"],
        )

        if not executed_in_neo4j:
            st.info("Neo4j execution was skipped. Here is the generated EKG load plan.")

        for index, step in enumerate(steps, start=1):
            st.markdown(f"**Step {index}: {step['name']}**")
            st.code(step["query"], language="cypher")


# -----------------------------------------------------------------------------
# Page
# -----------------------------------------------------------------------------


def render_page() -> None:
    st.title("EKG Loader Config Builder")
    st.write(
        "Build a compact backend configuration for loading multi-robot mission logs into an EKG. "
        "This app does not generate relationship CSVs; relationships are inferred by the backend."
    )

    with st.sidebar:
        st.header("Settings")
        log_name = st.text_input("Log name", value="log_1")
        upload_dir = resolve_app_path(st.text_input("Upload storage directory", value="ekg_uploaded_inputs"))
        st.caption(f"App directory: `{APP_DIR}`")
        st.caption(f"Resolved upload directory: `{upload_dir}`")

    imported_config = upload_loader_config()
    if imported_config is not None:
        st.info("Using the uploaded loader configuration. Manual upload/mapping steps are skipped.")
        render_config(imported_config)
        render_ekg_creation(imported_config)
        return

    uploaded_event = upload_event_log(upload_dir)
    if uploaded_event is None:
        return

    event_df, event_path = uploaded_event

    mapping = map_event_log(event_df)
    if mapping is None:
        st.info("Apply the event/entity mapping to continue.")
        return

    validate_mapping(event_df, mapping)

    entities = map_entity_files(
        entity_columns=mapping["entity_columns"],
        event_df=event_df,
        event_path=event_path,
        upload_dir=upload_dir,
    )

    task_capabilities = map_task_capabilities(upload_dir)
    if task_capabilities is None:
        st.info("Upload and map the task-capability table to generate the backend config.")
        return

    config = build_loader_config(
        log_name=log_name,
        event_path=event_path,
        event_df=event_df,
        mapping=mapping,
        entities=entities,
        task_capabilities=task_capabilities,
    )
    config = normalize_loader_config_paths(config)
    render_config(config)
    render_ekg_creation(config)


def main() -> None:
    st.set_page_config(page_title="EKG Loader Config Builder", page_icon="🤖", layout="wide")
    render_page()


if __name__ == "__main__":
    main()
