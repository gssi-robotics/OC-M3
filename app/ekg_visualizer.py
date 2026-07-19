import streamlit as st

from ekg.page import render_page


def main() -> None:
    st.set_page_config(
        page_title="Perspective-driven EKG Aggregation",
        layout="wide",
    )
    render_page()


if __name__ == "__main__":
    main()
