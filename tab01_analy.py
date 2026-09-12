"""분석 탭: 이동평균·RS·신고가 조건으로 ETF를 걸러 트리 그리드로 보여준다."""

import itertools
import json
from pathlib import Path

import streamlit as st

BASE_DIR = Path(__file__).resolve().parent
CATEGORY_FILE = BASE_DIR / "data" / "stock" / "031_EtfCategorization.json"
ETF_LIST_FILE = BASE_DIR / "data" / "stock" / "030_EtfList.json"
ETF_VALUE_DIR = BASE_DIR / "data" / "values" / "etf"

# 라벨 -> 종가 다음으로 이어서 비교할 이동평균 키(앞에서부터 큰 값이어야 조건 충족)
MA_FILTERS: dict[str, tuple[str, ...]] = {
    "전체": (),
    "종가>MA10": ("ma10",),
    "종가>MA10>MA20": ("ma10", "ma20"),
    "종가>MA10>MA20>MA30": ("ma10", "ma20", "ma30"),
    "종가>MA10>MA20>MA30>MA50": ("ma10", "ma20", "ma30", "ma50"),
    "종가>MA10>MA20>MA30>MA50>MA100": ("ma10", "ma20", "ma30", "ma50", "ma100"),
    "종가>MA10>MA20>MA30>MA50>MA100>MA150": (
        "ma10",
        "ma20",
        "ma30",
        "ma50",
        "ma100",
        "ma150",
    ),
}

RS_FILTERS: dict[str, bool] = {
    "전체": False,
    "RS20>RS50": True,
}

# 라벨 -> 신고가(Top52) 대비 허용하는 하락률 상한
# (Top52 - value) / Top52 이 이 값 이하면 조건 충족
TOP52_FILTERS: dict[str, float | None] = {
    "전체": None,
    "5%": 0.05,
    "10%": 0.10,
    "15%": 0.15,
    "20%": 0.20,
    "25%": 0.25,
    "30%": 0.30,
    "40%": 0.40,
    "50%": 0.50,
}

GRID_RATIO = (3, 7)  # 트리(종목) : 구성 종목
PAGE_SIZE = 30  # 한 분류에 ETF가 수백 개라 나눠 그린다

INDENT_PX = 18  # 한 계층을 들여쓰는 폭
GUIDE_PX = 9  # 부모 아이콘 중앙을 지나는 세로 연결선의 x 좌표
GUIDE_COLOR = "#b8bfc6"
MAX_DEPTH = 2  # 섹터(0) → 세부 분류(1) → 종목(2)
ROW_PREFIX = "analytree"  # 컨테이너 key → CSS class(st-key-...) 로 연결선을 그린다

MA_KEY = "analy_ma"
RS_KEY = "analy_rs"
TOP52_KEY = "analy_top52"
OPEN_KEY = "analy_open_nodes"
LIMIT_KEY = "analy_page_limits"
PICK_KEY = "analy_picked_item"


def _file_signature(path: Path) -> tuple[float, int]:
    """파일이 바뀌면 캐시를 새로 읽도록 (수정시각, 크기)를 돌려준다."""
    try:
        stat = path.stat()
    except OSError:
        return (0.0, 0)
    return (stat.st_mtime, stat.st_size)


def _dir_signature(directory: Path) -> tuple[int, float]:
    """디렉터리 안 JSON의 (개수, 최신 수정시각)."""
    times = [path.stat().st_mtime for path in directory.glob("*.json")]
    return (len(times), max(times, default=0.0))


@st.cache_data(show_spinner=False)
def load_categories(signature: tuple[float, int]) -> dict[str, dict[str, list[str]]]:
    """sector -> sectorlist -> itemcode[] 분류 트리."""
    try:
        loaded = json.loads(CATEGORY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


@st.cache_data(show_spinner=False)
def load_etf_list(signature: tuple[float, int]) -> dict[str, dict]:
    """itemcode -> ETF 종목 정보(종목명·구성 종목)."""
    try:
        loaded = json.loads(ETF_LIST_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {
        str(row["itemcode"]): row
        for row in loaded
        if isinstance(row, dict) and row.get("itemcode")
    }


@st.cache_data(show_spinner="종목 지표를 읽는 중입니다…")
def load_latest_values(signature: tuple[int, float]) -> tuple[str | None, dict[str, dict]]:
    """전 종목 공통 최신 기준일과, 그 날짜 지표만 담은 itemcode -> 레코드."""
    by_code: dict[str, dict] = {}
    for path in ETF_VALUE_DIR.glob("*.json"):
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):  # 깨진 파일은 건너뛴다
            continue
        rows = [row for row in loaded if isinstance(row, dict) and row.get("date")]
        if rows:
            by_code[path.stem] = max(rows, key=lambda row: row["date"])
    if not by_code:
        return None, {}
    # 파일마다 마지막 날이 다를 수 있어, 검색은 그중 가장 늦은 날짜만 쓴다
    as_of = max(row["date"] for row in by_code.values())
    return as_of, {
        itemcode: row for itemcode, row in by_code.items() if row["date"] == as_of
    }


@st.cache_data(show_spinner=False)
def build_holder_index(signature: tuple[float, int]) -> dict[str, list[str]]:
    """구성 종목명 -> 그 종목을 담고 있는 ETF itemcode[]."""
    index: dict[str, list[str]] = {}
    for itemcode, row in load_etf_list(signature).items():
        for name in row.get("itemlist") or []:
            index.setdefault(str(name), []).append(itemcode)
    return index


def passes_filters(
    record: dict | None,
    ma_keys: tuple[str, ...],
    rs_only: bool,
    top52_ratio: float | None,
) -> bool:
    """최근 지표 레코드가 세 콤보박스 조건을 모두 만족하는지 본다."""
    if record is None:
        return False

    value = record.get("value") or 0.0
    if value <= 0:
        return False

    if ma_keys:
        averages = [record.get(key) or 0.0 for key in ma_keys]
        # 상장 기간이 짧아 이동평균이 채워지지 않은 종목(0)은 비교 대상에서 뺀다
        if any(average <= 0 for average in averages):
            return False
        chain = [value, *averages]
        if any(upper <= lower for upper, lower in zip(chain, chain[1:])):
            return False

    if rs_only and (record.get("RS20") or 0.0) <= (record.get("RS50") or 0.0):
        return False

    if top52_ratio is not None:
        top52 = record.get("Top52") or 0.0
        if top52 <= 0:
            return False
        # 신고가 대비 하락률: (Top52 - value) / Top52 이 선택한 % 이내인지 본다
        # 예) value=100, Top52=120 → 16.67% → 20% 포함, 15% 제외
        if (top52 - value) / top52 > top52_ratio:
            return False

    return True


def filter_tree(
    categories: dict[str, dict[str, list[str]]],
    values: dict[str, dict],
    ma_keys: tuple[str, ...],
    rs_only: bool,
    top52_ratio: float | None,
) -> dict[str, dict[str, list[str]]]:
    """조건을 만족하는 ETF만 남긴 분류 트리. 종목이 없는 분류는 뺀다."""
    tree: dict[str, dict[str, list[str]]] = {}
    for sector, sectorlists in categories.items():
        kept_lists: dict[str, list[str]] = {}
        for sectorlist, itemcodes in sectorlists.items():
            kept = [
                itemcode
                for itemcode in itemcodes
                if passes_filters(values.get(itemcode), ma_keys, rs_only, top52_ratio)
            ]
            if kept:
                kept_lists[sectorlist] = kept
        if kept_lists:
            tree[sector] = kept_lists
    return tree


def _toggle_node(node: str) -> None:
    opened: set[str] = st.session_state.setdefault(OPEN_KEY, set())
    opened.symmetric_difference_update({node})


def _grow_page(node: str) -> None:
    limits: dict[str, int] = st.session_state.setdefault(LIMIT_KEY, {})
    limits[node] = limits.get(node, PAGE_SIZE) + PAGE_SIZE


def _reset_tree() -> None:
    """검색 버튼을 누르면 최신 지표를 다시 읽고 펼친 상태·선택을 처음으로 돌린다."""
    load_latest_values.clear()
    st.session_state[OPEN_KEY] = set()
    st.session_state[LIMIT_KEY] = {}
    st.session_state[PICK_KEY] = None


def _pick_holding(pills_key: str, itemcode: str) -> None:
    picked = st.session_state.get(pills_key)
    st.session_state[PICK_KEY] = (
        {"name": picked, "itemcode": itemcode} if picked else None
    )
    # 다른 행에 남아 있는 선택을 지워 화면에 한 종목만 선택된 상태로 둔다
    for key in list(st.session_state):
        if str(key).startswith("analy_pills_") and key != pills_key:
            st.session_state[key] = None


def _row_prefix(flags: tuple[bool, ...]) -> str:
    """계층마다 '마지막 형제인가'를 CSS class에 쓸 문자열로 만든다."""
    if not flags:
        return f"{ROW_PREFIX}-root"
    return f"{ROW_PREFIX}-" + "".join("1" if flag else "0" for flag in flags)


def _guide_css() -> str:
    """깊이·형제 위치 조합마다 세로선과 꺾임선을 배경으로 그리는 CSS."""
    rules = [
        # 행 간격이 남으면 세로선이 끊겨 보이므로 버튼·문단 여백을 줄인다
        f'div[class*="st-key-{ROW_PREFIX}-"] button {{'
        " padding-top: 0; padding-bottom: 0; min-height: 1.5rem;"
        " width: auto !important; max-width: 100%; }",
        # Streamlit tertiary 버튼은 안쪽 flex가 가운데라 연결선에서 라벨이 떨어진다
        f'div[class*="st-key-{ROW_PREFIX}-"] button,'
        f' div[class*="st-key-{ROW_PREFIX}-"] button *'
        " { justify-content: flex-start !important; text-align: left !important; }",
        f'div[class*="st-key-{ROW_PREFIX}-"]'
        " [data-testid='stMarkdownContainer'],"
        f' div[class*="st-key-{ROW_PREFIX}-"]'
        " [data-testid='stMarkdownContainer'] p,"
        f' div[class*="st-key-{ROW_PREFIX}-"]'
        " [data-testid='stMarkdownContainer'] div"
        " { text-align: left !important; justify-content: flex-start !important;"
        " margin: 0; }",
        f'div[class*="st-key-{ROW_PREFIX}-"] p {{ margin-bottom: 0; }}',
        f'div[class*="st-key-{ROW_PREFIX}-"] [data-testid="stIconMaterial"]'
        " { font-size: 15px !important; }",
    ]
    for depth in range(1, MAX_DEPTH + 1):
        for flags in itertools.product((False, True), repeat=depth):
            # (x 위치, 선 크기) 목록을 배경 레이어로 쌓는다
            layers: list[tuple[str, str]] = []
            for level, ancestor_is_last in enumerate(flags[:-1]):
                # 위 계층에 남은 형제가 있으면 그 칸의 세로선을 계속 내려 긋는다
                if not ancestor_is_last:
                    layers.append((f"{GUIDE_PX + INDENT_PX * level}px 0", "1px 100%"))
            own_x = GUIDE_PX + INDENT_PX * (depth - 1)
            # 마지막 형제는 세로선을 행 중간까지만 그어 └ 모양으로 닫는다
            layers.append((f"{own_x}px 0", "1px 50%" if flags[-1] else "1px 100%"))
            layers.append((f"{own_x}px 50%", f"{INDENT_PX - GUIDE_PX}px 1px"))

            selector = f'div[class*="st-key-{_row_prefix(flags)}-"]'
            gradients = ", ".join(
                f"linear-gradient({GUIDE_COLOR}, {GUIDE_COLOR})" for _ in layers
            )
            rules.append(
                f"{selector} {{"
                f" background-image: {gradients};"
                f" background-size: {', '.join(size for _, size in layers)};"
                f" background-position: {', '.join(spot for spot, _ in layers)};"
                " background-repeat: no-repeat; }"
            )
            rules.append(
                f'{selector} div[data-testid="stColumn"]:first-of-type'
                f" {{ padding-left: {INDENT_PX * depth}px; }}"
            )
    return "<style>\n" + "\n".join(rules) + "\n</style>"


TREE_CSS = _guide_css()


def _tree_rows(
    tree: dict[str, dict[str, list[str]]],
    opened: set[str],
    limits: dict[str, int],
) -> list[dict]:
    """펼친 노드만 따라가며 그릴 행을 계층 순서대로 늘어놓는다."""
    rows: list[dict] = []
    sectors = list(tree.items())
    for sector, sectorlists in sectors:
        rows.append(
            {
                "kind": "branch",
                "flags": (),
                "node": sector,
                "label": sector,
                "count": sum(len(codes) for codes in sectorlists.values()),
            }
        )
        if sector not in opened:
            continue

        entries = list(sectorlists.items())
        for list_index, (sectorlist, itemcodes) in enumerate(entries):
            list_is_last = list_index == len(entries) - 1
            node = f"{sector}/{sectorlist}"
            rows.append(
                {
                    "kind": "branch",
                    "flags": (list_is_last,),
                    "node": node,
                    "label": sectorlist,
                    "count": len(itemcodes),
                }
            )
            if node not in opened:
                continue

            shown = itemcodes[: limits.get(node, PAGE_SIZE)]
            remaining = len(itemcodes) - len(shown)
            for item_index, itemcode in enumerate(shown):
                is_last = item_index == len(shown) - 1 and not remaining
                rows.append(
                    {
                        "kind": "leaf",
                        "flags": (list_is_last, is_last),
                        "itemcode": itemcode,
                    }
                )
            if remaining:
                rows.append(
                    {
                        "kind": "more",
                        "flags": (list_is_last, True),
                        "node": node,
                        "remaining": remaining,
                    }
                )
    return rows


def _render_branch(row: dict, opened: set[str]) -> None:
    """섹터·세부 분류 행: 접고 펴는 네모 아이콘과 이름을 그린다."""
    tree_col, _ = st.columns(GRID_RATIO, vertical_alignment="center")
    with tree_col:
        is_open = row["node"] in opened
        st.button(
            f"{row['label']} ({row['count']})",
            icon=(
                ":material/indeterminate_check_box:"
                if is_open
                else ":material/add_box:"
            ),
            key=f"analy_node_{row['node']}",
            on_click=_toggle_node,
            args=(row["node"],),
            type="tertiary",
            width="content",
        )


def _render_more(row: dict) -> None:
    """한 분류에서 아직 못 그린 종목을 더 불러오는 행."""
    tree_col, _ = st.columns(GRID_RATIO, vertical_alignment="center")
    with tree_col:
        st.button(
            f"남은 {row['remaining']}종목 중 {min(row['remaining'], PAGE_SIZE)}개 더 보기",
            icon=":material/more_horiz:",
            key=f"analy_more_{row['node']}",
            on_click=_grow_page,
            args=(row["node"],),
            type="tertiary",
            width="content",
        )


def _render_leaf(itemcode: str, item: dict, record: dict | None) -> None:
    """ETF 한 종목: 왼쪽은 종목명, 오른쪽은 구성 종목 박스."""
    tree_col, holdings_col = st.columns(GRID_RATIO, vertical_alignment="center")
    with tree_col:
        itemname = item.get("itemname") or itemcode
        detail = itemcode
        if record:
            detail = f"{itemcode} · {record.get('date', '')} 종가 {record.get('value', 0):,.0f}"
        st.markdown(
            f":material/description: {itemname}"
            f" <span style='color:#868e96'>{detail}</span>",
            unsafe_allow_html=True,
        )
    with holdings_col:
        holdings = [str(name) for name in item.get("itemlist") or []]
        if not holdings:
            st.caption("구성 종목 정보가 없습니다.")
            return
        pills_key = f"analy_pills_{itemcode}"
        st.pills(
            "구성 종목",
            holdings,
            selection_mode="single",
            label_visibility="collapsed",
            wrap=True,  # 구성 종목명이 길어도 잘리지 않고 다음 줄로 넘어가게 한다
            key=pills_key,
            on_change=_pick_holding,
            args=(pills_key, itemcode),
            persist_state="session",
        )


def _render_tree(
    tree: dict[str, dict[str, list[str]]],
    etfs: dict[str, dict],
    values: dict[str, dict],
) -> None:
    st.markdown(TREE_CSS, unsafe_allow_html=True)
    header_cols = st.columns(GRID_RATIO)
    header_cols[0].markdown("**종목**")
    header_cols[1].markdown("**구성 종목**")
    st.divider()

    if not tree:
        st.info("조건을 만족하는 종목이 없습니다.")
        return

    opened: set[str] = st.session_state.setdefault(OPEN_KEY, set())
    limits: dict[str, int] = st.session_state.setdefault(LIMIT_KEY, {})
    # 행 사이 간격을 없애 계층 연결선이 끊기지 않게 한다
    with st.container(gap=0):
        for index, row in enumerate(_tree_rows(tree, opened, limits)):
            # key가 st-key-... class로 나가면서 CSS가 행의 연결선을 찾는다
            with st.container(key=f"{_row_prefix(row['flags'])}-{index}", gap=0):
                if row["kind"] == "branch":
                    _render_branch(row, opened)
                elif row["kind"] == "leaf":
                    itemcode = row["itemcode"]
                    _render_leaf(
                        itemcode,
                        etfs.get(itemcode, {"itemcode": itemcode}),
                        values.get(itemcode),
                    )
                else:
                    _render_more(row)


def _render_picked(etfs: dict[str, dict], holder_index: dict[str, list[str]]) -> None:
    """구성 종목 박스를 클릭했을 때 같은 종목을 담은 ETF를 보여준다."""
    picked = st.session_state.get(PICK_KEY)
    if not picked:
        return

    name = picked["name"]
    holders = holder_index.get(name, [])
    st.divider()
    source = etfs.get(picked["itemcode"], {}).get("itemname", picked["itemcode"])
    st.markdown(f"**구성 종목 · {name}이 포함된 ETF 종목** — {source} 에서 선택 / 포함 ETF {len(holders)}개")
    st.dataframe(
        [
            {
                "종목코드": itemcode,
                "종목명": etfs.get(itemcode, {}).get("itemname", ""),
                "섹터": etfs.get(itemcode, {}).get("sector", ""),
                "분류": etfs.get(itemcode, {}).get("sectorlist", ""),
            }
            for itemcode in holders
        ],
        width="stretch",
        hide_index=True,
    )


def show() -> None:
    st.subheader("분석")

    category_signature = _file_signature(CATEGORY_FILE)
    list_signature = _file_signature(ETF_LIST_FILE)
    categories = load_categories(category_signature)
    etfs = load_etf_list(list_signature)
    as_of, values = load_latest_values(_dir_signature(ETF_VALUE_DIR))

    ma_col, rs_col, top52_col, button_col = st.columns(
        [3, 2, 2, 1], vertical_alignment="bottom"
    )
    # 다른 탭에 갔다 돌아와도 고른 조건이 남도록 상태를 세션 단위로 유지한다
    # accept_new_options=False·filter_mode=None 으로 목록 선택만 허용하고 입력·수정은 막는다
    with ma_col:
        ma_label = st.selectbox(
            "이동평균선",
            list(MA_FILTERS),
            key=MA_KEY,
            accept_new_options=False,
            filter_mode=None,
            persist_state="session",
        )
    with rs_col:
        rs_label = st.selectbox(
            "RS지수",
            list(RS_FILTERS),
            key=RS_KEY,
            accept_new_options=False,
            filter_mode=None,
            persist_state="session",
        )
    with top52_col:
        top52_label = st.selectbox(
            "52주 신고가 비율",
            list(TOP52_FILTERS),
            key=TOP52_KEY,
            accept_new_options=False,
            filter_mode=None,
            persist_state="session",
        )
    with button_col:
        st.button(
            "검색",
            type="primary",
            width="stretch",
            on_click=_reset_tree,
            key="analy_search",
        )

    tree = filter_tree(
        categories,
        values,
        MA_FILTERS[ma_label],
        RS_FILTERS[rs_label],
        TOP52_FILTERS[top52_label],
    )
    matched = sum(len(codes) for lists in tree.values() for codes in lists.values())
    total = sum(len(codes) for lists in categories.values() for codes in lists.values())
    as_of_text = as_of or "없음"
    st.caption(
        f"기준일 {as_of_text} · 이동평균선 {ma_label} · RS지수 {rs_label} · "
        f"신고가 비율 {top52_label} → {matched}/{total}종목"
    )

    with st.container(border=True):
        _render_tree(tree, etfs, values)

    _render_picked(etfs, build_holder_index(list_signature))
