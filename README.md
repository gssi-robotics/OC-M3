# OC-M3: Object-Centric Collaboration Mining for Adaptive Multi-Robot Missions

This repository contains the supporting implementation and reproducibility data for the _Object-Centric Collaboration Mining for Adaptive Multi-Robot Missions_ journal submission. 

OC-M3 imports multi-robot execution logs into a Neo4j event knowledge graph (EKG), instantiates collaboration structures, and provides object-centric analyses and exportable evaluation tables through a Streamlit interface.

The evaluation is intended to determine whether the proposed collaboration structures and indicators can **characterize and explain execution behavior induced by different multi-robot task-allocation strategies**. It does not claim to benchmark or rank the task-allocation algorithms themselves.

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
- Robot timelines that can include or hide Control events while preserving their explanatory context.
- Downloadable, analysis-ready CSV evaluation package.
- Synthetic log generation and ground-truth occurrence data for reproducibility checks.

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
├── app/                              Streamlit application and analysis views
├── query-lib/                        EKG construction, Cypher queries, and evaluation utilities
├── replication-data/     Journal replication datasets for four allocation strategies
    └── agriculture/
    └── cleaning/
    └── order_management/ 
```

Generator development is tracked on the `log-generator` ([link](https://github.com/gssi-robotics/OC-M3/tree/log-generator)) branch. 

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

Neo4j must be running before EKG creation or analysis. The application defaults to `neo4j://localhost:7687`; credentials and the target Neo4j database can be changed in the sidebar.

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

Enter the Neo4j connection settings once in the shared sidebar. The selected database is passed to the loader, aggregation, and collaboration-analysis sessions.

## Reproduce the agriculture experiment

The paper-facing agriculture data are under [`replication-data/agriculture`](replication-data/agriculture). Four allocation strategies are provided for seed `10`:

| Strategy | Log name | Directory |
| --- | --- | --- |
| Auction | `agriculture_auction_seed10` | `agriculture_auction_seed10/` |
| Greedy | `agriculture_greedy_seed10` | `agriculture_greedy_seed10/` |
| Hungarian | `agriculture_hungarian_seed10` | `agriculture_hungarian_seed10/` |
| Random | `agriculture_random_seed10` | `agriculture_random_seed10/` |

Each directory contains the EKG input tables, source configuration, loader configuration, ground-truth assignments and collaboration occurrences, and summary tables.

### Load one strategy

1. Start Neo4j and launch the Streamlit application.
2. Open **Load EKG**.
3. Upload the strategy's `events.csv`, `robot_table.csv`, and `task_table.csv`, then confirm the displayed mappings; or load its `ekg_loader_config.json` after correcting its paths.
4. Keep cleanup disabled when loading multiple strategy logs into the same database. Enable it only when the target database should be cleared first.
5. Select **Create EKG** and wait for all import and inference queries to complete.
6. Repeat for the remaining strategies to compare them in one analysis database.

> **Portability note:** paths inside the committed `ekg_loader_config.json` files record the machine on which the datasets were generated. On another machine, either update every `path` field to the local checkout or recreate the configuration through **Load EKG**. The CSV data themselves are portable.


### Extract the evaluation package

1. Open **Collaboration Analysis** and connect to the database containing the imported logs.
2. Select the relevant strategy logs.
3. Generate the evaluation dataset.
4. Inspect any export table directly in the interface or select **Download evaluation package (.zip)**.

The ZIP contains the following CSV files:

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

Durations and transition times in the evaluation CSVs are expressed in seconds. The `strategy` column corresponds to the EKG `Log` property selected in the UI.


## Input expectations

The importer is configurable, but a valid event table must provide columns that can be mapped to:

- a unique event identifier;
- an activity name;
- an event type whose values are `Task` or `Control`;
- start and end timestamps;
- the relevant entity identifiers, such as robot, mission, and segment.

Timestamps should be ISO-8601 compatible, for example `2026-01-01T08:00:00`. A task-capability table maps task/activity names to required capabilities, while the robot table identifies robots and their available capabilities.

## Reproducibility notes

- Import all logs to be compared into the same Neo4j database without cleanup between imports, or select the appropriate database before each analysis.
- Use distinct `log_name` values; they become the EKG `Log` property and the strategy identifier in exports.
- Do not combine evaluation files produced from different Neo4j databases unless that merge is intentional and documented.
- Control events are included in robot `DF_Control` sequences for explainability but excluded from Task-only `DF` inference.
- Parallel collaboration counts overlapping objective instances. It is not a synchronization relation between missions.
- Generated results depend on the selected scenario, allocation policy, robot fleet, seed, and generator version. Record all of these with reported results.

## Citation

If you use this software or the replication data, please cite the accompanying OC-M3 journal article. Full bibliographic metadata will be added after publication.

## License

TBD.