"""Custom stylesheet and layout enhancements for the Streamlit UI."""
import streamlit as st

CUSTOM_CSS = """
<style>
.block-container {
    max-width: 1200px;
    padding-top: 2.2rem;
    padding-bottom: 3rem;
}
[data-testid="stImage"] img {
    height: 245px;
    object-fit: contain;
    background: #f6f5f3;
    border-radius: 8px;
}
[data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: 12px;
}
@media (max-width: 640px) {
    [data-testid="stImage"] img {
        height: 210px;
    }
}
</style>
"""


def inject_custom_css():
    """Inject responsive styling into the current Streamlit page."""
    st.html(CUSTOM_CSS)
