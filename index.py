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

    /* 타이틀·탭 한 줄: 폴더형 탭 바 배경 */
    div[data-testid="stHorizontalBlock"]:has(div[role="radiogroup"]) {
        align-items: flex-end !important;
        gap: 0.75rem !important;
        margin-bottom: 0 !important;
        padding: 0.35rem 0.5rem 0 0.5rem !important;
        border-bottom: 1px solid #cfd4dc;
        background: #eef1f5;
        border-radius: 6px 6px 0 0;
    }

    /* 폴더형 탭 */
    div[role="radiogroup"] {
        display: flex !important;
        flex-wrap: wrap !important;
        gap: 2px !important;
        align-items: flex-end !important;
        background: transparent !important;
        border: none !important;
        padding: 0 !important;
        margin: 0 !important;
    }
    div[role="radiogroup"] > label {
        margin: 0 !important;
        padding: 0.45rem 0.95rem !important;
        border: 1px solid transparent !important;
        border-bottom: none !important;
        border-radius: 6px 6px 0 0 !important;
        background: #eef1f5 !important;
        color: #5c6570 !important;
        font-weight: 600 !important;
        line-height: 1.2 !important;
        cursor: pointer !important;
        position: relative !important;
        top: 1px !important;
        box-shadow: none !important;
    }
    div[role="radiogroup"] > label:hover {
        background: #e3e7ed !important;
        color: #2f3640 !important;
    }
    /* 활성 탭: 흰 배경 + 상·좌·우 테두리, 하단은 콘텐츠와 이어짐 */
    div[role="radiogroup"] > label:has(input:checked) {
        background: #ffffff !important;
        color: #1f2933 !important;
        border-color: #cfd4dc !important;
        border-bottom: 1px solid #ffffff !important;
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
    "분석": tab01_analy.show,
    "검색": tab02_search.show,
    "관심": tab03_select.show,
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
