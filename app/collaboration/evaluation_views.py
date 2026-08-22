from __future__ import annotations

import io
import json
import zipfile
from numbers import Number
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
import streamlit as st

from .collaboration_data import run_pattern_query
from .collaboration_utils import (
    event_id_from_mapping,
    filter_rows_by_log,
    humanize_name,
    node_id,
    normalize_value,
    table_safe_rows,
)


OCCURRENCE_LABELS = {
    "handover": "Robot handover",
    "objective_switch": "Objective switch",
    "capability_driven_return": "Capability-driven return",
    "parallel_collaboration": "Parallel collaboration",
}

CONTROL_CONTEXT_KEYS = (
    "fromTaskPreparationEvents",
    "toTaskPreparationEvents",
    "firstTaskPreparationEvents",
    "intermediateTaskPreparationEvents",
    "returnTaskPreparationEvents",
    "fromRobotControlEvents",
    "toRobotControlEvents",
    "controlEvents",
)

TIME_METRICS = {
    "transitionTime",
    "switchTime",
    "returnTime",
    "transitionToIntermediate",
    "transitionBack",
    "intermediateDuration",
    "overlapDuration",
    "avgTransitionTime",
    "avgSwitchTime",
    "avgReturnTime",
    "obsTime",
    "activeTime",
    "workloadSeconds",
    "totalWorkloadSeconds",
    "syncDelay",
    "branchWait",
    "branchWait1",
    "branchWait2",
    "totalBranchWait",
}


def _structure_name(query_name: str) -> str:
    for prefix, label in OCCURRENCE_LABELS.items():
        if query_name.startswith(prefix) or prefix in query_name:
            return label
    return humanize_name(query_name)


def _perspective(query_name: str) -> str:
    if query_name.endswith("_mission") or "_mission_" in query_name:
        return "Mission"
    if query_name.endswith("_segment") or "_segment_" in query_name or "segments" in query_name:
        return "Segment"
    if "by_robot" in query_name:
        return "Robot"
    if "capability" in query_name:
        return "Capability"
    return "Cross-perspective"


def _json(value: Any) -> str:
    return json.dumps(normalize_value(value), ensure_ascii=True, default=str, sort_keys=True)


def _id(value: Any) -> str:
    return node_id(value) or ""


def _event_id(value: Any) -> str:
    return event_id_from_mapping(value) or ""


def _list_ids(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [item for item in (_id(entry) for entry in value) if item]


def _control_events(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for key in CONTROL_CONTEXT_KEYS:
        value = row.get(key)
        if not isinstance(value, list):
            continue
        for event in value:
            if not isinstance(event, dict):
                continue
            event_key = _event_id(event) or _json(event)
            if event_key in seen:
                continue
            seen.add(event_key)
            events.append(event)
    return events


def _occurrence_record(
    strategy: str,
    query_name: str,
    index: int,
    row: Dict[str, Any],
) -> Dict[str, Any]:
    controls = _control_events(row)
    capabilities = _list_ids(row.get("capabilities")) or _list_ids(row.get("sharedRequiredCapabilities"))
    return {
        "strategy": strategy,
        "structure": _structure_name(query_name),
        "perspective": _perspective(query_name),
        "occurrence_number": index,
        "objective_id": _id(row.get("objective")),
        "from_objective_id": _id(row.get("fromObjective")),
        "to_objective_id": _id(row.get("toObjective")),
        "mission_id": _id(row.get("mission")) or _id(row.get("mission1")),
        "second_mission_id": _id(row.get("mission2")),
        "segment_1_id": _id(row.get("segment1")),
        "segment_2_id": _id(row.get("segment2")),
        "robot_id": _id(row.get("robot")),
        "from_robot_id": _id(row.get("fromRobot")),
        "to_robot_id": _id(row.get("toRobot")),
        "returning_robot_id": _id(row.get("returningRobot")),
        "intermediate_robot_id": _id(row.get("intermediateRobot")),
        "event_i": _event_id(row.get("e_i")),
        "event_j": _event_id(row.get("e_j")),
        "event_k": _event_id(row.get("e_k")),
        "transition_seconds": row.get("transitionTime", row.get("switchTime")),
        "return_seconds": row.get("returnTime"),
        "overlap_seconds": row.get("overlapDuration"),
        "control_event_count": len(controls),
        "control_activities": " | ".join(str(event.get("activity") or "") for event in controls),
        "capabilities": " | ".join(capabilities),
        "details_json": _json(row),
    }


def _row_dimensions(row: Dict[str, Any]) -> Dict[str, str]:
    return {
        "objective_id": _id(row.get("objective")),
        "robot_id": _id(row.get("robot")),
        "capability_id": _id(row.get("capability")),
        "mission_id": _id(row.get("mission")),
        "segment_1_id": _id(row.get("segment1")),
        "segment_2_id": _id(row.get("segment2")),
        "secondary_entity_id": _id(row.get("provider")),
    }


def _metric_unit(metric: str) -> str:
    if metric in TIME_METRICS or metric.lower().endswith(("time", "duration", "seconds", "wait")):
        return "seconds"
    if metric.lower().endswith(("ratio", "share")):
        return "ratio"
    if metric == "rate":
        return "occurrences_per_second"
    return "count" if "count" in metric.lower() or metric in {"teamSize", "availability"} else "value"


def _indicator_records(
    strategy: str,
    query_name: str,
    rows: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for observation, row in enumerate(rows, start=1):
        dimensions = _row_dimensions(row)
        for metric, value in row.items():
            if isinstance(value, bool) or not isinstance(value, Number):
                continue
            records.append(
                {
                    "strategy": strategy,
                    "indicator": humanize_name(query_name),
                    "perspective": _perspective(query_name),
                    "observation": observation,
                    **dimensions,
                    "metric": metric,
                    "value": float(value),
                    "unit": _metric_unit(metric),
                    "details_json": _json(row),
                }
            )
    return records


def build_evaluation_dataset(
    driver: Any,
    database: Optional[str],
    catalog: Dict[str, Dict[str, str]],
    strategies: List[str],
) -> Dict[str, List[Dict[str, Any]]]:
    counts: List[Dict[str, Any]] = []
    occurrences: List[Dict[str, Any]] = []
    indicators: List[Dict[str, Any]] = []

    for query_name, query in catalog.get("Occurrences", {}).items():
        all_rows = run_pattern_query(driver, database, query, None)
        for strategy in strategies:
            rows = filter_rows_by_log(all_rows, strategy)
            counts.append(
                {
                    "strategy": strategy,
                    "structure": _structure_name(query_name),
                    "perspective": _perspective(query_name),
                    "occurrence_count": len(rows),
                }
            )
            occurrences.extend(
                _occurrence_record(strategy, query_name, index, row)
                for index, row in enumerate(rows, start=1)
            )

    for query_name, query in catalog.get("Diagnostics", {}).items():
        all_rows = run_pattern_query(driver, database, query, None)
        for strategy in strategies:
            indicators.extend(
                _indicator_records(
                    strategy,
                    query_name,
                    filter_rows_by_log(all_rows, strategy),
                )
            )

    return {"counts": counts, "occurrences": occurrences, "indicators": indicators}


def _evaluation_zip(payload: Dict[str, List[Dict[str, Any]]]) -> bytes:
    readme = """OC-M3 collaboration evaluation export

occurrence_counts.csv
  One row per strategy, collaboration structure, and objective perspective.

occurrences.csv
  One row per concrete EKG occurrence, including event/entity identifiers,
  temporal values, capability evidence, and Control-event context.

indicators_long.csv
  Tidy long-format analytical indicators. Each row is one metric value for
  one strategy and entity-level observation, ready for statistical analysis.

The strategy column corresponds to the EKG Log property selected in the UI.
Time values are expressed in seconds.
"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.txt", readme)
        archive.writestr("occurrence_counts.csv", pd.DataFrame(payload["counts"]).to_csv(index=False))
        archive.writestr("occurrences.csv", pd.DataFrame(payload["occurrences"]).to_csv(index=False))
        archive.writestr("indicators_long.csv", pd.DataFrame(payload["indicators"]).to_csv(index=False))
    return buffer.getvalue()


def render_evaluation_workspace(
    driver: Any,
    database: Optional[str],
    catalog: Dict[str, Dict[str, str]],
    logs: List[str],
) -> None:
    st.subheader("1. Evaluation dataset")
    st.caption(
        "Compare task-allocation strategies through collaboration behavior. "
        "Each selected EKG Log is treated as one strategy or experimental condition."
    )
    strategies = st.multiselect(
        "Strategies / logs",
        logs,
        default=logs,
        key="evaluation_logs",
    )
    if not strategies:
        st.info("Select at least one strategy/log.")
        return

    signature = tuple(strategies)
    if st.button("Generate evaluation dataset", type="primary", key="generate_evaluation_dataset"):
        with st.spinner("Computing collaboration occurrences and indicators..."):
            st.session_state["evaluation_payload"] = build_evaluation_dataset(
                driver, database, catalog, strategies
            )
            st.session_state["evaluation_signature"] = signature

    if st.session_state.get("evaluation_signature") != signature:
        st.info("Generate the dataset for the current strategy selection.")
        return

    payload = st.session_state.get("evaluation_payload")
    if not payload:
        return

    counts_df = pd.DataFrame(payload["counts"])
    if not counts_df.empty:
        comparison = counts_df.pivot_table(
            index=["structure", "perspective"],
            columns="strategy",
            values="occurrence_count",
            aggfunc="sum",
            fill_value=0,
        )
        st.dataframe(comparison, width="stretch")

    st.download_button(
        "Download evaluation package (.zip)",
        data=_evaluation_zip(payload),
        file_name="oc_m3_collaboration_evaluation.zip",
        mime="application/zip",
        key="download_evaluation_package",
    )
    st.caption(
        f"Export contains {len(payload['occurrences']):,} occurrences and "
        f"{len(payload['indicators']):,} tidy indicator values."
    )


def _preparation_contexts(query_name: str, row: Dict[str, Any]) -> List[Tuple[str, str, Any, Any]]:
    if query_name.startswith("handover"):
        return [
            ("source task", "fromTaskPreparationEvents", row.get("e_i"), row.get("fromRobot")),
            ("target task", "toTaskPreparationEvents", row.get("e_j"), row.get("toRobot")),
        ]
    if query_name.startswith("objective_switch"):
        return [
            ("task before switch", "fromTaskPreparationEvents", row.get("e_i"), row.get("robot")),
            ("task after switch", "toTaskPreparationEvents", row.get("e_j"), row.get("robot")),
        ]
    if query_name.startswith("capability_driven_return"):
        return [
            ("first task", "firstTaskPreparationEvents", row.get("e_i"), row.get("returningRobot")),
            ("intermediate task", "intermediateTaskPreparationEvents", row.get("e_j"), row.get("intermediateRobot")),
            ("return task", "returnTaskPreparationEvents", row.get("e_k"), row.get("returningRobot")),
        ]
    return []


def _explanation_rows(query_name: str, row: Dict[str, Any]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen: set[Tuple[str, str]] = set()
    for task_role, context_key, task, robot in _preparation_contexts(query_name, row):
        robot_id = _id(robot)
        controls = row.get(context_key) if isinstance(row.get(context_key), list) else []
        for control in controls:
            if not isinstance(control, dict):
                continue
            control_id = _event_id(control) or _json(control)
            dedupe_key = (task_role, control_id)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            result.append(
                {
                    "context": f"prepares {task_role}",
                    "event_type": "Control",
                    "robot": robot_id,
                    "event_id": _event_id(control),
                    "activity": control.get("activity"),
                    "start": control.get("start"),
                    "end": control.get("end"),
                }
            )
        if isinstance(task, dict):
            result.append(
                {
                    "context": task_role,
                    "event_type": "Task",
                    "robot": robot_id,
                    "event_id": _event_id(task),
                    "activity": task.get("activity"),
                    "start": task.get("start"),
                    "end": task.get("end"),
                }
            )
    result.sort(key=lambda item: (str(item.get("start") or ""), item["event_type"] == "Task"))
    for index, item in enumerate(result, start=1):
        item["sequence"] = index
    return result


def _sequence_graph(rows: List[Dict[str, Any]]) -> str:
    lines = ["digraph explanation {", "rankdir=LR;", 'graph [bgcolor="white", pad="0.2"];']
    lines.append('node [shape=box, style="rounded,filled", fontname="Helvetica"];')
    for index, row in enumerate(rows):
        color = "#DCEAF7" if row["event_type"] == "Control" else "#FAD7C8"
        label = f"{row['event_type']}\\n{row.get('activity') or '?'}\\n{row.get('robot') or '?'}"
        lines.append(f"n{index} [label={json.dumps(label)}, fillcolor=\"{color}\"];")
        if index:
            lines.append(f"n{index - 1} -> n{index};")
    lines.append("}")
    return "\n".join(lines)


def render_occurrence_explainability(
    driver: Any,
    database: Optional[str],
    catalog: Dict[str, Dict[str, str]],
    logs: List[str],
) -> None:
    st.subheader("2. Explain one collaboration occurrence")
    st.caption(
        "Control events are shown immediately before the Task they prepare, making transition "
        "time interpretable as robot-local behavior rather than an unexplained gap."
    )
    sequential_queries = {
        name: query
        for name, query in catalog.get("Occurrences", {}).items()
        if not name.startswith("parallel_collaboration")
    }
    c1, c2 = st.columns(2)
    with c1:
        selected_log = st.selectbox("Strategy / log", logs, key="explain_log")
    with c2:
        query_name = st.selectbox(
            "Collaboration structure",
            list(sequential_queries),
            format_func=humanize_name,
            key="explain_structure",
        )

    signature = (selected_log, query_name)
    if st.button("Find occurrences", key="find_explainable_occurrences"):
        st.session_state["explain_rows"] = run_pattern_query(
            driver,
            database,
            sequential_queries[query_name],
            selected_log,
            row_limit=500,
        )
        st.session_state["explain_signature"] = signature

    if st.session_state.get("explain_signature") != signature:
        return
    rows = st.session_state.get("explain_rows", [])
    if not rows:
        st.info("No occurrences were found for this structure and strategy.")
        return

    selected_index = st.selectbox(
        "Occurrence",
        list(range(len(rows))),
        format_func=lambda index: f"Occurrence {index + 1} of {len(rows)}",
        key="explain_occurrence_index",
    )
    selected = rows[selected_index]
    sequence = _explanation_rows(query_name, selected)
    if sequence:
        st.graphviz_chart(_sequence_graph(sequence), width="stretch")
        st.dataframe(table_safe_rows(sequence), width="stretch", hide_index=True)
    else:
        st.info("This occurrence has no preceding Control events in the current graph.")

    with st.expander("Occurrence evidence", expanded=False):
        st.json(selected)

