"""분석 탭: STOCK_DATA와 STOCKS를 조인해 ETF·개별 종목을 카드별 트리로 보여준다."""

import itertools
import json
import urllib.parse
from collections import defaultdict

import streamlit as st

from tab04_adm import (
    PAGE_SIZE,
    PROJECT_SECRETS,
    _secret,
    call_rest,
    resolve_project,
    rest_url,
)

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

# 라벨 -> 거래량(proc) 다음으로 이어서 비교할 거래량 이동평균 키
PROC_FILTERS: dict[str, tuple[str, ...]] = {
    "전체": (),
    "거래량>proc10": ("proc10",),
    "거래량>proc10>proc20": ("proc10", "proc20"),
    "거래량>proc10>proc20>proc30": ("proc10", "proc20", "proc30"),
    "거래량>proc10>proc20>proc30>proc50": ("proc10", "proc20", "proc30", "proc50"),
}

# 라벨 -> 신고가(top52Value) 대비 허용하는 하락률 상한
# (top52Value - value) / top52Value 이 이 값 이하면 조건 충족
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

# 라벨 -> 시가총액(marketSum, 억원) 하한. None이면 시가총액 조건 없음
MARKET_SUM_FILTERS: dict[str, float | None] = {
    "전체": None,
    "1000억 이상": 1000,
    "5000억 이상": 5000,
    "1조 이상": 10000,
    "1.5조 이상": 15000,
    "2조 이상": 20000,
}

PAGE_ROWS = 30  # 한 분류에 종목이 수백 개라 나눠 그린다

INDENT_PX = 18  # 한 계층을 들여쓰는 폭
GUIDE_PX = 9  # 부모 아이콘 중앙을 지나는 세로 연결선의 x 좌표
GUIDE_COLOR = "#b8bfc6"
MAX_DEPTH = 2  # 섹터(0) → 세부 분류(1) → 종목(2)

# 컨테이너 key → CSS class(st-key-...) 로 연결선을 그린다. 카드마다 달라야 key가 겹치지 않는다
ETF_PREFIX = "analytree"
STOCK_PREFIX = "stocktree"

PICK_KEY = "analy_picked_item"
PROJECT_KEY = "analy_supabase_project"

STOCKS_TABLE = "STOCKS"
STOCK_DATA_TABLE = "STOCK_DATA"
SECTORS_TABLE = "SECTORS"


def fetch_paginated(
    project: str,
    api_key: str,
    table: str,
    select: str,
    filters: dict[str, str] | None = None,
    order: str | None = None,
) -> list[dict]:
    """PostgREST로 테이블을 페이지 단위로 읽어 전부 돌려준다."""
    items: list[dict] = []
    offset = 0
    while True:
        params: dict[str, str] = {
            "select": select,
            "limit": str(PAGE_SIZE),
            "offset": str(offset),
        }
        if order:
            params["order"] = order
        if filters:
            params.update(filters)
        raw = call_rest(
            f"{rest_url(project, table)}?{urllib.parse.urlencode(params)}",
            api_key,
            "GET",
            "return=representation",
            None,
        )
        chunk = json.loads(raw) if raw.strip() else []
        if not isinstance(chunk, list):
            raise RuntimeError(f"{table} 응답이 리스트가 아닙니다")
        items.extend(chunk)
        if len(chunk) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return items


def resolve_supabase() -> tuple[str | None, str | None, str | None]:
    """(project, api_key, error)를 돌려준다. 프로젝트 주소는 세션에 기억한다."""
    api_key = _secret("SUPABASE_KEY")
    if not api_key:
        return None, None, "secrets에 SUPABASE_KEY가 없습니다."
    found = [name for name in PROJECT_SECRETS if _secret(name)]
    if not found:
        return None, None, f"secrets에 {' 또는 '.join(PROJECT_SECRETS)}가 없습니다."

    project = st.session_state.get(PROJECT_KEY, "")
    if project:
        return project, api_key, None

    name, project, _attempts = resolve_project(api_key)
    if not project:
        return None, None, "접속되는 프로젝트 주소가 없습니다."
    st.session_state[PROJECT_KEY] = project
    st.session_state["analy_supabase_secret"] = name
    return project, api_key, None


def load_bundle(
    project: str,
    api_key: str,
    item_filter: str,
    default_item: str,
) -> tuple[str | None, dict, dict, dict, bool]:
    """STOCK_DATA와 STOCKS를 stockCode로 조인해 분석용 묶음을 만든다.

    item_filter는 stockItem에 걸 PostgREST 조건(예: eq.ETF, neq.ETF)이다.

    반환: (기준일, categories, items, values, has_top52)
    - categories: sectorName -> sectorItem -> stockCode[]
    - items: stockCode -> 종목 정보(STOCKS + 섹터명)
    - values: stockCode -> 해당 기준일 STOCK_DATA 행
    """
    stocks = fetch_paginated(
        project,
        api_key,
        STOCKS_TABLE,
        "stockCode,stockName,stockItem,stockItems,sectorCode,sectorItem",
        filters={"stockItem": item_filter},
        order="stockCode",
    )
    stock_by_code = {
        str(row["stockCode"]): row
        for row in stocks
        if isinstance(row, dict) and row.get("stockCode")
    }
    if not stock_by_code:
        return None, {}, {}, {}, False

    sectors = fetch_paginated(
        project,
        api_key,
        SECTORS_TABLE,
        "sectorCode,sectorName",
        order="sectorCode",
    )
    sector_name = {
        str(row["sectorCode"]): row.get("sectorName") or row["sectorCode"]
        for row in sectors
        if isinstance(row, dict) and row.get("sectorCode")
    }

    latest_raw = call_rest(
        f"{rest_url(project, STOCK_DATA_TABLE)}?"
        + urllib.parse.urlencode(
            {
                "select": "date",
                "stockItem": item_filter,
                "order": "date.desc",
                "limit": "1",
            }
        ),
        api_key,
        "GET",
        "return=representation",
        None,
    )
    latest = json.loads(latest_raw) if latest_raw.strip() else []
    if not isinstance(latest, list) or not latest:
        return None, {}, {}, {}, False
    as_of = str(latest[0]["date"])

    data_rows = fetch_paginated(
        project,
        api_key,
        STOCK_DATA_TABLE,
        "stockCode,stockItem,date,sectorCode,sectorItem,value,proc,kospi,"
        "rs20,rs50,ma10,ma20,ma30,ma50,ma100,ma150,"
        "proc10,proc20,proc30,proc50,top52Value,marketSum",
        filters={"stockItem": item_filter, "date": f"eq.{as_of}"},
        order="stockCode",
    )

    values: dict[str, dict] = {}
    items: dict[str, dict] = {}
    categories: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    has_top52 = False

    for row in data_rows:
        if not isinstance(row, dict):
            continue
        stock_code = str(row.get("stockCode") or "").strip()
        stock = stock_by_code.get(stock_code)
        if not stock:
            continue  # STOCKS에 없는 시세 행은 조인 대상이 아니다

        if row.get("top52Value") not in (None, "", 0, 0.0):
            has_top52 = True

        sector_code = stock.get("sectorCode") or row.get("sectorCode") or "etc"
        sector_item = stock.get("sectorItem") or row.get("sectorItem") or "기타"
        sector = sector_name.get(str(sector_code), str(sector_code))
        holdings = stock.get("stockItems") or []
        if not isinstance(holdings, list):
            holdings = []

        items[stock_code] = {
            "stockCode": stock_code,
            "stockName": stock.get("stockName") or stock_code,
            "stockItem": stock.get("stockItem") or default_item,
            "stockItems": [str(name) for name in holdings],
            "sectorCode": sector_code,
            "sectorItem": sector_item,
            "sector": sector,
            "sectorlist": sector_item,
            # 기존 트리·선택 UI 호환 키
            "itemcode": stock_code,
            "itemname": stock.get("stockName") or stock_code,
            "itemlist": [str(name) for name in holdings],
        }
        values[stock_code] = row
        categories[sector][str(sector_item)].append(stock_code)

    return (
        as_of,
        {sector: dict(sectorlists) for sector, sectorlists in categories.items()},
        items,
        values,
        has_top52,
    )


@st.cache_data(show_spinner="ETF 시세를 불러오는 중입니다…", ttl=300)
def load_etf_bundle(project: str, api_key: str) -> tuple[str | None, dict, dict, dict, bool]:
    """STOCK_DATA(ETF) ⨝ STOCKS 묶음."""
    return load_bundle(project, api_key, "eq.ETF", "ETF")


@st.cache_data(show_spinner="개별 종목 시세를 불러오는 중입니다…", ttl=300)
def load_stock_bundle(project: str, api_key: str) -> tuple[str | None, dict, dict, dict, bool]:
    """STOCK_DATA(ETF 제외: KS·KQ) ⨝ STOCKS 묶음."""
    return load_bundle(project, api_key, "neq.ETF", "KS")


# 카드(보드)마다 다른 CSS 접두어·세션 키·그리드 구성을 한곳에 모아 둔다
ETF_BOARD: dict = {
    "kind": "etf",
    "title": "ETF 기준",
    "prefix": ETF_PREFIX,
    "widget": "analy",
    "loader": load_etf_bundle,
    "source": "STOCK_DATA(ETF) ⨝ STOCKS",
    "ratio": (3, 1, 1, 1, 4),  # 종목 : 시가총액 : 종가 : 52주 신고가 : 구성 종목
    "headers": ("종목", "시가총액", "종가", "52주 신고가", "구성 종목"),
    "ma_key": "analy_ma",
    "proc_key": "analy_proc",
    "top52_key": "analy_top52",
    "market_key": "analy_market_sum",
    "open_key": "analy_open_nodes",
    "limit_key": "analy_page_limits",
}

STOCK_BOARD: dict = {
    "kind": "stock",
    "title": "개별 종목 기준",
    "prefix": STOCK_PREFIX,
    "widget": "analystk",
    "loader": load_stock_bundle,
    "source": "STOCK_DATA(KS·KQ) ⨝ STOCKS",
    "ratio": (3, 1, 1, 1, 4),  # 종목 : 시가총액 : 종가 : 52주 신고가 : 거래량·RS
    "headers": ("종목", "시가총액", "종가", "52주 신고가", "거래량 · RS"),
    "ma_key": "analystk_ma",
    "proc_key": "analystk_proc",
    "top52_key": "analystk_top52",
    "market_key": "analystk_market_sum",
    "open_key": "analystk_open_nodes",
    "limit_key": "analystk_page_limits",
}

BOARDS = (ETF_BOARD, STOCK_BOARD)


def build_holder_index(etfs: dict[str, dict]) -> dict[str, list[str]]:
    """구성 종목명 -> 그 종목을 담고 있는 ETF stockCode[]."""
    index: dict[str, list[str]] = {}
    for stock_code, row in etfs.items():
        for name in row.get("stockItems") or row.get("itemlist") or []:
            index.setdefault(str(name), []).append(stock_code)
    return index


def passes_filters(
    record: dict | None,
    ma_keys: tuple[str, ...],
    proc_keys: tuple[str, ...],
    top52_ratio: float | None,
    market_sum_min: float | None,
) -> bool:
    """최근 지표 레코드가 콤보박스 조건을 모두 만족하는지 본다."""
    if record is None:
        return False

    value = record.get("value") or 0.0
    if value <= 0:
        return False

    # marketSum 단위는 억원. '전체'면 시가총액 조건을 건너뛴다
    if market_sum_min is not None:
        market_sum = record.get("marketSum") or 0.0
        if market_sum < market_sum_min:
            return False

    if ma_keys:
        averages = [record.get(key) or 0.0 for key in ma_keys]
        # 상장 기간이 짧아 이동평균이 채워지지 않은 종목(0)은 비교 대상에서 뺀다
        if any(average <= 0 for average in averages):
            return False
        chain = [value, *averages]
        if any(upper <= lower for upper, lower in zip(chain, chain[1:])):
            return False

    if proc_keys:
        proc = record.get("proc") or 0.0
        if proc <= 0:
            return False
        averages = [record.get(key) or 0.0 for key in proc_keys]
        # 거래량 이동평균이 없는 종목(0)은 비교 대상에서 뺀다
        if any(average <= 0 for average in averages):
            return False
        chain = [proc, *averages]
        if any(upper <= lower for upper, lower in zip(chain, chain[1:])):
            return False

    if top52_ratio is not None:
        top52 = record.get("top52Value") or 0.0
        if top52 <= 0:
            return False
        # 신고가 대비 하락률: (top52Value - value) / top52Value 이 선택한 % 이내인지 본다
        if (top52 - value) / top52 > top52_ratio:
            return False

    return True


def filter_tree(
    categories: dict[str, dict[str, list[str]]],
    values: dict[str, dict],
    ma_keys: tuple[str, ...],
    proc_keys: tuple[str, ...],
    top52_ratio: float | None,
    market_sum_min: float | None,
) -> dict[str, dict[str, list[str]]]:
    """조건을 만족하는 종목만 남긴 분류 트리. 종목이 없는 분류는 뺀다."""
    tree: dict[str, dict[str, list[str]]] = {}
    for sector, sectorlists in categories.items():
        kept_lists: dict[str, list[str]] = {}
        for sectorlist, itemcodes in sectorlists.items():
            kept = [
                itemcode
                for itemcode in itemcodes
                if passes_filters(
                    values.get(itemcode),
                    ma_keys,
                    proc_keys,
                    top52_ratio,
                    market_sum_min,
                )
            ]
            if kept:
                kept_lists[sectorlist] = kept
        if kept_lists:
            tree[sector] = kept_lists
    return tree


def _toggle_node(open_key: str, node: str) -> None:
    opened: set[str] = st.session_state.setdefault(open_key, set())
    opened.symmetric_difference_update({node})


def _grow_page(limit_key: str, node: str) -> None:
    limits: dict[str, int] = st.session_state.setdefault(limit_key, {})
    limits[node] = limits.get(node, PAGE_ROWS) + PAGE_ROWS


def _reset_board(board: dict) -> None:
    """검색 버튼을 누르면 최신 지표를 다시 읽고 펼친 상태·선택을 처음으로 돌린다."""
    board["loader"].clear()
    st.session_state[board["open_key"]] = set()
    st.session_state[board["limit_key"]] = {}
    if board["kind"] == "etf":
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


def _row_prefix(prefix: str, flags: tuple[bool, ...]) -> str:
    """계층마다 '마지막 형제인가'를 CSS class에 쓸 문자열로 만든다."""
    if not flags:
        return f"{prefix}-root"
    return f"{prefix}-" + "".join("1" if flag else "0" for flag in flags)


def _guide_css(prefix: str) -> str:
    """깊이·형제 위치 조합마다 세로선과 꺾임선을 배경으로 그리는 CSS."""
    rules = [
        # 트리 헤더(종목 / 구성 종목): 글자 높이에 맞게 세로 여백 축소
        f'div[class*="st-key-{prefix}-header"] {{'
        " padding: 0.15rem 0 0.2rem 0 !important; margin: 0 0 0.25rem 0 !important;"
        " gap: 0 !important; border-bottom: 1px solid rgba(49, 51, 63, 0.2); }",
        f'div[class*="st-key-{prefix}-header"]'
        " [data-testid='stHorizontalBlock'] {"
        " gap: 0.5rem !important; align-items: center !important;"
        " min-height: 0 !important; }",
        f'div[class*="st-key-{prefix}-header"]'
        " [data-testid='stColumn'] {"
        " padding-top: 0 !important; padding-bottom: 0 !important;"
        " min-height: 0 !important; }",
        f'div[class*="st-key-{prefix}-header"]'
        " [data-testid='stMarkdownContainer'],"
        f' div[class*="st-key-{prefix}-header"]'
        " [data-testid='stMarkdownContainer'] p {"
        " margin: 0 !important; padding: 0 !important;"
        " line-height: 1.2 !important; }",
        # TREE_CSS 주입용 markdown이 빈 세로 공간을 차지하지 않게 함
        '[data-testid="stMarkdownContainer"]:has(> style):not(:has(> :not(style))) {'
        " display: none !important; height: 0 !important;"
        " margin: 0 !important; padding: 0 !important; }",
        f'div[class*="st-key-{prefix}-"] button {{'
        " padding-top: 0; padding-bottom: 0; min-height: 1.5rem;"
        " width: auto !important; max-width: 100%; }",
        f'div[class*="st-key-{prefix}-"] button,'
        f' div[class*="st-key-{prefix}-"] button *'
        " { justify-content: flex-start !important; text-align: left !important; }",
        f'div[class*="st-key-{prefix}-"]'
        " [data-testid='stMarkdownContainer'],"
        f' div[class*="st-key-{prefix}-"]'
        " [data-testid='stMarkdownContainer'] p,"
        f' div[class*="st-key-{prefix}-"]'
        " [data-testid='stMarkdownContainer'] div"
        " { text-align: left !important; justify-content: flex-start !important;"
        " margin: 0; }",
        f'div[class*="st-key-{prefix}-"] p {{ margin-bottom: 0; }}',
        f'div[class*="st-key-{prefix}-"] [data-testid="stIconMaterial"]'
        " { font-size: 15px !important; }",
    ]
    for depth in range(1, MAX_DEPTH + 1):
        for flags in itertools.product((False, True), repeat=depth):
            layers: list[tuple[str, str]] = []
            for level, ancestor_is_last in enumerate(flags[:-1]):
                if not ancestor_is_last:
                    layers.append((f"{GUIDE_PX + INDENT_PX * level}px 0", "1px 100%"))
            own_x = GUIDE_PX + INDENT_PX * (depth - 1)
            layers.append((f"{own_x}px 0", "1px 50%" if flags[-1] else "1px 100%"))
            layers.append((f"{own_x}px 50%", f"{INDENT_PX - GUIDE_PX}px 1px"))

            selector = f'div[class*="st-key-{_row_prefix(prefix, flags)}-"]'
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


TREE_CSS = {board["prefix"]: _guide_css(board["prefix"]) for board in BOARDS}


def _tree_rows(
    tree: dict[str, dict[str, list[str]]],
    opened: set[str],
    limits: dict[str, int],
) -> list[dict]:
    """펼친 노드만 따라가며 그릴 행을 계층 순서대로 늘어놓는다."""
    rows: list[dict] = []
    for sector, sectorlists in tree.items():
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

            shown = itemcodes[: limits.get(node, PAGE_ROWS)]
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


def _fmt_market_sum(value: float | int | None) -> str:
    """시가총액(억원)을 화면용 문자열로 만든다."""
    if value in (None, "", 0, 0.0):
        return "-"
    amount = float(value)
    if amount >= 10000:
        return f"{amount / 10000:,.1f}조"
    return f"{amount:,.0f}억"


def _fmt_price(value: float | int | None) -> str:
    """종가·신고가 숫자를 화면용 문자열로 만든다."""
    if value in (None, "", 0, 0.0):
        return "-"
    return f"{float(value):,.0f}"


def _fmt_volume(value: float | int | None) -> str:
    """거래량을 화면용 문자열로 만든다."""
    if value in (None, "", 0, 0.0):
        return "-"
    return f"{float(value):,.0f}주"


def _fmt_rs(value: float | int | None) -> str:
    """RS(시장 대비 상대강도, 100=시장과 동일)를 화면용 문자열로 만든다."""
    if value in (None, "", 0, 0.0):
        return "-"
    return f"{float(value):,.1f}"


def _render_branch(board: dict, row: dict, opened: set[str]) -> None:
    """섹터·세부 분류 행: 접고 펴는 네모 아이콘과 이름을 그린다."""
    tree_col, *_ = st.columns(board["ratio"], vertical_alignment="center")
    with tree_col:
        is_open = row["node"] in opened
        st.button(
            f"{row['label']} ({row['count']})",
            icon=(
                ":material/indeterminate_check_box:"
                if is_open
                else ":material/add_box:"
            ),
            key=f"{board['widget']}_node_{row['node']}",
            on_click=_toggle_node,
            args=(board["open_key"], row["node"]),
            type="tertiary",
            width="content",
        )


def _render_more(board: dict, row: dict) -> None:
    """한 분류에서 아직 못 그린 종목을 더 불러오는 행."""
    tree_col, *_ = st.columns(board["ratio"], vertical_alignment="center")
    with tree_col:
        st.button(
            f"남은 {row['remaining']}종목 중 {min(row['remaining'], PAGE_ROWS)}개 더 보기",
            icon=":material/more_horiz:",
            key=f"{board['widget']}_more_{row['node']}",
            on_click=_grow_page,
            args=(board["limit_key"], row["node"]),
            type="tertiary",
            width="content",
        )


def _render_leaf(board: dict, itemcode: str, item: dict, record: dict | None) -> None:
    """한 종목: 종목·시가총액·종가·신고가와 카드별 마지막 칸을 한 행에 그린다."""
    tree_col, market_col, value_col, top52_col, last_col = st.columns(
        board["ratio"], vertical_alignment="center"
    )
    with tree_col:
        itemname = item.get("stockName") or item.get("itemname") or itemcode
        st.markdown(
            f":material/description: {itemname}<br>"
            f"<span style='color:#868e96;margin-left:1.4em'>{itemcode}</span>",
            unsafe_allow_html=True,
        )
    with market_col:
        st.markdown(_fmt_market_sum(record.get("marketSum") if record else None))
    with value_col:
        st.markdown(_fmt_price(record.get("value") if record else None))
    with top52_col:
        st.markdown(_fmt_price(record.get("top52Value") if record else None))
    with last_col:
        if board["kind"] == "etf":
            _render_holdings(itemcode, item)
        else:
            _render_metrics(record)


def _render_holdings(itemcode: str, item: dict) -> None:
    """ETF 행의 마지막 칸: 구성 종목을 고를 수 있는 박스."""
    holdings = [
        str(name) for name in (item.get("stockItems") or item.get("itemlist") or [])
    ]
    if not holdings:
        st.caption("구성 종목 정보가 없습니다.")
        return
    pills_key = f"analy_pills_{itemcode}"
    st.pills(
        "구성 종목",
        holdings,
        selection_mode="single",
        label_visibility="collapsed",
        wrap=True,
        key=pills_key,
        on_change=_pick_holding,
        args=(pills_key, itemcode),
        persist_state="session",
    )


def _render_metrics(record: dict | None) -> None:
    """개별 종목 행의 마지막 칸: 거래량과 RS20·RS50."""
    st.markdown(
        f"{_fmt_volume(record.get('proc') if record else None)}<br>"
        "<span style='color:#868e96'>"
        f"RS20 {_fmt_rs(record.get('rs20') if record else None)} · "
        f"RS50 {_fmt_rs(record.get('rs50') if record else None)}</span>",
        unsafe_allow_html=True,
    )


def _render_tree(
    board: dict,
    tree: dict[str, dict[str, list[str]]],
    items: dict[str, dict],
    values: dict[str, dict],
    matched: int,
) -> None:
    prefix = board["prefix"]
    st.markdown(TREE_CSS[prefix], unsafe_allow_html=True)
    with st.container(key=f"{prefix}-header", gap=0):
        header_cols = st.columns(board["ratio"], gap="small")
        for col, title in zip(header_cols, board["headers"]):
            if title == "종목":
                col.markdown(f"**{title} ({matched})**")
            else:
                col.markdown(f"**{title}**")

    if not tree:
        st.info("조건을 만족하는 종목이 없습니다.")
        return

    opened: set[str] = st.session_state.setdefault(board["open_key"], set())
    limits: dict[str, int] = st.session_state.setdefault(board["limit_key"], {})
    with st.container(gap=0):
        for index, row in enumerate(_tree_rows(tree, opened, limits)):
            with st.container(key=f"{_row_prefix(prefix, row['flags'])}-{index}", gap=0):
                if row["kind"] == "branch":
                    _render_branch(board, row, opened)
                elif row["kind"] == "leaf":
                    itemcode = row["itemcode"]
                    _render_leaf(
                        board,
                        itemcode,
                        items.get(itemcode, {"stockCode": itemcode, "itemcode": itemcode}),
                        values.get(itemcode),
                    )
                else:
                    _render_more(board, row)


def _render_picked(etfs: dict[str, dict], holder_index: dict[str, list[str]]) -> None:
    """구성 종목 박스를 클릭했을 때 같은 종목을 담은 ETF를 보여준다."""
    picked = st.session_state.get(PICK_KEY)
    if not picked:
        return

    name = picked["name"]
    holders = holder_index.get(name, [])
    st.divider()
    source = etfs.get(picked["itemcode"], {}).get("stockName") or etfs.get(
        picked["itemcode"], {}
    ).get("itemname", picked["itemcode"])
    st.markdown(
        f"**[구성 종목] '{name}' 포함된 ETF 종목** — {source} 에서 선택 / 포함 ETF {len(holders)}개"
    )
    st.dataframe(
        [
            {
                "종목코드": itemcode,
                "종목명": etfs.get(itemcode, {}).get("stockName")
                or etfs.get(itemcode, {}).get("itemname", ""),
                "섹터": etfs.get(itemcode, {}).get("sector", ""),
                "분류": etfs.get(itemcode, {}).get("sectorItem")
                or etfs.get(itemcode, {}).get("sectorlist", ""),
            }
            for itemcode in holders
        ],
        width="stretch",
        hide_index=True,
    )


def _render_filters(board: dict) -> dict[str, str]:
    """이동평균선·거래량·시가총액·신고가 콤보박스와 검색 버튼. 고른 라벨을 돌려준다."""
    ma_col, proc_col, market_col, top52_col, button_col = st.columns(
        [3, 3, 2, 2, 1], vertical_alignment="bottom"
    )
    with ma_col:
        ma_label = st.selectbox(
            "이동평균선",
            list(MA_FILTERS),
            key=board["ma_key"],
            accept_new_options=False,
            filter_mode=None,
            persist_state="session",
        )
    with proc_col:
        proc_label = st.selectbox(
            "거래량 이동평균",
            list(PROC_FILTERS),
            key=board["proc_key"],
            accept_new_options=False,
            filter_mode=None,
            persist_state="session",
        )
    with market_col:
        market_label = st.selectbox(
            "시가총액",
            list(MARKET_SUM_FILTERS),
            key=board["market_key"],
            accept_new_options=False,
            filter_mode=None,
            persist_state="session",
        )
    with top52_col:
        top52_label = st.selectbox(
            "52주 신고가 비율",
            list(TOP52_FILTERS),
            key=board["top52_key"],
            accept_new_options=False,
            filter_mode=None,
            persist_state="session",
        )
    with button_col:
        st.button(
            "검색",
            type="primary",
            width="stretch",
            on_click=_reset_board,
            args=(board,),
            key=f"{board['widget']}_search",
        )
    return {
        "ma": ma_label,
        "proc": proc_label,
        "market": market_label,
        "top52": top52_label,
    }


def _render_board(board: dict, project: str, api_key: str) -> None:
    """카드 한 장: 콤보박스 → 요약 캡션 → 트리 그리드(→ ETF는 포함 ETF 리스트)."""
    st.markdown(f"**{board['title']}**")

    try:
        as_of, categories, items, values, has_top52 = board["loader"](project, api_key)
    except Exception as load_error:  # 연결·권한 오류를 화면에 그대로 보여준다
        st.error(f"{board['title']} 데이터 조회 실패 — {load_error}")
        return

    labels = _render_filters(board)

    if not has_top52 and TOP52_FILTERS[labels["top52"]] is not None:
        st.warning(
            "STOCK_DATA에 top52Value 값이 없어 신고가 비율 필터는 '전체'만 유효합니다."
        )

    tree = filter_tree(
        categories,
        values,
        MA_FILTERS[labels["ma"]],
        PROC_FILTERS[labels["proc"]],
        TOP52_FILTERS[labels["top52"]],
        MARKET_SUM_FILTERS[labels["market"]],
    )
    matched = sum(len(codes) for lists in tree.values() for codes in lists.values())
    total = sum(len(codes) for lists in categories.values() for codes in lists.values())
    as_of_text = as_of or "없음"
    secret_name = st.session_state.get("analy_supabase_secret", "")
    st.caption(
        f"기준일 {as_of_text} · {board['source']} · "
        f"{secret_name or 'Supabase'} · 이동평균선 {labels['ma']} · "
        f"거래량 이동평균 {labels['proc']} · 신고가 비율 {labels['top52']} · "
        f"시가총액 {labels['market']} → {matched}/{total}종목"
    )

    with st.container(border=True, gap=0):
        _render_tree(board, tree, items, values, matched)

    if board["kind"] == "etf":
        _render_picked(items, build_holder_index(items))


def show() -> None:
    st.subheader("분석")

    project, api_key, error = resolve_supabase()
    if error or not project or not api_key:
        st.error(error or "Supabase 연결 정보를 확인하세요.")
        st.caption("관리 탭에서 기준 데이터·시세 데이터를 먼저 적재해야 합니다.")
        return

    for board in BOARDS:
        with st.container(border=True):
            _render_board(board, project, api_key)
