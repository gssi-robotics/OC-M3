# Object-Centric Collaboration Mining for Adaptive Multi-Robot Missions

This repository contains **OC-M3**, the supporting analysis implementation and replication package for the submitted paper _Object-Centric Collaboration Mining for Adaptive Multi-Robot Missions_.

OC-M3 imports multi-robot execution logs into a Neo4j event knowledge graph (EKG), instantiates collaboration structures, and provides object-centric analyses and exportable evaluation tables through a Streamlit interface.

The evaluation is intended to determine whether the proposed collaboration structures and indicators can **characterize and explain execution behavior induced by different multi-robot task-allocation strategies**. It does not claim to benchmark or rank the task-allocation algorithms themselves.

## Related MULTI-3 implementation

The multi-robot mission implementation framework is maintained in the separate [gssi-robotics/multi-3-ocpm](https://github.com/gssi-robotics/multi-3-ocpm) repository. Use that repository to inspect or run the **MULTI-3 framework** that drives the multi-robot executions.


## Main features

- Configurable import of event, robot, mission, segment, and task-capability tables.
- Neo4j EKG construction with Task and Control events.
- Personalized EKG aggregation by entity type or identifier.
- Detection and analysis of four paper-aligned collaboration structures:
  - **Robot handover**: work passes between robots within the same objective.
  - **Objective switch**: a robot moves between mission or segment objectives.
  - **Capability-driven return**: a robot returns to an objective after an intermediate task whose capability requirements explain the allocation change.
  - **Parallel collaboration**: objective instances overlap in time and involve robots concurrently.
- Mission, segment, robot, capability, duration, transition, process-map, timeline, and pairwise analyses.
- Single-objective and aggregated Resource Perspectives for robot contribution, capability demand, capability utilization, and collaboration relationships.
- Robot timelines that can include or hide Control events while preserving their explanatory context.
- Downloadable, analysis-ready CSV evaluation package.
- Paper replication logs and reusable EKG loader configurations.

## Event knowledge graph

The importer creates an EKG centered on the following concepts:

- `Event` nodes store the event identifier, activity, start/end timestamps, source log, and `Type` (`Task` or `Control`).
- `Entity` nodes represent objects such as `Robot`, `Mission`, and `Segment`.
- `Capability` nodes capture robot capabilities and task requirements.
- `CORR` relates events to participating entities.
- `PART_OF` represents entity containment, such as segments belonging to missions.
- `HAS` and `REQ` capture available and required capabilities.
- `DF` represents directly-follows relations among Task events for each process perspective.
- `DF_Control` represents the complete Task/Control event sequence of each robot.

The personalized aggregation module creates `Class` nodes, `OBS` relations, and aggregated `DF_C` relations. Classes may be defined by entity type-level attributes or individual identifiers.

## Repository structure

```text
OC-M3/
├── app/                         Streamlit application and analysis views
├── query-lib/                   EKG construction, Cypher queries, and evaluation utilities
└── replication-data/            Paper replication inputs
    ├── agriculture/
    ├── cleaning/
    └── order_management/
```


## Requirements

- Python 3.10 or newer (development currently uses Python 3.12)
- Neo4j 5.x
- Graphviz, including the system `dot` executable
- Python packages: `streamlit`, `pandas`, `neo4j`, `plotly`, `graphviz`, and `openpyxl`

Create a virtual environment and install the Python dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install streamlit pandas neo4j plotly graphviz openpyxl
```

Install the Graphviz system package if it is not already available:

```bash
# macOS with Homebrew
brew install graphviz

# Debian/Ubuntu
sudo apt-get install graphviz
```

Neo4j must be running before EKG creation or analysis. The application defaults to `neo4j://localhost:7687`. Each imported log is isolated in a dedicated database named `ekg-<log-name>`; this requires Neo4j multi-database support and `CREATE DATABASE` privileges for first-time creation.

## Run the application

From the repository root:

```bash
source .venv/bin/activate
streamlit run app/main.py
```

The interface contains three modules:

1. **Load EKG** maps CSV/XLSX columns, validates the input schema, creates a loader configuration, and executes the EKG construction queries.
2. **Aggregate EKG** builds and visualizes a personalized class-level aggregation, including class durations and aggregate duration summaries.
3. **Collaboration Analysis** instantiates the collaboration queries and provides evaluation panels, timelines, process maps, tables, and downloadable data.

Enter the Neo4j connection settings once in the shared sidebar. Use **Refresh EKG databases** to discover loader-created databases and select the active one. The selected database is passed to aggregation and collaboration-analysis sessions, and changing it invalidates results cached from the previous graph.

## Use the replication data

[`replication-data`](replication-data) contains the fixed EKG inputs used to reproduce the paper analyses. It covers three scenarios and three task-allocation strategies, for a total of nine execution logs:

| Scenario | Event logs | Loader configurations | Shared tables |
| --- | --- | --- | --- |
| Agriculture | `agriculture_{baseline,capability-preserving,closest}.csv` | `{baseline,capability-preserving,closest}.json` | `robot_table.csv`, `task_table.csv` |
| Cleaning | `cleaning_{baseline,capability-preserving,closest}.csv` | `{baseline,capability-preserving,closest}.json` | `robot_table.csv`, `task_table.csv` |
| Order management | `order_management_{baseline,capability-preserving,closest}.csv` | `{baseline,capability-preserving,closest}.json` | `robot_table.csv`, `task_table.csv` |

Each scenario directory contains:

- one event log per allocation strategy;
- `robot_table.csv`, which declares each robot's semicolon-separated capabilities;
- `task_table.csv`, which declares each task's semicolon-separated required capabilities;
- one JSON loader configuration per strategy, containing the complete column mapping and recommended `log_name`.

The CSV logs are the direct replication inputs for OC-M3. The JSON files are **OC-M3 loader configurations**, not MULTI-3 execution configurations. To regenerate executions rather than analyze the provided logs, use the [MULTI-3 implementation repository](https://github.com/gssi-robotics/multi-3-ocpm) and follow its documentation, then import the generated event tables into OC-M3.

### Import a replication log

The most portable procedure is to configure the importer from the supplied CSV files:

1. Start Neo4j, launch the Streamlit application, and open **Load EKG**.
2. Choose a unique **Log name**. The committed JSON for the selected execution provides the paper-facing value, such as `agri_baseline`, `clean_capability_preserving`, or `om_closest`.
3. Upload the selected scenario/strategy event log and map `event_id`, `activity`, `event_type`, `start_time`, and `end_time` to their corresponding fields.
4. Map the entity columns as `robot_id` to **Robot**, `mission_id` to **Mission**, and `segment_id` to **Segment**.
5. Upload the scenario's `robot_table.csv` as the optional Robot attribute table. Use `robot_id` as its unique identifier and include `capabilities`. Mission and Segment entities can be created directly from the event log.
6. Upload the scenario's `task_table.csv` as the task-capability table. Map `task_name` to the task/activity field and `required_capability` to the capability field.
7. Generate the backend configuration, review the target `ekg-<log-name>` database, and select **Create EKG**.
8. Repeat these steps for the other executions that must be analyzed. Each log is intentionally stored in its own Neo4j database.

Alternatively, select **Optional loader config JSON** and upload the strategy's JSON file. These committed configurations contain absolute file paths from the machine on which they were prepared. Before using one on another machine, replace every `path` value with the absolute path to the corresponding CSV in the local checkout. The UI validates the resolved paths before EKG creation.

### Analyze a replication log

1. In the shared sidebar, select **Refresh EKG databases**.
2. Select the `ekg-<log-name>` database for the execution to analyze.
3. Open **Aggregate EKG** or **Collaboration Analysis**. All subsequent queries, metrics, tables, and visualizations use the active database.
4. When switching to another execution, change the active database in the same selector. The analysis state is refreshed so results from different executions are not mixed.


### Extract the evaluation package

1. Refresh the EKG database list and select the database containing the desired replication log.
2. Open **Collaboration Analysis** and connect to the selected database.
3. Generate the evaluation dataset.
4. Inspect any export table directly in the interface or select **Download evaluation package (.zip)**.

The ZIP contains the following principal CSV files:

| File | Purpose |
| --- | --- |
| `strategy_summary.csv` | Paper-facing comparison by strategy and objective perspective. |
| `occurrence_counts.csv` | Collaboration-structure counts by strategy and perspective. |
| `occurrences.csv` | Concrete event-level occurrences, temporal values, capabilities, and Control-event context. |
| `indicators_long.csv` | Tidy, long-format analytical indicators for statistical analysis. |
| `collaboration_variants.csv` | Mission-level collaboration signatures, continuity, and performance measures. |
| `robot_handover_network.csv` | Directed robot-to-robot handover edges and temporal context. |
| `entity_duration_summary.csv` | Mission, robot, and segment elapsed-duration distributions. |
| `activity_duration_summary.csv` | Task and Control activity execution-duration distributions. |
| `activity_transition_summary.csv` | Task `DF` and all-event robot `DF_Control` transition-time distributions. |

Durations and transition times in the evaluation CSVs are expressed in seconds. The `strategy` column corresponds to the EKG `Log` property selected in the UI. To compare strategies or scenarios, export each dedicated database and combine equivalent CSV tables using this column while retaining the scenario and loader configuration as provenance.


## Input expectations

The importer is configurable, but a valid event table must provide columns that can be mapped to:

- a unique event identifier;
- an activity name;
- an event type whose values are `Task` or `Control`;
- start and end timestamps;
- the relevant entity identifiers, such as robot, mission, and segment.

Timestamps should be ISO-8601 compatible, for example `2026-01-01T08:00:00`. A task-capability table maps task/activity names to required capabilities, while the robot table identifies robots and their available capabilities.

## Reproducibility notes

- Import each execution log into its dedicated `ekg-<log-name>` database. This prevents identifier collisions and keeps analytical queries independent of log filters.
- Use distinct `log_name` values; they determine the database name, the EKG `Log` property, and the strategy identifier in exports.
- Select each database in turn when extracting results. Combine evaluation files externally only when that merge is intentional and documented.
- Control events are included in robot `DF_Control` sequences for explainability but excluded from Task-only `DF` inference.
- Parallel collaboration counts overlapping objective instances. It is not a synchronization relation between missions.
- Generated results depend on the selected scenario, allocation policy, robot fleet, seed, and generator version. Record all of these with reported results.

## Citation

If you use this software or the replication data, please refer to the submitted paper _Object-Centric Collaboration Mining for Adaptive Multi-Robot Missions_. Full bibliographic metadata will be added after publication.

## License

TBD.
