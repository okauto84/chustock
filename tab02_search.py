"""검색 탭."""

import streamlit as st


def show() -> None:
    st.subheader("검색")
    st.write("디테일한 필터 조건")
    st.write("변동성 축소 패턴, 거래량 마르는 패턴 등")
