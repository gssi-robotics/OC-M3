from __future__ import annotations

import streamlit as st

import collaboration_dashboard as collaboration_dashboard
import ekg_visualizer
import log_import
import neo4j_shared


def main() -> None:
    st.set_page_config(page_title="OC-M3", page_icon="🤖", layout="wide")

    with st.sidebar:
        st.title("OC-M3")
        page = st.radio(
            "Module",
            ["Load EKG", "Visualize EKG", "Collaboration Dashboard"],
            index=0,
        )
        neo4j_shared.render_shared_connection_controls()

    if page == "Load EKG":
        log_import.render_page()
        return

    if page == "Collaboration Dashboard":
        collaboration_dashboard.render_page()
        return

    ekg_visualizer.render_page()


if __name__ == "__main__":
    main()
