import streamlit as st

from collaboration_ekg.page import render_page


def main() -> None:
    st.set_page_config(page_title="Collaboration Pattern Inspector",page_icon="🔎",layout="wide")
    render_page()


if __name__ == "__main__":
    main()
