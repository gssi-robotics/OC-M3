"""
Cypher query builder for loading a multi-robot mission Event Knowledge Graph (EKG)
from the JSON configuration.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote


ENTITY_TYPE_ALIASES = {
    "Segment": "Segment",
}


# -----------------------------------------------------------------------------
# Basic helpers
# -----------------------------------------------------------------------------


def normalize_entity_type(entity_type: str) -> str:
    return ENTITY_TYPE_ALIASES.get(entity_type, entity_type)


def cypher_identifier(name: str) -> str:
    """Return a Cypher-safe backtick-escaped identifier."""
    escaped = str(name).replace("`", "``")
    return f"`{escaped}`"


def line_value(column: str) -> str:
    return f"line.{cypher_identifier(column)}"


def csv_uri(path: str) -> str:
    """Convert a local path to a file:/// URI for LOAD CSV."""
    p = Path(path).expanduser()
    if p.is_absolute():
        # Keep / separators and escape spaces/special characters.
        return "file:///" + quote(str(p).replace("\\", "/").lstrip("/"))
    return "file:///" + quote(str(p).replace("\\", "/"))


def value_expr(column: str, attr_type: str) -> str:
    """Build a Cypher expression that converts line[column] to attr_type."""
    raw = line_value(column)
    attr_type = (attr_type or "String").lower()

    if attr_type == "integer":
        return f"CASE WHEN trim({raw}) = '' THEN null ELSE toInteger({raw}) END"
    if attr_type == "float":
        return f"CASE WHEN trim({raw}) = '' THEN null ELSE toFloat({raw}) END"
    if attr_type == "boolean":
        return f"CASE WHEN trim({raw}) = '' THEN null ELSE toBoolean({raw}) END"
    if attr_type == "datetime":
        return f"CASE WHEN trim({raw}) = '' THEN null ELSE datetime({raw}) END"
    return f"CASE WHEN trim({raw}) = '' THEN null ELSE {raw} END"


def set_properties(alias: str, attrs: Iterable[str], attr_types: Dict[str, str]) -> str:
    """Generate SET clauses for dynamic CSV attributes."""
    clauses: List[str] = []
    for attr in attrs:
        clauses.append(f"SET {alias}.{cypher_identifier(attr)} = {value_expr(attr, attr_types.get(attr, 'String'))}")
    return "\n".join(clauses)


def query_step(name: str, query: str) -> Dict[str, str]:
    return {"name": name, "query": query.strip() + "\n"}


def cleanup_log_query(log_name: str) -> str:
    """Optional query for clearing the current database before re-importing it."""
    return f"""
MATCH (n)
DETACH DELETE n;
""".strip()


# -----------------------------------------------------------------------------
# Node loading queries
# -----------------------------------------------------------------------------


def load_events_query(config: Dict[str, Any]) -> str:
    events = config["events"]
    uri = csv_uri(events["path"])
    log_name = config["log_name"]

    event_id_col = config["event_id"]
    activity_col = config["event_activity"]
    event_type_col = config["event_type"]
    start_col = config["event_start"]
    end_col = config["event_end"]

    attrs = events.get("attr", [])
    attr_types = events.get("attr_types", {})
    extra_sets = set_properties("e", attrs, attr_types)

    return f"""
LOAD CSV WITH HEADERS FROM {json.dumps(uri)} AS line
WITH line
WHERE {line_value(event_id_col)} IS NOT NULL AND trim({line_value(event_id_col)}) <> ''
MERGE (e:Event {{event_id: {line_value(event_id_col)}}})
SET e.Log = {json.dumps(log_name)}
SET e.id = {line_value(event_id_col)}
SET e.activity = {line_value(activity_col)}
SET e.Type = {line_value(event_type_col)}
SET e.start = {value_expr(start_col, 'Datetime')}
SET e.end = {value_expr(end_col, 'Datetime')}
{extra_sets}
""".strip()


def load_entities_from_event_column_query(
    config: Dict[str, Any],
    event_column: str,
    entity_type: str,
) -> str:
    events = config["events"]
    uri = csv_uri(events["path"])
    log_name = config["log_name"]
    normalized_type = normalize_entity_type(entity_type)

    return f"""
LOAD CSV WITH HEADERS FROM {json.dumps(uri)} AS line
WITH DISTINCT {line_value(event_column)} AS entity_id
WHERE entity_id IS NOT NULL AND trim(entity_id) <> ''
MERGE (n:Entity {{type: {json.dumps(normalized_type)}, id: entity_id}})
SET n.Log = {json.dumps(log_name)}
SET n.{cypher_identifier(event_column)} = entity_id
""".strip()


def load_entities_from_attribute_file_query(
    config: Dict[str, Any],
    entity_source: str,
    entity_cfg: Dict[str, Any],
) -> str:
    uri = csv_uri(entity_cfg["path"])
    log_name = config["log_name"]
    entity_id_col = entity_cfg["entity_id"]
    entity_type = normalize_entity_type(entity_cfg["type"])
    attrs = entity_cfg.get("attr", [entity_id_col])
    attr_types = entity_cfg.get("attr_types", {})
    extra_sets = set_properties("n", attrs, attr_types)

    return f"""
LOAD CSV WITH HEADERS FROM {json.dumps(uri)} AS line
WITH line
WHERE {line_value(entity_id_col)} IS NOT NULL AND trim({line_value(entity_id_col)}) <> ''
MERGE (n:Entity {{type: {json.dumps(entity_type)}, id: {line_value(entity_id_col)}}})
SET n.Log = {json.dumps(log_name)}
SET n.sourceColumn = {json.dumps(entity_source)}
{extra_sets}
""".strip()


def load_entity_queries(config: Dict[str, Any]) -> List[Dict[str, str]]:
    steps: List[Dict[str, str]] = []

    for entity_type, event_column in config["events"].get("entity_columns", {}).items():
        # Always create entities from the event table first. This ensures nodes exist
        # even if an optional attribute table is incomplete.
        steps.append(
            query_step(
                f"load_{entity_type.lower()}_entities_from_event_log",
                load_entities_from_event_column_query(config, event_column, entity_type),
            )
        )

    for entity_source, entity_cfg in config.get("entities", {}).items():
        if entity_cfg.get("from_event_table", False):
            continue
        steps.append(
            query_step(
                f"load_{normalize_entity_type(entity_cfg['type']).lower()}_attributes_{entity_source}",
                load_entities_from_attribute_file_query(config, entity_source, entity_cfg),
            )
        )

    return steps


# -----------------------------------------------------------------------------
# Relationship loading queries
# -----------------------------------------------------------------------------


def load_corr_query(config: Dict[str, Any], entity_type: str, event_column: str) -> str:
    events = config["events"]
    uri = csv_uri(events["path"])
    log_name = config["log_name"]
    event_id_col = config["event_id"]
    event_type_col = config["event_type"]
    normalized_type = normalize_entity_type(entity_type)
    task_filter = "" if normalized_type == "Robot" else f"\n  AND {line_value(event_type_col)} = 'Task'"

    return f"""
LOAD CSV WITH HEADERS FROM {json.dumps(uri)} AS line
WITH line
WHERE {line_value(event_id_col)} IS NOT NULL
  AND trim({line_value(event_id_col)}) <> ''
  AND {line_value(event_column)} IS NOT NULL
  AND trim({line_value(event_column)}) <> ''{task_filter}
MATCH (e:Event {{event_id: {line_value(event_id_col)}}})
MATCH (n:Entity {{type: {json.dumps(normalized_type)}, id: {line_value(event_column)}}})
MERGE (e)-[:CORR]->(n)
""".strip()


def load_part_of_query(config: Dict[str, Any]) -> Optional[str]:
    entity_columns = config["events"].get("entity_columns", {})
    segment_col = entity_columns.get("Segment") or entity_columns.get("Segment")
    mission_col = entity_columns.get("Mission")
    if not segment_col or not mission_col:
        return None

    events = config["events"]
    uri = csv_uri(events["path"])
    event_type_col = config["event_type"]

    return f"""
LOAD CSV WITH HEADERS FROM {json.dumps(uri)} AS line
WITH line
WHERE {line_value(event_type_col)} = 'Task'
WITH DISTINCT {line_value(segment_col)} AS segment_id, {line_value(mission_col)} AS mission_id
WHERE segment_id IS NOT NULL AND trim(segment_id) <> ''
  AND mission_id IS NOT NULL AND trim(mission_id) <> ''
MATCH (f:Entity {{type: "Segment", id: segment_id}})
MATCH (m:Entity {{type: "Mission", id: mission_id}})
MERGE (f)-[:PART_OF]->(m)
""".strip()


def load_capability_nodes_query(config: Dict[str, Any]) -> str:
    task_caps = config["task_capabilities"]
    uri = csv_uri(task_caps["path"])
    log_name = config["log_name"]
    cap_col = task_caps["capability_column"]

    return f"""
LOAD CSV WITH HEADERS FROM {json.dumps(uri)} AS line
WITH DISTINCT {line_value(cap_col)} AS capability
WHERE capability IS NOT NULL AND trim(capability) <> ''
MERGE (c:Capability {{name: capability}})
SET c.Log = {json.dumps(log_name)}
""".strip()


def load_req_query(config: Dict[str, Any]) -> str:
    events = config["events"]
    task_caps = config["task_capabilities"]
    event_uri = csv_uri(events["path"])
    cap_uri = csv_uri(task_caps["path"])
    log_name = config["log_name"]

    event_id_col = config["event_id"]
    activity_col = config["event_activity"]
    event_type_col = config["event_type"]
    task_col = task_caps["task_column"]
    cap_col = task_caps["capability_column"]

    return f"""
        LOAD CSV WITH HEADERS FROM {json.dumps(event_uri)} AS eventLine
        WITH eventLine
        WHERE {f'eventLine.{cypher_identifier(event_id_col)}'} IS NOT NULL
        AND trim({f'eventLine.{cypher_identifier(event_id_col)}'}) <> ''
        AND eventLine.{cypher_identifier(event_type_col)} = 'Task'
        LOAD CSV WITH HEADERS FROM {json.dumps(cap_uri)} AS capLine
        WITH eventLine, capLine
        WHERE capLine.{cypher_identifier(task_col)} = eventLine.{cypher_identifier(activity_col)}
        AND capLine.{cypher_identifier(cap_col)} IS NOT NULL
        AND trim(capLine.{cypher_identifier(cap_col)}) <> ''
        MATCH (e:Event {{event_id: eventLine.{cypher_identifier(event_id_col)}}})
        MERGE (c:Capability {{name: capLine.{cypher_identifier(cap_col)}}})
        SET c.Log = {json.dumps(log_name)}
        MERGE (e)-[:REQ]->(c)
        """.strip()


def infer_observed_has_query(config: Dict[str, Any]) -> str:
    """
    Derive Robot-[:HAS]->Capability relations from the capability list
    stored on Robot entity nodes.
    """
    return f"""
            MATCH (ro:Entity {{type: "Robot"}})
            WHERE ro.capabilities IS NOT NULL
            UNWIND split(ro.capabilities, ";") AS raw_capability
            WITH DISTINCT ro, trim(toString(raw_capability)) AS capability_name
            WHERE capability_name <> ""
            MERGE (c:Capability {{
                name: capability_name,
                Log: ro.Log}})
            MERGE (ro)-[:HAS]->(c)
            """.strip()


def load_relationship_queries(config: Dict[str, Any]) -> List[Dict[str, str]]:
    steps: List[Dict[str, str]] = []

    for entity_type, event_column in config["events"].get("entity_columns", {}).items():
        steps.append(
            query_step(
                f"load_corr_{entity_type.lower()}",
                load_corr_query(config, entity_type, event_column),
            )
        )

    part_of = load_part_of_query(config)
    if part_of:
        steps.append(query_step("load_part_of_segment_mission", part_of))

    steps.append(query_step("load_capability_nodes", load_capability_nodes_query(config)))
    steps.append(query_step("load_req_event_capability", load_req_query(config)))
    steps.append(query_step("infer_observed_robot_has_capability", infer_observed_has_query(config)))

    return steps


# -----------------------------------------------------------------------------
# Derived directly-follows relations
# -----------------------------------------------------------------------------


def derive_df_query(config: Dict[str, Any], entity_type: str) -> str:
    """Create interval-based directly-follows edges between Task events."""
    normalized_type = normalize_entity_type(entity_type)

    return f"""
MATCH (t:Entity {{type: {json.dumps(normalized_type)}}})<-[:CORR]-(e1:Event {{Type: "Task"}})
MATCH (t)<-[:CORR]-(e2:Event {{Type: "Task"}})
WHERE e1.event_id <> e2.event_id
  AND e1.end <= e2.start
  AND NOT EXISTS {{
    MATCH (t)<-[:CORR]-(e3:Event {{Type: "Task"}})
    WHERE e3.event_id <> e1.event_id
      AND e3.event_id <> e2.event_id
      AND e1.end <= e3.start
      AND e3.end <= e2.start
  }}
MERGE (e1)-[df:DF {{perspective_id: t.id, type: t.type}}]->(e2)
SET df.transitionTimeSeconds = duration.inSeconds(e1.end, e2.start).seconds
SET df.transitionTime = duration.between(e1.end, e2.start)
""".strip()


def derive_df_control_query(config: Dict[str, Any]) -> Optional[str]:
    """Create robot-perspective directly-follows edges across all event types."""
    if "Robot" not in config["events"].get("entity_columns", {}):
        return None

    return """
MATCH (robot:Entity {type: "Robot"})<-[:CORR]-(e1:Event)
MATCH (robot)<-[:CORR]-(e2:Event)
WHERE e1.event_id <> e2.event_id
  AND e1.end <= e2.start
  AND NOT EXISTS {
    MATCH (robot)<-[:CORR]-(e3:Event)
    WHERE e3.event_id <> e1.event_id
      AND e3.event_id <> e2.event_id
      AND e1.end <= e3.start
      AND e3.end <= e2.start
  }
MERGE (e1)-[df:DF_Control {perspective_id: robot.id, type: robot.type}]->(e2)
SET df.transitionTimeSeconds = duration.inSeconds(e1.end, e2.start).seconds
SET df.transitionTime = duration.between(e1.end, e2.start)
""".strip()


def derive_all_df_queries(config: Dict[str, Any]) -> List[Dict[str, str]]:
    entity_types = list(config["events"].get("entity_columns", {}).keys())
    steps = [
        query_step(f"derive_df_{normalize_entity_type(entity_type).lower()}", derive_df_query(config, entity_type))
        for entity_type in entity_types
    ]
    df_control = derive_df_control_query(config)
    if df_control:
        steps.append(query_step("derive_df_control_robot", df_control))
    return steps


# -----------------------------------------------------------------------------
# Full load plan
# -----------------------------------------------------------------------------


def build_load_plan(config: Dict[str, Any], include_cleanup: bool = False) -> List[Dict[str, str]]:
    """Return an ordered list of Cypher query steps."""
    steps: List[Dict[str, str]] = []

    if include_cleanup:
        steps.append(query_step("cleanup_existing_log", cleanup_log_query(config["log_name"])))

    steps.append(query_step("load_event_nodes", load_events_query(config)))
    steps.extend(load_entity_queries(config))
    steps.extend(load_relationship_queries(config))
    steps.extend(derive_all_df_queries(config))

    return steps


def write_load_plan(config: Dict[str, Any], out_path: str, include_cleanup: bool = False) -> None:
    steps = build_load_plan(config, include_cleanup=include_cleanup)
    with open(out_path, "w", encoding="utf-8") as handle:
        for i, step in enumerate(steps, start=1):
            handle.write(f"// -----------------------------------------------------------------------------\n")
            handle.write(f"// Step {i}: {step['name']}\n")
            handle.write(f"// -----------------------------------------------------------------------------\n")
            handle.write(step["query"].strip())
            handle.write("\n\n")


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Neo4j LOAD CSV Cypher queries for the EKG loader config.")
    parser.add_argument("config", help="Path to the loader_config.json produced by Streamlit.")
    parser.add_argument("--out", help="Optional output .cypher file. If omitted, queries are printed.")
    parser.add_argument("--cleanup", action="store_true", help="Include a first step that deletes nodes for the same log.")
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as handle:
        config = json.load(handle)

    if args.out:
        write_load_plan(config, args.out, include_cleanup=args.cleanup)
        print(f"Wrote Cypher load plan to {args.out}")
        return

    for i, step in enumerate(build_load_plan(config, include_cleanup=args.cleanup), start=1):
        print(f"// Step {i}: {step['name']}")
        print(step["query"])
        print()


if __name__ == "__main__":
    main()
