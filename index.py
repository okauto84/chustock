"""ChuStock 메인 진입점. 상단 탭으로 분석/검색/관심/컨트롤 화면을 연결한다."""

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

st.title("ChuStock")

tab_analy, tab_search, tab_select, tab_admin = st.tabs(
    ["분석", "검색", "관심", "컨트롤"]
)

with tab_analy:
    tab01_analy.show()

with tab_search:
    tab02_search.show()

with tab_select:
    tab03_select.show()

with tab_admin:
    tab04_admin.show()
