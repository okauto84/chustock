"""ChuStock 메인 진입점. 타이틀 옆에 탭을 두고 각 화면을 연결한다."""

import streamlit as st

import tab01_analy
import tab02_search
import tab03_select
import tab04_admin

st.set_page_config(
    page_title="ChuStock",
    page_icon="📈",
    layout="wide",
)

st.markdown(
    """
    <style>
    html, body, [class*="css"], .stApp, .stMarkdown, .stText, p, label, span, div {
        font-size: 12px !important;
    }
    h1, h2, h3, h4, h5, h6 {
        font-size: 12px !important;
    }
    .app-title {
        font-size: 18px !important;
        font-weight: 700;
        line-height: 2.2rem;
        margin: 0;
        white-space: nowrap;
    }
    div[data-testid="stHorizontalBlock"] {
        align-items: center;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

TAB_MAP = {
    "분석": tab01_analy.show,
    "검색": tab02_search.show,
    "관심": tab03_select.show,
    "컨트롤": tab04_admin.show,
}

title_col, tab_col = st.columns([1, 6], gap="small")
with title_col:
    st.markdown('<p class="app-title">ChuStock</p>', unsafe_allow_html=True)
with tab_col:
    selected = st.radio(
        "menu",
        list(TAB_MAP.keys()),
        horizontal=True,
        label_visibility="collapsed",
        key="main_tab",
    )

TAB_MAP[selected]()
