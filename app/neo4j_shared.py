from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st


DEFAULT_URI = "neo4j://localhost:7687"
DEFAULT_USER = "neo4j"
DEFAULT_PASSWORD = "12341234"
DEFAULT_DATABASE = ""
DATABASE_SCOPED_STATE_PREFIXES = (
    "agg_",
    "cpi_",
    "collab_",
    "evaluation_",
    "explain_",
    "resource_",
    "aggregate_resource_",
)


def database_name_for_log(log_name: str) -> str:
    """Return a deterministic Neo4j-safe database name for one execution log."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(log_name).strip().lower()).strip("-")
    slug = slug or "log"
    return f"ekg-{slug}"[:63].rstrip("-")


def ensure_database_available(driver: Any, database: str) -> None:
    """Reuse an accessible database or create it through Neo4j's system database."""
    try:
        with driver.session(database=database) as session:
            session.run("RETURN 1 AS ready").consume()
        return
    except Exception as access_error:  # noqa: BLE001
        escaped_database = database.replace("`", "``")
        try:
            with driver.session(database="system") as session:
                session.run(
                    f"CREATE DATABASE `{escaped_database}` IF NOT EXISTS WAIT 30 SECONDS"
                ).consume()
        except Exception as create_error:  # noqa: BLE001
            raise RuntimeError(
                f"Database `{database}` is not accessible and could not be created. "
                "Dedicated databases require Neo4j multi-database support and a user "
                f"with CREATE DATABASE privileges. Access error: {access_error}. "
                f"Creation error: {create_error}"
            ) from create_error

        with driver.session(database=database) as session:
            session.run("RETURN 1 AS ready").consume()


def discover_ekg_databases(driver: Any) -> List[str]:
    """Return online databases created by the EKG loader."""
    query = """
    SHOW DATABASES YIELD name, currentStatus
    WHERE name STARTS WITH 'ekg-' AND currentStatus = 'online'
    RETURN name
    ORDER BY name
    """
    with driver.session(database="system") as session:
        return [str(record["name"]) for record in session.run(query)]


def filter_discovered_databases(databases: List[str], query: str) -> List[str]:
    """Filter database names case-insensitively using all search terms."""
    terms = str(query).casefold().split()
    if not terms:
        return list(databases)
    return [
        database
        for database in databases
        if all(term in database.casefold() for term in terms)
    ]


def set_active_database(database: str) -> None:
    """Set the shared database and invalidate results from the previous graph."""
    database = str(database).strip()
    current = str(st.session_state.get("shared_neo4j_database", "")).strip()
    if database == current:
        return
    st.session_state["shared_neo4j_database"] = database
    for key in list(st.session_state):
        if key.startswith(DATABASE_SCOPED_STATE_PREFIXES):
            st.session_state.pop(key, None)


def _select_discovered_database() -> None:
    selected = st.session_state.get("shared_neo4j_database_selector")
    if selected:
        set_active_database(str(selected))


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
        help="Leave empty to use the default database, or select a discovered EKG database below.",
    )
    if st.button("Refresh EKG databases", key="refresh_ekg_databases"):
        settings = get_connection_settings()
        driver, error = get_neo4j_driver(
            settings["uri"], settings["user"], settings["password"]
        )
        if driver is None:
            st.session_state["shared_database_discovery_error"] = error
        else:
            try:
                st.session_state["shared_ekg_databases"] = discover_ekg_databases(driver)
                st.session_state["shared_database_discovery_error"] = None
            except Exception as exc:  # noqa: BLE001
                st.session_state["shared_database_discovery_error"] = (
                    f"Could not discover EKG databases: {exc}"
                )
            finally:
                driver.close()

    discovery_error = st.session_state.get("shared_database_discovery_error")
    if discovery_error:
        st.caption(str(discovery_error))

    databases = list(st.session_state.get("shared_ekg_databases", []))
    if databases:
        current = str(st.session_state.get("shared_neo4j_database", ""))
        st.markdown("**Discovered EKG databases**")
        search = st.text_input(
            "Filter discovered EKG databases",
            key="shared_ekg_database_filter",
            placeholder="Filter by execution, strategy, seed...",
            label_visibility="collapsed",
        )
        filtered_databases = filter_discovered_databases(databases, search)
        st.caption(f"Showing {len(filtered_databases)} of {len(databases)} databases")

        if not filtered_databases:
            st.info("No discovered database matches the current filter.")
        else:
            selector_key = "shared_neo4j_database_selector"
            if st.session_state.get(selector_key) not in filtered_databases:
                st.session_state.pop(selector_key, None)
            index = filtered_databases.index(current) if current in filtered_databases else None
            with st.container(height=260, border=True):
                st.radio(
                    "Discovered EKG databases",
                    options=filtered_databases,
                    index=index,
                    key=selector_key,
                    on_change=_select_discovered_database,
                    label_visibility="collapsed",
                    width="stretch",
                )


def render_connection_summary() -> None:
    settings = get_connection_settings()
    database = settings["database"].strip() or "default"
    st.caption(f"Shared Neo4j connection: `{settings['uri']}` / database `{database}` / user `{settings['user']}`")
