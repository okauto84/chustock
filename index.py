"""ChuStock 메인 진입점. 타이틀 옆에 탭을 두고 각 화면을 연결한다."""

import streamlit as st

import tab01_analy
import tab02_search
import tab03_select
import tab04_adm

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

    /* 상단 여백 축소 */
    header[data-testid="stHeader"] {
        height: 0 !important;
        min-height: 0 !important;
    }
    .stAppDeployButton, [data-testid="stToolbar"] {
        display: none !important;
    }
    .block-container {
        padding-top: 0.6rem !important;
        padding-bottom: 1rem !important;
    }
    div[data-testid="stVerticalBlock"] > div:first-child {
        gap: 0.35rem !important;
    }

    .app-title {
        font-size: 18px !important;
        font-weight: 700;
        line-height: 1.8rem;
        margin: 0;
        white-space: nowrap;
        color: #e03131 !important;
    }

    /* 타이틀·탭 한 줄: 배경 투명, 하단 구분선만 */
    div[data-testid="stHorizontalBlock"]:has(div[role="radiogroup"]) {
        align-items: center !important;
        gap: 0.75rem !important;
        margin-bottom: 0.35rem !important;
        padding: 0.25rem 0 0.35rem 0 !important;
        border-bottom: 1px solid #dee2e6;
        background: transparent !important;
        border-radius: 0 !important;
    }

    /* 탭 */
    div[role="radiogroup"] {
        display: flex !important;
        flex-wrap: wrap !important;
        gap: 4px !important;
        align-items: center !important;
        background: transparent !important;
        border: none !important;
        padding: 0 !important;
        margin: 0 !important;
    }
    div[role="radiogroup"] > label {
        margin: 0 !important;
        padding: 0.4rem 0.9rem !important;
        border: 1px solid transparent !important;
        border-radius: 6px !important;
        background: transparent !important;
        color: #5c6570 !important;
        font-weight: 600 !important;
        line-height: 1.2 !important;
        cursor: pointer !important;
        position: static !important;
        top: auto !important;
        box-shadow: none !important;
    }
    div[role="radiogroup"] > label:hover {
        background: #f1f3f5 !important;
        color: #2f3640 !important;
    }
    /* 활성 탭: 연한 파란 바탕 */
    div[role="radiogroup"] > label:has(input:checked) {
        background: #d0ebff !important;
        color: #1864ab !important;
        border-color: #a5d8ff !important;
        border-bottom-color: #a5d8ff !important;
        z-index: 1 !important;
    }
    /* radio 원형 표시 숨김 */
    div[role="radiogroup"] > label > div:first-child {
        display: none !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

TAB_MAP = {
    "스크리닝": tab01_analy.show,
    "디테일": tab02_search.show,
    "가능성": tab03_select.show,
    "관리": tab04_adm.show,
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
