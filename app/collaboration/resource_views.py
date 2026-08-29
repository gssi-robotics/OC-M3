from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

import pandas as pd
import streamlit as st

from .collaboration_utils import format_seconds
from .collaboration_visuals import render_dashboard_cards
from .resource_analysis import (
    OBJECTIVE_TYPES,
    build_aggregated_resource_perspective,
    build_resource_perspective,
    fetch_objective_ids,
    fetch_objective_references,
)


def _plotly_graph_objects() -> Any:
    try:
        import plotly.graph_objects as go
    except ImportError as exc:  # pragma: no cover
        raise ImportError("Install Plotly with `pip install plotly` to use the Resource Perspective.") from exc
    return go


def _render_contribution_donut(
    rows: List[Dict[str, Any]],
    value_key: str,
    title: str,
    hover_suffix: str,
) -> None:
    go = _plotly_graph_objects()
    fig = go.Figure(go.Pie(
        labels=[row["robot_id"] for row in rows],
        values=[row[value_key] for row in rows],
        hole=0.58,
        sort=False,
        textinfo="label+percent",
        hovertemplate=f"<b>%{{label}}</b><br>%{{value:.2f}} {hover_suffix}<br>%{{percent}}<extra></extra>",
        marker=dict(line=dict(color="white", width=2)),
    ))
    fig.update_layout(
        title=title,
        height=370,
        margin=dict(l=20, r=20, t=60, b=20),
        legend=dict(orientation="h", y=-0.08),
    )
    st.plotly_chart(fig, width="stretch", config={"displaylogo": False})


def render_robot_contribution(payload: Mapping[str, Any]) -> None:
    """Render task-count and optional execution-time contribution views."""
    rows = list(payload.get("robot_contributions", []))
    summary = dict(payload.get("summary", {}))
    st.markdown("### Robot Contribution")
    if not rows:
        st.info("No robot-correlated Task executions were found for this objective.")
        return

    chart_column, table_column = st.columns([1, 1.15])
    with chart_column:
        _render_contribution_donut(rows, "task_count", "Task contribution", "Task executions")
    with table_column:
        table = pd.DataFrame([{
            "Robot": row["robot_id"],
            "Task contribution": f"{row['task_count']} / {row['total_task_count']}",
            "Task share (%)": round(float(row["task_percentage"]), 2),
            "Execution time": format_seconds(row.get("execution_seconds")) or "n/a",
            "Time share (%)": (
                round(float(row["time_percentage"]), 2)
                if row.get("time_percentage") is not None
                else None
            ),
        } for row in rows])
        st.dataframe(table, width="stretch", hide_index=True, height=365)

    if summary.get("duration_share_available"):
        st.caption(
            "Time contribution is based on summed Task execution durations; concurrent work is counted "
            "for each executing robot and is distinct from objective elapsed time."
        )
        _render_contribution_donut(
            rows,
            "execution_seconds",
            "Execution-time contribution",
            "seconds",
        )
    elif not summary.get("duration_complete"):
        st.info(
            "Execution-time contribution is not shown because valid start/end timestamps are not "
            f"available for every Task ({summary.get('timed_task_count', 0)} of "
            f"{summary.get('task_count', 0)} covered)."
        )
    else:
        st.info(
            "Execution-time contribution is not shown because the selected objective has zero total "
            "Task execution duration."
        )


def render_capability_demand(payload: Mapping[str, Any]) -> None:
    """Render objective capability demand without collapsing multi-capability tasks."""
    rows = list(payload.get("capability_demand", []))
    st.markdown("### Capability Demand")
    st.caption(
        "Each Task execution contributes once to every capability it requires, so percentages may sum "
        "to more than 100% when tasks have multiple requirements."
    )
    if not rows:
        st.info("No `REQ` capability information is available for this objective's Task executions.")
        return

    go = _plotly_graph_objects()
    fig = go.Figure(go.Bar(
        x=[row["required_task_executions"] for row in rows],
        y=[row["capability"] for row in rows],
        orientation="h",
        marker_color="#2563EB",
        text=[
            f"{row['required_task_executions']} ({row['task_percentage']:.1f}%)"
            for row in rows
        ],
        textposition="outside",
        hovertemplate="<b>%{y}</b><br>Required Task executions: %{x}<extra></extra>",
    ))
    fig.update_layout(
        height=max(300, 52 * len(rows) + 110),
        margin=dict(l=30, r=100, t=25, b=45),
        xaxis_title="Task executions requiring capability",
        yaxis_title="",
        yaxis=dict(autorange="reversed"),
    )
    st.plotly_chart(fig, width="stretch", config={"displaylogo": False})


def render_capability_utilization(payload: Mapping[str, Any]) -> None:
    """Render observed Robot x Capability execution counts and workload shares."""
    rows = list(payload.get("capability_utilization", []))
    st.markdown("### Robot × Capability Utilization")
    st.caption(
        "Cells count observed Task executions performed by a robot that required the capability; "
        "they do not represent declared capability availability."
    )
    if not rows:
        st.info("No Robot × Capability utilization can be computed for this objective.")
        return

    robots = sorted({str(row["robot_id"]) for row in rows})
    capabilities = sorted({str(row["capability"]) for row in rows})
    lookup = {
        (str(row["robot_id"]), str(row["capability"])): int(row["task_count"])
        for row in rows
    }
    matrix = [[lookup.get((robot, capability), 0) for capability in capabilities] for robot in robots]

    go = _plotly_graph_objects()
    fig = go.Figure(go.Heatmap(
        z=matrix,
        x=capabilities,
        y=robots,
        colorscale="Blues",
        zmin=0,
        text=matrix,
        texttemplate="%{text}",
        hovertemplate="Robot: %{y}<br>Capability: %{x}<br>Task executions: %{z}<extra></extra>",
        colorbar=dict(title="Tasks"),
    ))
    fig.update_layout(
        height=max(320, 46 * len(robots) + 140),
        margin=dict(l=70, r=30, t=30, b=90),
        xaxis_title="Required capability",
        yaxis_title="Robot",
    )
    st.plotly_chart(fig, width="stretch", config={"displaylogo": False})

    matrix_table = pd.DataFrame(matrix, index=robots, columns=capabilities)
    matrix_table.index.name = "Robot"
    with st.expander("Utilization matrix table", expanded=False):
        st.dataframe(matrix_table, width="stretch")

    workload_rows = list(payload.get("capability_workload", []))
    with st.expander("Capability workload distribution", expanded=False):
        st.dataframe(pd.DataFrame([{
            "Capability": row["capability"],
            "Robot": row["robot_id"],
            "Task executions": row["task_count"],
            "Capability workload (%)": round(float(row["workload_percentage"]), 2),
            "Declared capable": row["declared_capable"],
        } for row in workload_rows]), width="stretch", hide_index=True)


def render_capability_availability(payload: Mapping[str, Any]) -> None:
    """Render declared providers, involved providers, and observed capability use."""
    rows = list(payload.get("capability_availability", []))
    st.markdown("### Capability Availability")
    if not rows:
        st.info("No required capabilities are available for comparison.")
        return
    table = pd.DataFrame([{
        "Capability": row["capability"],
        "reqCount(c,o)": row["required_task_executions"],
        "availability(c)": row["availability"],
        "|R|": row["team_size"],
        "availabilityRate(c) (%)": (
            round(float(row["availability_percentage"]), 1)
            if row.get("availability_percentage") is not None else None
        ),
        "demandAvailability(c,o)": (
            round(float(row["demand_availability"]), 3)
            if row.get("demand_availability") is not None else None
        ),
        "Capable robots": ", ".join(row["capable_robots"]) or "none declared",
        "Involved capable robots": ", ".join(row["involved_capable_robots"]) or "none",
        "Actually used robots": ", ".join(row["actually_used_robots"]) or "none",
    } for row in rows])
    st.dataframe(table, width="stretch", hide_index=True)
    st.caption(
        "For the selected objective o: demandAvailability(c,o) = reqCount(c,o) / "
        "availability(c). reqCount uses distinct Task events requiring c; availability "
        "uses distinct robots declared capable of c in the selected execution. "
        "availabilityRate(c) = availability(c) / |R| x 100, where |R| is the complete "
        "robot roster for that execution. Ratios with a zero denominator are undefined."
    )
    objective = dict(payload.get("objective", {}))
    st.download_button(
        "Download objective demandAvailability (.csv)",
        data=table.to_csv(index=False).encode("utf-8"),
        file_name=(
            f"demand_availability_{objective.get('type', 'objective')}_"
            f"{objective.get('id', 'selected')}.csv"
        ),
        mime="text/csv",
        key="resource_objective_demand_availability_download",
    )


def render_collaboration_counts(payload: Mapping[str, Any]) -> None:
    """Render formal collaboration occurrences involving the selected objective."""
    counts = dict(payload.get("collaboration_counts", {}))
    objective_type = str(payload.get("objective", {}).get("type", "Mission")).lower()
    st.markdown("### Collaboration Structures")
    st.caption("Counts reuse the existing formal occurrence queries and are filtered to this objective instance.")
    render_dashboard_cards([
        {
            "label": "Handovers",
            "value": str(counts.get(f"handover_{objective_type}", 0)),
            "caption": "Robot handovers within the objective",
            "accent": "#DC2626",
        },
        {
            "label": "Objective switches",
            "value": str(counts.get(f"objective_switch_{objective_type}", 0)),
            "caption": "Switches entering or leaving the objective",
            "accent": "#2563EB",
        },
        {
            "label": "Capability returns",
            "value": str(counts.get(f"capability_driven_return_{objective_type}", 0)),
            "caption": "Capability-driven return occurrences",
            "accent": "#7C3AED",
        },
        {
            "label": "Parallel work",
            "value": str(counts.get(f"parallel_collaboration_{objective_type}", 0)),
            "caption": "Parallel structures involving the objective",
            "accent": "#059669",
        },
    ])


def render_resource_payload(payload: Mapping[str, Any]) -> None:
    """Render the complete single-objective Resource Perspective payload."""
    objective = dict(payload.get("objective", {}))
    summary = dict(payload.get("summary", {}))
    if not summary.get("task_count"):
        st.warning(
            f"No Task executions were found for {objective.get('type', 'objective')} "
            f"`{objective.get('id', '')}` in log `{objective.get('log', '')}`."
        )
        return

    dominant = summary.get("dominant_robot") or "n/a"
    dominance = summary.get("dominance_percentage")
    render_dashboard_cards([
        {
            "label": "Robots involved",
            "value": str(summary.get("robot_count", 0)),
            "caption": "Robots executing selected-objective Tasks",
            "accent": "#2563EB",
        },
        {
            "label": "Task executions",
            "value": str(summary.get("task_count", 0)),
            "caption": "Distinct Task events in this objective",
            "accent": "#059669",
        },
        # {
        #     "label": "Dominant robot",
        #     "value": str(dominant),
        #     "caption": f"{float(dominance):.1f}% of Tasks" if dominance is not None else "No assigned robot",
        #     "accent": "#D97706",
        # },
        {
            "label": "Dominance",
            "value": f"{float(dominance):.1f}%" if dominance is not None else "n/a",
            "caption": "Maximum robot Task contribution",
            "accent": "#DB2777",
        },
        {
            "label": "Required capabilities",
            "value": str(summary.get("distinct_capability_count", 0)),
            "caption": "Distinct `REQ` capabilities",
            "accent": "#7C3AED",
        },
    ])

    render_robot_contribution(payload)
    render_capability_demand(payload)
    render_capability_utilization(payload)
    render_capability_availability(payload)
    render_collaboration_counts(payload)


def render_instance_resource_perspective(
    driver: Any,
    database: Optional[str],
    catalog: Mapping[str, Mapping[str, str]],
    logs: List[str],
) -> None:
    """Render selectors and analysis for exactly one Mission or Segment instance."""
    st.subheader("Resource Perspective")
    st.caption(
        "Inspect how robots contributed to one objective instance and how observed work related to "
        "Task capability requirements. This view does not aggregate across objectives."
    )

    selector_columns = st.columns([1.2, 1, 1.4])
    with selector_columns[0]:
        log_name = st.selectbox("Strategy / log", logs, key="resource_log_filter")
    with selector_columns[1]:
        objective_type = st.radio(
            "Objective type",
            list(OBJECTIVE_TYPES),
            horizontal=True,
            key="resource_objective_type",
        )

    try:
        objective_ids = fetch_objective_ids(
            driver, database, log_name, objective_type
        )
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not load {objective_type} instances: {exc}")
        return
    if not objective_ids:
        st.info(f"No {objective_type} instances were found in `{log_name}`.")
        return

    with selector_columns[2]:
        objective_id = st.selectbox(
            f"{objective_type} instance",
            objective_ids,
            key="resource_objective_id",
        )

    signature = (str(database or ""), log_name, objective_type, str(objective_id))
    if st.button("Analyze objective", type="primary", key="resource_analyze_objective"):
        try:
            with st.spinner(f"Analyzing {objective_type} {objective_id}..."):
                st.session_state["resource_payload"] = build_resource_perspective(
                    driver,
                    database,
                    catalog,
                    log_name,
                    objective_type,
                    str(objective_id),
                )
                st.session_state["resource_signature"] = signature
        except Exception as exc:  # noqa: BLE001
            st.session_state.pop("resource_payload", None)
            st.session_state.pop("resource_signature", None)
            st.error(f"Could not build the Resource Perspective: {exc}")
            return

    if st.session_state.get("resource_signature") != signature:
        st.info("Select one objective instance and press `Analyze objective`.")
        return
    payload = st.session_state.get("resource_payload")
    if payload:
        render_resource_payload(payload)


def _render_aggregate_overview(payload: Mapping[str, Any]) -> None:
    summary = dict(payload.get("summary", {}))
    mean_robots = summary.get("mean_robots_per_objective")
    mean_dominance = summary.get("mean_dominance")
    render_dashboard_cards([
        {
            "label": "Objectives",
            "value": str(summary.get("objective_count", 0)),
            "caption": f"{summary.get('empty_objective_count', 0)} without valid Task events",
            "accent": "#2563EB",
        },
        {
            "label": "Task executions",
            "value": str(summary.get("total_task_count", 0)),
            "caption": "Distinct Tasks across selected objectives",
            "accent": "#059669",
        },
        {
            "label": "Distinct robots",
            "value": str(summary.get("distinct_robot_count", 0)),
            "caption": "Observed Task executors",
            "accent": "#D97706",
        },
        {
            "label": "Mean robots / objective",
            "value": f"{float(mean_robots):.2f}" if mean_robots is not None else "n/a",
            "caption": "Includes zero for empty selected objectives",
            "accent": "#DB2777",
        },
        {
            "label": "Mean dominance",
            "value": f"{float(mean_dominance) * 100.0:.1f}%" if mean_dominance is not None else "n/a",
            "caption": "Mean of valid instance-level dominance values",
            "accent": "#7C3AED",
        },
        {
            "label": "Capabilities",
            "value": str(summary.get("distinct_capability_count", 0)),
            "caption": "Distinct required capabilities",
            "accent": "#0F766E",
        },
    ])


def _render_robot_participation(payload: Mapping[str, Any]) -> None:
    rows = list(payload.get("robot_participation", []))
    st.markdown("### Robot Participation")
    st.caption(
        "Participation uses selected objectives as denominator; pooled workload uses all selected Task "
        "executions as denominator. It is not the mean per-objective contribution."
    )
    if not rows:
        st.info("No robot participation was observed in the selected objectives.")
        return
    go = _plotly_graph_objects()
    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Objective participation",
        x=[row["robot_id"] for row in rows],
        y=[row["participation_percentage"] for row in rows],
        marker_color="#2563EB",
        customdata=[row["objective_count"] for row in rows],
        hovertemplate="%{x}<br>%{customdata} objectives<br>%{y:.1f}% of selected objectives<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        name="Pooled workload",
        x=[row["robot_id"] for row in rows],
        y=[row["pooled_workload_percentage"] for row in rows],
        marker_color="#059669",
        customdata=[row["total_task_count"] for row in rows],
        hovertemplate="%{x}<br>%{customdata} Tasks<br>%{y:.1f}% pooled workload<extra></extra>",
    ))
    fig.update_layout(
        barmode="group",
        height=420,
        margin=dict(l=45, r=20, t=25, b=70),
        yaxis_title="Percentage (%)",
        xaxis_title="Robot",
        legend=dict(orientation="h", y=1.12),
    )
    st.plotly_chart(fig, width="stretch", config={"displaylogo": False})
    with st.expander("Robot participation table", expanded=False):
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def _render_contribution_distribution(payload: Mapping[str, Any]) -> None:
    distributions = list(payload.get("robot_contribution_distribution", []))
    summaries = list(payload.get("robot_contribution_summary", []))
    st.markdown("### Contribution Distribution")
    st.caption(
        "Each distribution contains one value per selected objective. A zero means that the robot did "
        "not participate in that objective."
    )
    if not distributions:
        st.info("No contribution distributions are available.")
        return
    go = _plotly_graph_objects()
    fig = go.Figure()
    for robot in sorted({str(row["robot_id"]) for row in distributions}):
        robot_rows = [row for row in distributions if str(row["robot_id"]) == robot]
        fig.add_trace(go.Box(
            name=robot,
            y=[row["contribution_percentage"] for row in robot_rows],
            boxpoints="all",
            jitter=0.28,
            pointpos=0,
            customdata=[
                [row["log_name"], row["objective_id"]] for row in robot_rows
            ],
            hovertemplate=(
                "Robot: %{fullData.name}<br>Log: %{customdata[0]}<br>Objective: %{customdata[1]}"
                "<br>Contribution: %{y:.1f}%<extra></extra>"
            ),
        ))
    fig.update_layout(
        height=460,
        margin=dict(l=50, r=20, t=25, b=70),
        yaxis_title="Per-objective Task contribution (%)",
        xaxis_title="Robot",
        showlegend=False,
    )
    st.plotly_chart(fig, width="stretch", config={"displaylogo": False})
    summary_table = pd.DataFrame([{
        "Robot": row["robot_id"],
        "Objectives participated": row["objectives_participated"],
        "Selected objectives": row["selected_objective_count"],
        "Mean (%)": round(float(row["mean_contribution"] or 0) * 100, 2),
        "Median (%)": round(float(row["median_contribution"] or 0) * 100, 2),
        "Min (%)": round(float(row["minimum_contribution"] or 0) * 100, 2),
        "Max (%)": round(float(row["maximum_contribution"] or 0) * 100, 2),
        "Std. dev. (%)": round(float(row["standard_deviation"] or 0) * 100, 2),
    } for row in summaries])
    st.dataframe(summary_table, width="stretch", hide_index=True)


def _render_objective_distributions(payload: Mapping[str, Any]) -> None:
    rows = list(payload.get("objective_rows", []))
    dominance_rows = [row for row in rows if row.get("dominance_percentage") is not None]
    st.markdown("### Objective Dominance and Team Size")
    c1, c2 = st.columns(2)
    go = _plotly_graph_objects()
    with c1:
        if dominance_rows:
            fig = go.Figure(go.Histogram(
                x=[row["dominance_percentage"] for row in dominance_rows],
                nbinsx=min(12, max(4, len(dominance_rows))),
                marker_color="#7C3AED",
                hovertemplate="Dominance: %{x:.1f}%<br>Objectives: %{y}<extra></extra>",
            ))
            fig.update_layout(height=350, xaxis_title="Dominance (%)", yaxis_title="Objectives", margin=dict(l=45, r=15, t=25, b=50))
            st.plotly_chart(fig, width="stretch", config={"displaylogo": False})
        else:
            st.info("No valid dominance values are available.")
    with c2:
        frequencies: Dict[int, int] = {}
        for row in rows:
            count = int(row["robot_count"])
            frequencies[count] = frequencies.get(count, 0) + 1
        fig = go.Figure(go.Bar(
            x=list(sorted(frequencies)),
            y=[frequencies[value] for value in sorted(frequencies)],
            marker_color="#D97706",
            hovertemplate="Robots: %{x}<br>Objectives: %{y}<extra></extra>",
        ))
        fig.update_layout(height=350, xaxis_title="Participating robots per objective", yaxis_title="Objectives", margin=dict(l=45, r=15, t=25, b=50))
        st.plotly_chart(fig, width="stretch", config={"displaylogo": False})

    dominance = dict(payload.get("dominance_statistics", {}))
    robot_stats = dict(payload.get("robot_count_statistics", {}))
    thresholds = dict(dominance.get("threshold_shares", {}))
    table = pd.DataFrame([
        {
            "Distribution": "Dominance (%)",
            "Mean": float(dominance["mean"]) * 100 if dominance.get("mean") is not None else None,
            "Median": float(dominance["median"]) * 100 if dominance.get("median") is not None else None,
            "Minimum": float(dominance["minimum"]) * 100 if dominance.get("minimum") is not None else None,
            "Maximum": float(dominance["maximum"]) * 100 if dominance.get("maximum") is not None else None,
            "> 0.50 (%)": (thresholds.get("0.5") or 0) * 100,
            "> 0.75 (%)": (thresholds.get("0.75") or 0) * 100,
            "> 0.90 (%)": (thresholds.get("0.9") or 0) * 100,
        },
        {
            "Distribution": "Robots per objective",
            "Mean": robot_stats.get("mean"),
            "Median": robot_stats.get("median"),
            "Minimum": robot_stats.get("minimum"),
            "Maximum": robot_stats.get("maximum"),
        },
    ])
    st.dataframe(table, width="stretch", hide_index=True)


def _render_aggregate_capabilities(payload: Mapping[str, Any]) -> None:
    demand = list(payload.get("capability_demand", []))
    demand_availability = list(payload.get("capability_demand_availability", []))
    st.markdown("### Capability Demand")
    st.caption(
        "Event-level demand counts required Task executions; objective frequency uses all selected "
        "objectives as denominator. Multi-capability Tasks contribute to each requirement."
    )
    if not demand:
        st.info("No capability requirements are available for the selected objectives.")
        return
    paper_table = pd.DataFrame([{
        "Execution / log": row["log_name"],
        "Objective type": row["objective_type"],
        "Objective ID (o)": row["objective_id"],
        "Capability (c)": row["capability"],
        "reqCount(c,o)": row["req_count"],
        "availability(c)": row["availability"],
        "|R|": row["team_size"],
        "availabilityRate(c) (%)": (
            round(float(row["availability_percentage"]), 1)
            if row.get("availability_percentage") is not None else None
        ),
        "demandAvailability(c,o)": (
            round(float(row["demand_availability"]), 3)
            if row.get("demand_availability") is not None else None
        ),
    } for row in demand_availability])
    st.markdown("#### Demand-to-availability values by objective")
    st.caption(
        "Each row implements the paper definition directly: demandAvailability(c,o) "
        "= reqCount(c,o) / availability(c). availabilityRate(c) = availability(c) / "
        "|R| x 100 expresses the percentage of the execution team able to provide c. "
        "No averaging or pooling is applied."
    )
    st.dataframe(paper_table, width="stretch", hide_index=True)
    st.download_button(
        "Download demandAvailability values (.csv)",
        data=paper_table.to_csv(index=False).encode("utf-8"),
        file_name="resource_perspective_demand_availability.csv",
        mime="text/csv",
        key="aggregate_demand_availability_download",
    )
    st.markdown("#### Visual exploration")
    go = _plotly_graph_objects()
    fig = go.Figure(go.Bar(
        x=[row["total_task_executions"] for row in demand],
        y=[row["capability"] for row in demand],
        orientation="h",
        marker_color="#2563EB",
        text=[f"{row['objective_percentage']:.1f}% objectives" for row in demand],
        textposition="outside",
        customdata=[[row["objective_count"], row["selected_objective_count"]] for row in demand],
        hovertemplate=(
            "<b>%{y}</b><br>Required Task executions: %{x}<br>Required in %{customdata[0]} / "
            "%{customdata[1]} objectives<extra></extra>"
        ),
    ))
    fig.update_layout(
        height=max(320, 50 * len(demand) + 120),
        margin=dict(l=30, r=130, t=25, b=50),
        xaxis_title="Required Task executions",
        yaxis=dict(autorange="reversed"),
    )
    st.plotly_chart(fig, width="stretch", config={"displaylogo": False})
    with st.expander("Capability demand and provider table", expanded=False):
        st.dataframe(pd.DataFrame(demand), width="stretch", hide_index=True)


def _render_aggregate_capability_matrix(payload: Mapping[str, Any]) -> None:
    rows = list(payload.get("capability_utilization", []))
    st.markdown("### Aggregated Robot × Capability Utilization")
    if not rows:
        st.info("No aggregated capability utilization is available.")
        return
    modes = {
        "Total Task executions": "total_task_executions",
        "Capability workload (%)": "capability_workload_percentage",
        "Objective instances": "objective_count",
        "Relevant-objective use (%)": "capability_use_percentage",
    }
    selected_mode = st.selectbox(
        "Matrix cell value",
        list(modes),
        key="aggregate_capability_matrix_mode",
    )
    value_key = modes[selected_mode]
    robots = sorted({str(row["robot_id"]) for row in rows})
    capabilities = sorted({str(row["capability"]) for row in rows})
    lookup = {
        (str(row["robot_id"]), str(row["capability"])): float(row[value_key])
        for row in rows
    }
    matrix = [[lookup.get((robot, capability), 0.0) for capability in capabilities] for robot in robots]
    go = _plotly_graph_objects()
    fig = go.Figure(go.Heatmap(
        z=matrix,
        x=capabilities,
        y=robots,
        text=[[f"{value:.1f}" if isinstance(value, float) and not value.is_integer() else f"{value:g}" for value in line] for line in matrix],
        texttemplate="%{text}",
        colorscale="Blues",
        zmin=0,
        hovertemplate=f"Robot: %{{y}}<br>Capability: %{{x}}<br>{selected_mode}: %{{z:.2f}}<extra></extra>",
    ))
    fig.update_layout(height=max(340, 45 * len(robots) + 140), margin=dict(l=70, r=25, t=25, b=90))
    st.plotly_chart(fig, width="stretch", config={"displaylogo": False})
    with st.expander("Aggregated utilization details", expanded=False):
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def _render_capability_concentration(payload: Mapping[str, Any]) -> None:
    rows = list(payload.get("capability_concentration", []))
    st.markdown("### Capability Concentration")
    st.caption("Capability dominance is the largest robot share of observed capability-related Task executions.")
    if not rows:
        st.info("No capability concentration values are available.")
        return
    go = _plotly_graph_objects()
    fig = go.Figure(go.Bar(
        x=[row["capability"] for row in rows],
        y=[row["capability_dominance_percentage"] for row in rows],
        marker_color="#7C3AED",
        text=[f"{row['capability_dominance_percentage']:.1f}%" for row in rows],
        textposition="outside",
        customdata=[[row["used_robot_count"], row["capable_robot_count"]] for row in rows],
        hovertemplate="%{x}<br>Dominance: %{y:.1f}%<br>Used robots: %{customdata[0]}<br>Capable robots: %{customdata[1]}<extra></extra>",
    ))
    fig.update_layout(height=350, yaxis_title="Capability workload dominance (%)", xaxis_title="Capability", margin=dict(l=50, r=20, t=25, b=80))
    st.plotly_chart(fig, width="stretch", config={"displaylogo": False})


def _render_aggregate_collaboration(payload: Mapping[str, Any]) -> None:
    rows = list(payload.get("collaboration_structures", []))
    st.markdown("### Collaboration Structures")
    st.caption(
        "Totals count unique formal occurrence rows. Per-objective averages count objective involvement; "
        "a pairwise occurrence may involve two selected objectives."
    )
    if not rows:
        st.info("No collaboration occurrence queries were available for this selection.")
        return
    table = pd.DataFrame([{
        "Structure": row["structure"],
        "Total occurrences": row["total_occurrences"],
        "Average per objective": round(float(row["average_per_objective"]), 3),
        "Objectives with occurrence": row["objectives_with_occurrence"],
        "Objective coverage (%)": round(float(row["objective_coverage_percentage"]), 2),
    } for row in rows])
    st.dataframe(table, width="stretch", hide_index=True)


def _render_resource_collaboration_relationships(payload: Mapping[str, Any]) -> None:
    rows = list(payload.get("resource_collaboration_rows", []))
    correlations = list(payload.get("resource_collaboration_correlations", []))
    if not rows or not correlations:
        return
    st.markdown("### Resource–Collaboration Relationships")
    st.caption("These are descriptive associations over selected objectives and do not imply causality.")
    labels = [row["relationship"] for row in correlations]
    selected = st.selectbox("Relationship", labels, key="aggregate_resource_relationship")
    spec = next(row for row in correlations if row["relationship"] == selected)
    go = _plotly_graph_objects()
    fig = go.Figure(go.Scatter(
        x=[row.get(spec["x_key"]) for row in rows],
        y=[row.get(spec["y_key"]) for row in rows],
        mode="markers",
        marker=dict(size=10, color="#2563EB", opacity=0.72),
        customdata=[[row.get("log_name"), row.get("objective_id")] for row in rows],
        hovertemplate="Log: %{customdata[0]}<br>Objective: %{customdata[1]}<br>x=%{x}<br>y=%{y}<extra></extra>",
    ))
    correlation = spec.get("correlation")
    title = f"{selected} | Pearson r={float(correlation):.3f}" if correlation is not None else f"{selected} | correlation unavailable"
    fig.update_layout(title=title, height=390, xaxis_title=spec["x_key"], yaxis_title=spec["y_key"], margin=dict(l=50, r=20, t=55, b=55))
    st.plotly_chart(fig, width="stretch", config={"displaylogo": False})


def _render_group_comparison(payload: Mapping[str, Any]) -> None:
    rows = list(payload.get("group_comparison", []))
    st.markdown("### Group Comparison")
    st.caption("Groups correspond to the existing EKG Log dimension, typically a strategy or execution configuration.")
    if not rows:
        st.info("No log groups are available.")
        return
    metrics = {
        "Mean dominance (%)": ("mean_dominance", 100.0),
        "Mean robots per objective": ("mean_robots_per_objective", 1.0),
        "Mean robot contribution (%)": ("mean_robot_contribution", 100.0),
        "Mean capability dominance (%)": ("mean_capability_dominance", 100.0),
        "Handovers": ("handovers", 1.0),
        "Handovers / objective": ("handover_average_per_objective", 1.0),
        "Capability-driven returns": ("capability_driven_returns", 1.0),
        "Capability-driven returns / objective": ("capability_driven_return_average_per_objective", 1.0),
        "Parallel collaborations": ("parallel_collaborations", 1.0),
        "Parallel collaborations / objective": ("parallel_collaboration_average_per_objective", 1.0),
    }
    selected = st.selectbox("Comparison metric", list(metrics), key="aggregate_group_metric")
    key, scale = metrics[selected]
    values = [float(row.get(key) or 0.0) * scale for row in rows]
    go = _plotly_graph_objects()
    fig = go.Figure(go.Bar(
        x=[row["group"] for row in rows],
        y=values,
        marker_color="#0F766E",
        text=[f"{value:.2f}" for value in values],
        textposition="outside",
        hovertemplate="Group: %{x}<br>Value: %{y:.3f}<extra></extra>",
    ))
    fig.update_layout(height=360, xaxis_title="Log / group", yaxis_title=selected, margin=dict(l=55, r=20, t=25, b=90))
    st.plotly_chart(fig, width="stretch", config={"displaylogo": False})
    with st.expander("Group comparison table", expanded=False):
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def render_aggregated_resource_perspective(
    driver: Any,
    database: Optional[str],
    catalog: Mapping[str, Mapping[str, str]],
    logs: List[str],
) -> None:
    """Render aggregation over an explicit set of Mission or Segment instances."""
    st.subheader("Aggregated Resource Perspective")
    st.caption(
        "Characterize robot contribution, capability use, and collaboration structures across an "
        "explicit set of objective instances. Mission and Segment instances are never mixed."
    )
    c1, c2 = st.columns([1.4, 1])
    with c1:
        selected_logs = st.multiselect(
            "Strategies / logs",
            logs,
            default=logs,
            key="aggregate_resource_logs",
        )
    with c2:
        objective_type = st.radio(
            "Aggregation unit",
            list(OBJECTIVE_TYPES),
            horizontal=True,
            key="aggregate_resource_objective_type",
        )
    if not selected_logs:
        st.info("Select at least one strategy/log.")
        return
    try:
        references = fetch_objective_references(
            driver, database, selected_logs, objective_type
        )
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not load objective instances: {exc}")
        return
    if not references:
        st.info(f"No {objective_type} instances were found for the selected logs.")
        return

    labels: Dict[str, Dict[str, str]] = {}
    multiple_logs = len(selected_logs) > 1
    for reference in references:
        label = (
            f"{reference['log_name']} :: {reference['objective_id']}"
            if multiple_logs else reference["objective_id"]
        )
        labels[label] = reference
    selected_labels = st.multiselect(
        f"Selected {objective_type} instances",
        list(labels),
        default=list(labels),
        key="aggregate_resource_objectives",
        help="Every percentage and distribution below uses exactly this selected objective set.",
    )
    selected_objectives = [labels[label] for label in selected_labels]
    if not selected_objectives:
        st.info("Select at least one objective instance.")
        return
    signature = (
        str(database or ""),
        objective_type,
        tuple(sorted((item["log_name"], item["objective_id"]) for item in selected_objectives)),
    )
    if st.button("Analyze selected objectives", type="primary", key="aggregate_resource_analyze"):
        try:
            with st.spinner(f"Aggregating {len(selected_objectives)} {objective_type} instances..."):
                st.session_state["aggregate_resource_payload"] = build_aggregated_resource_perspective(
                    driver, database, catalog, selected_objectives
                )
                st.session_state["aggregate_resource_signature"] = signature
        except Exception as exc:  # noqa: BLE001
            st.session_state.pop("aggregate_resource_payload", None)
            st.session_state.pop("aggregate_resource_signature", None)
            st.error(f"Could not build the Aggregated Resource Perspective: {exc}")
            return
    if st.session_state.get("aggregate_resource_signature") != signature:
        st.info("Select the aggregation set and press `Analyze selected objectives`.")
        return
    payload = st.session_state.get("aggregate_resource_payload")
    if not payload:
        return
    _render_aggregate_overview(payload)
    _render_robot_participation(payload)
    _render_contribution_distribution(payload)
    _render_objective_distributions(payload)
    _render_aggregate_capabilities(payload)
    _render_aggregate_capability_matrix(payload)
    _render_capability_concentration(payload)
    _render_aggregate_collaboration(payload)
    with st.expander("Resource–collaboration relationships", expanded=False):
        _render_resource_collaboration_relationships(payload)
    with st.expander("Grouped comparison", expanded=False):
        _render_group_comparison(payload)


def render_resource_perspective_tab(
    driver: Any,
    database: Optional[str],
    catalog: Mapping[str, Mapping[str, str]],
    logs: List[str],
) -> None:
    """Render instance-level and aggregate resource analyses from one shared pipeline."""
    instance_tab, aggregate_tab = st.tabs(
        ["Single Objective", "Aggregated Resource Perspective"]
    )
    with instance_tab:
        render_instance_resource_perspective(driver, database, catalog, logs)
    with aggregate_tab:
        render_aggregated_resource_perspective(driver, database, catalog, logs)
