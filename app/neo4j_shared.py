from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import streamlit as st


DEFAULT_URI = "neo4j://localhost:7687"
DEFAULT_USER = "neo4j"
DEFAULT_PASSWORD = "12341234"
DEFAULT_DATABASE = ""


def ensure_defaults() -> None:
    defaults = {
        "shared_neo4j_uri": DEFAULT_URI,
        "shared_neo4j_user": DEFAULT_USER,
        "shared_neo4j_password": DEFAULT_PASSWORD,
        "shared_neo4j_database": DEFAULT_DATABASE,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def get_connection_settings() -> Dict[str, str]:
    ensure_defaults()
    return {
        "uri": str(st.session_state.get("shared_neo4j_uri", DEFAULT_URI)),
        "user": str(st.session_state.get("shared_neo4j_user", DEFAULT_USER)),
        "password": str(st.session_state.get("shared_neo4j_password", DEFAULT_PASSWORD)),
        "database": str(st.session_state.get("shared_neo4j_database", DEFAULT_DATABASE)),
    }


def session_kwargs(database: Optional[str]) -> Dict[str, str]:
    if database and database.strip():
        return {"database": database.strip()}
    return {}


def get_neo4j_driver(uri: str, user: str, password: str) -> Tuple[Optional[Any], Optional[str]]:
    try:
        from neo4j import GraphDatabase
    except ImportError:
        return None, "Neo4j Python driver is not installed. Install it with `pip install neo4j`."

    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        driver.verify_connectivity()
        return driver, None
    except Exception as exc:  # noqa: BLE001
        try:
            driver.close()
        except Exception:
            pass
        return None, f"Neo4j is not reachable at `{uri}`. Details: {exc}"


def render_shared_connection_controls() -> None:
    ensure_defaults()
    st.header("Neo4j")
    st.text_input("Neo4j URI", key="shared_neo4j_uri")
    st.text_input("Neo4j user", key="shared_neo4j_user")
    st.text_input("Neo4j password", type="password", key="shared_neo4j_password")
    st.text_input(
        "Neo4j database",
        key="shared_neo4j_database",
        help="Leave empty to use the default database.",
    )


def render_connection_summary() -> None:
    settings = get_connection_settings()
    database = settings["database"].strip() or "default"
    st.caption(f"Shared Neo4j connection: `{settings['uri']}` / database `{database}` / user `{settings['user']}`")
