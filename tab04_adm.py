"""관리 탭: 기준 데이터(SECTORS·STOCKS)와 시세 지표(STOCK_DATA)를 Supabase에 적재한다."""

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

import streamlit as st

BASE_DIR = Path(__file__).resolve().parent
SECTOR_FILE = BASE_DIR / "data" / "stock" / "010_SectorList.json"
STOCK_FILE = BASE_DIR / "data" / "stock" / "020_StockList.json"

CHUNK_SIZE = 500  # PostgREST 한 번에 보낼 행 수
TIMEOUT = 60
MAX_RETRY = 3

# 프로젝트 주소로 쓸 값. 앞에서부터 실제로 접속되는 것을 골라 쓴다.
PROJECT_SECRETS = ("SUPABASE_PROJECT", "SUPABASE_ID")

# 리스트 컬럼은 빈 배열로, 나머지는 빈 문자열로 채운다.
# PostgREST는 한 번에 보내는 행들의 key가 모두 같아야 하므로 없는 값도 채워 보낸다.
LIST_COLUMNS = ("sectorItems", "stockItems")

TARGETS = (
    {
        "table": "SECTORS",
        "file": SECTOR_FILE,
        "key": "sectorCode",
        "columns": ("sectorCode", "sectorName", "sectorItems"),
    },
    {
        "table": "STOCKS",
        "file": STOCK_FILE,
        "key": "stockCode",
        "columns": ("stockCode", "stockName", "stockItem", "stockItems", "sectorCode", "sectorItem"),
    },
)

STOCKS_TABLE = "STOCKS"
STOCK_DATA_TABLE = "STOCK_DATA"
STOCK_DATA_CONFLICT = "stockCode,date"
STOCKS_SELECT = "stockCode,stockName,stockItem,sectorCode,sectorItem"
STOCK_DATA_COLUMNS = (
    "stockCode",
    "stockItem",
    "date",
    "sectorCode",
    "sectorItem",
    "value",
    "proc",
    "proc10",
    "proc20",
    "proc30",
    "proc50",
    "kospi",
    "rs20",
    "rs50",
    "ma10",
    "ma20",
    "ma30",
    "ma50",
    "ma100",
    "ma150",
    "top52Value",
    "marketSum",
)
PAGE_SIZE = 1000  # PostgREST 기본 상한에 맞춰 STOCKS를 나눠 읽는다

# 적재 방식 — 콤보박스에서만 고르고 직접 입력은 막는다
INSERT_MODES = (
    "덮어쓰기(upsert)",
    "그대로 INSERT",
    "기존 데이터 삭제 후 적재",
)

SISE_URL = (
    "https://api.finance.naver.com/siseJson.naver"
    "?symbol={symbol}&requestType=1&startTime={start}&endTime={end}&timeframe=day"
)
STOCK_INTEGRATION_URL = "https://m.stock.naver.com/api/stock/{code}/integration"
ETF_ANALYSIS_URL = "https://m.stock.naver.com/api/stock/{code}/etfAnalysis"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
}

KOSPI_SYMBOL = "KOSPI"
LOOKBACK_DAYS = 900  # 기준일 + ma150·rs50 계산에 필요한 여유 기간
MAX_TRADING_DAYS = 250
MA_WINDOWS = (10, 20, 30, 50, 100, 150)
RS_WINDOWS = (20, 50)
PROC_WINDOWS = (10, 20, 30, 50)
SLEEP_SEC = 1
SLEEP_EVERY = 50

ROW_PATTERN = re.compile(r'\["(\d{8})",([^\]]*)\]')
MARKET_JO = re.compile(r"([\d.]+)\s*조")
MARKET_EOK = re.compile(r"([\d.]+)\s*억")


def fetch_json_api(url: str) -> dict | list:
    """네이버 JSON API를 호출한다. 실패 시 지수 백오프로 재시도한다."""
    last_error: Exception | None = None
    for attempt in range(MAX_RETRY):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
            time.sleep(2**attempt)
    raise RuntimeError(f"요청 실패: {url}") from last_error


def parse_market_sum_text(raw: str | None) -> float:
    """'1,452조 8,002억' 형태를 억원 단위 숫자로 바꾼다."""
    text = str(raw or "").replace(",", "")
    total = 0.0
    jo = MARKET_JO.search(text)
    if jo:
        total += float(jo.group(1)) * 10_000  # 1조 = 10,000억
    eok = MARKET_EOK.search(text)
    if eok:
        total += float(eok.group(1))
    return round(total, 1)


def to_market_sum_eok(raw_value) -> float:
    """원 단위 시가총액을 억원 단위로 바꾼다."""
    try:
        return round(float(str(raw_value).replace(",", "")) / 100_000_000, 1)
    except (TypeError, ValueError):
        return 0.0


def fetch_market_sum(stock_code: str, stock_item: str) -> float:
    """종목의 현재 시가총액(억원)을 가져온다."""
    if stock_item == "ETF":
        try:
            payload = fetch_json_api(ETF_ANALYSIS_URL.format(code=stock_code))
            if isinstance(payload, dict) and payload.get("marketValueRaw") not in (None, ""):
                return to_market_sum_eok(payload["marketValueRaw"])
        except RuntimeError:
            pass

    try:
        payload = fetch_json_api(STOCK_INTEGRATION_URL.format(code=stock_code))
    except RuntimeError:
        return 0.0
    if not isinstance(payload, dict):
        return 0.0
    for info in payload.get("totalInfos") or []:
        if isinstance(info, dict) and info.get("code") == "marketValue":
            return parse_market_sum_text(info.get("value"))
    return 0.0


def parse_insert_mode(label: str) -> tuple[bool, bool]:
    """적재 방식 라벨을 (upsert, clear)로 바꾼다."""
    if label == "기존 데이터 삭제 후 적재":
        return False, True
    if label == "덮어쓰기(upsert)":
        return True, False
    return False, False


def _secret(name: str) -> str:
    """설정값을 st.secrets에서 읽고, 없으면 환경변수로 대체한다."""
    try:
        value = st.secrets.get(name, "")
    except Exception:  # secrets.toml이 없으면 환경변수만 사용한다
        value = ""
    return str(value or os.environ.get(name, "")).strip()


def rest_url(project: str, table: str) -> str:
    """프로젝트 값을 REST 엔드포인트로 바꾼다. 프로젝트 ref와 전체 URL을 모두 받는다."""
    project = project.strip().rstrip("/")
    base = project if project.startswith("http") else f"https://{project}.supabase.co"
    return f"{base}/rest/v1/{urllib.parse.quote(table)}"


def call_rest(
    url: str,
    api_key: str,
    method: str,
    prefer: str,
    payload: list | None,
    retries: int = MAX_RETRY,
    extra_headers: dict[str, str] | None = None,
) -> str:
    """Supabase REST를 호출하고 응답 본문을 돌려준다. 실패 시 지수 백오프로 재시도한다."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Prefer": prefer,
    }
    if extra_headers:
        headers.update(extra_headers)
    last_error: Exception | None = None
    for attempt in range(retries):
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:  # 원인이 본문에 담겨 오므로 그대로 올린다
            detail = error.read().decode("utf-8", "replace").strip()
            raise RuntimeError(f"HTTP {error.code} {detail}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = error
            if attempt < retries - 1:
                time.sleep(2**attempt)
    raise RuntimeError(f"요청 실패: {url} — {last_error}")


def fetch_stocks(project: str, api_key: str) -> list[dict]:
    """STOCKS 테이블에서 시세 적재에 필요한 종목 목록을 페이지 단위로 가져온다."""
    items: list[dict] = []
    offset = 0
    while True:
        query = urllib.parse.urlencode(
            {
                "select": STOCKS_SELECT,
                "order": "stockCode",
                "limit": PAGE_SIZE,
                "offset": offset,
            }
        )
        raw = call_rest(
            f"{rest_url(project, STOCKS_TABLE)}?{query}",
            api_key,
            "GET",
            "return=representation",
            None,
        )
        chunk = json.loads(raw) if raw.strip() else []
        if not isinstance(chunk, list):
            raise RuntimeError(f"{STOCKS_TABLE} 응답이 리스트가 아닙니다")
        items.extend(chunk)
        if len(chunk) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return items


def resolve_project(api_key: str) -> tuple[str, str, list[dict]]:
    """SUPABASE_PROJECT·SUPABASE_ID를 차례로 시도해 접속되는 값을 고른다.

    테이블 유무와 무관하게 판단하려고 REST 루트(/rest/v1/)로만 확인한다.
    (secrets 이름, 프로젝트 값, 시도 기록)을 돌려주고 모두 실패하면 이름·값이 빈 문자열이다.
    """
    attempts: list[dict] = []
    for name in PROJECT_SECRETS:
        value = _secret(name)
        if not value:
            attempts.append({"secrets": name, "값": "", "결과": "secrets에 없음"})
            continue
        try:
            call_rest(rest_url(value, ""), api_key, "GET", "return=minimal", None, retries=1)
        except RuntimeError as error:
            attempts.append({"secrets": name, "값": value, "결과": f"실패 — {error}"})
        else:
            attempts.append({"secrets": name, "값": value, "결과": "접속 성공"})
            return name, value, attempts
    return "", "", attempts


def normalize(rows: list[dict], columns: tuple[str, ...]) -> list[dict]:
    """테이블 컬럼만 남기고 누락된 값은 기본값으로 채운다."""
    return [
        {column: row.get(column, [] if column in LIST_COLUMNS else "") for column in columns}
        for row in rows
    ]


def load_rows(target: dict) -> list[dict]:
    """기준 데이터 JSON을 읽어 적재할 행 목록으로 만든다."""
    loaded = json.loads(target["file"].read_text(encoding="utf-8"))
    if not isinstance(loaded, list):
        raise RuntimeError(f"{target['file'].name}: 최상위가 리스트가 아닙니다")
    return normalize(loaded, target["columns"])


def delete_all(url: str, api_key: str, key_column: str) -> None:
    """테이블을 비운다. PostgREST는 조건 없는 삭제를 막으므로 키 컬럼 조건을 붙인다."""
    condition = urllib.parse.urlencode({key_column: "not.is.null"})
    call_rest(f"{url}?{condition}", api_key, "DELETE", "return=minimal", None)


def insert_rows(
    url: str,
    api_key: str,
    rows: list[dict],
    conflict: str,
    upsert: bool,
    progress=None,
) -> int:
    """행을 CHUNK_SIZE 단위로 나눠 적재하고 적재된 행 수를 돌려준다."""
    prefer = "return=minimal"
    endpoint = url
    if upsert:  # 같은 키가 이미 있으면 갱신한다
        prefer += ",resolution=merge-duplicates"
        endpoint = f"{url}?{urllib.parse.urlencode({'on_conflict': conflict})}"

    sent = 0
    for start in range(0, len(rows), CHUNK_SIZE):
        chunk = rows[start : start + CHUNK_SIZE]
        call_rest(endpoint, api_key, "POST", prefer, chunk)
        sent += len(chunk)
        if progress is not None:
            progress(sent, len(rows))
    return sent


def insert_base_data(project: str, api_key: str, upsert: bool, clear: bool) -> list[dict]:
    """SECTORS·STOCKS 두 테이블에 기준 데이터를 적재하고 테이블별 결과를 돌려준다."""
    results = []
    for target in TARGETS:
        table = target["table"]
        result = {"테이블": table, "파일": target["file"].name, "행": 0, "결과": ""}
        started = time.time()
        progress_bar = st.progress(0.0, text=f"{table} 준비 중…")
        try:
            rows = load_rows(target)
            url = rest_url(project, table)
            if clear:
                progress_bar.progress(0.0, text=f"{table} 기존 데이터 삭제 중…")
                delete_all(url, api_key, target["key"])

            def on_progress(sent: int, total: int, table=table) -> None:
                progress_bar.progress(sent / max(1, total), text=f"{table} {sent:,}/{total:,}행")

            sent = insert_rows(url, api_key, rows, target["key"], upsert, on_progress)
        except (RuntimeError, OSError, json.JSONDecodeError) as error:
            progress_bar.empty()
            result["결과"] = f"실패 — {error}"
            result["ok"] = False
        else:
            progress_bar.progress(1.0, text=f"{table} 완료")
            result["행"] = sent
            result["결과"] = f"완료 ({round(time.time() - started, 1)}초)"
            result["ok"] = True
        results.append(result)
    return results


def fetch_daily(symbol: str) -> list[tuple[str, float, int]]:
    """네이버 일봉을 (날짜, 종가, 거래량) 오름차순 목록으로 반환한다."""
    today = date.today()
    url = SISE_URL.format(
        symbol=symbol,
        start=(today - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d"),
        end=today.strftime("%Y%m%d"),
    )
    last_error: Exception | None = None
    for attempt in range(MAX_RETRY):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=20) as response:
                body = response.read().decode("utf-8", "replace")
            break
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = error
            time.sleep(2**attempt)
    else:
        raise RuntimeError(f"요청 실패: {symbol}") from last_error

    rows: list[tuple[str, float, int]] = []
    for match in ROW_PATTERN.finditer(body):
        fields = [field.strip() for field in match.group(2).split(",")]
        try:
            rows.append((match.group(1), float(fields[3]), int(float(fields[4]))))
        except (IndexError, ValueError):
            continue
    rows.sort(key=lambda row: row[0])
    return rows


def moving_average(closes: list[float], end_index: int, window: int) -> float:
    """end_index를 포함한 최근 window 거래일의 종가 평균."""
    start = end_index - window + 1
    if start < 0:
        return 0.0
    return round(sum(closes[start : end_index + 1]) / window, 2)


def average_volume(volumes: list[int], end_index: int, window: int) -> int:
    """end_index를 포함한 최근 window 거래일의 거래량 평균."""
    start = end_index - window + 1
    if start < 0:
        return 0
    return round(sum(volumes[start : end_index + 1]) / window)


def daily_rs(closes: list[float], kospis: list[float], index: int) -> float | None:
    """전일 대비 종가 변화율을 코스피 변화율로 나눈 일별 RS(100=시장과 동일)."""
    if index < 1:
        return None
    prev_close, prev_kospi = closes[index - 1], kospis[index - 1]
    close, kospi = closes[index], kospis[index]
    if prev_close <= 0 or prev_kospi <= 0 or kospi <= 0:
        return None
    return (close / prev_close) / (kospi / prev_kospi) * 100


def average_rs(closes: list[float], kospis: list[float], end_index: int, window: int) -> float:
    """기준일 포함 최근 window 거래일의 일별 RS 평균."""
    start = end_index - window + 1
    if start < 1:
        return 0.0
    values = [
        value
        for index in range(start, end_index + 1)
        if (value := daily_rs(closes, kospis, index)) is not None
    ]
    return round(sum(values) / len(values), 4) if values else 0.0


def top52_value(dates: list[str], closes: list[float], end_index: int) -> float:
    """기준일로부터 과거 1년(52주) 구간의 최고 종가."""
    limit = (
        datetime.strptime(dates[end_index], "%Y%m%d") - timedelta(days=365)
    ).strftime("%Y%m%d")
    window = [
        close
        for day, close in zip(dates[: end_index + 1], closes[: end_index + 1])
        if day > limit
    ]
    return round(max(window), 2) if window else 0.0


def build_stock_data_records(
    item: dict,
    series: list[tuple[str, float, int]],
    kospi_close_by_date: dict[str, float],
    base_dates: list[str],
    market_sum_today: float,
) -> list[dict]:
    """한 종목에 대해 STOCK_DATA 행을 만든다. STOCKS와 같은 키를 그대로 쓴다."""
    # 코스피가 있는 거래일만 남겨 RS·이평 계산 인덱스가 맞춰지게 한다
    aligned = [
        (day, close, volume)
        for day, close, volume in series
        if day in kospi_close_by_date
    ]
    if not aligned:
        return []

    dates = [row[0] for row in aligned]
    closes = [row[1] for row in aligned]
    volumes = [row[2] for row in aligned]
    kospis = [kospi_close_by_date[day] for day in dates]
    index_by_date = {day: position for position, day in enumerate(dates)}
    latest_close = closes[-1] if closes else 0.0

    records = []
    for base_date in base_dates:
        position = index_by_date.get(base_date)
        if position is None:
            continue
        # 발행주식수가 같다면 시가총액은 종가에 비례한다
        if latest_close > 0 and market_sum_today > 0:
            market_sum = round(market_sum_today * (closes[position] / latest_close), 1)
        else:
            market_sum = market_sum_today
        record = {
            "stockCode": item["stockCode"],
            "stockItem": item["stockItem"],
            "date": base_date,
            "sectorCode": item["sectorCode"],
            "sectorItem": item["sectorItem"],
            "value": closes[position],
            "proc": volumes[position],
            "kospi": kospis[position],
            "top52Value": top52_value(dates, closes, position),
            "marketSum": market_sum,
        }
        for window in RS_WINDOWS:
            record[f"rs{window}"] = average_rs(closes, kospis, position, window)
        for window in MA_WINDOWS:
            record[f"ma{window}"] = moving_average(closes, position, window)
        for window in PROC_WINDOWS:
            record[f"proc{window}"] = average_volume(volumes, position, window)
        records.append(record)
    return records


def insert_stock_data(
    project: str,
    api_key: str,
    trading_days: int,
    upsert: bool,
    clear: bool,
    progress=None,
) -> dict:
    """STOCKS 종목을 기준으로 시세를 모아 지표를 계산해 STOCK_DATA에 적재한다."""
    items = fetch_stocks(project, api_key)
    if not items:
        raise RuntimeError(f"{STOCKS_TABLE} 테이블에 종목이 없습니다")

    kospi_series = fetch_daily(KOSPI_SYMBOL)
    kospi_close_by_date = {day: close for day, close, _ in kospi_series}
    base_dates = [day for day, _, _ in kospi_series][-max(1, trading_days) :]

    url = rest_url(project, STOCK_DATA_TABLE)
    if clear:
        delete_all(url, api_key, "stockCode")

    started = time.time()
    summary = {
        "base_dates": base_dates,
        "items": len(items),
        "records": 0,
        "ok_items": 0,
        "failed": 0,
        "elapsed": 0.0,
        "by_item": {},
    }
    buffer: list[dict] = []

    def flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        insert_rows(url, api_key, buffer, STOCK_DATA_CONFLICT, upsert)
        summary["records"] += len(buffer)
        buffer = []

    for processed, item in enumerate(items, start=1):
        stock_code = str(item.get("stockCode", "")).strip()
        stock_item = item.get("stockItem") or ""
        sector = item.get("sectorCode") or "etc"
        stats = summary["by_item"].setdefault(
            stock_item,
            {"구분": stock_item or "?", "종목": 0, "성공": 0, "실패": 0, "레코드": 0},
        )
        stats["종목"] += 1
        added = 0

        if stock_code:
            try:
                series = fetch_daily(stock_code)
                market_sum = fetch_market_sum(stock_code, stock_item)
                records = build_stock_data_records(
                    item, series, kospi_close_by_date, base_dates, market_sum
                )
                buffer.extend(records)
                added = len(records)
                summary["ok_items"] += 1
                stats["성공"] += 1
                stats["레코드"] += added
                if len(buffer) >= CHUNK_SIZE:
                    flush()
            except (RuntimeError, OSError, KeyError, TypeError):
                summary["failed"] += 1
                stats["실패"] += 1
        else:
            summary["failed"] += 1
            stats["실패"] += 1

        if processed % SLEEP_EVERY == 0:
            time.sleep(SLEEP_SEC)

        summary["elapsed"] = round(time.time() - started, 1)
        if progress is not None:
            progress(
                {
                    "processed": processed,
                    "stockCode": stock_code,
                    "stockName": item.get("stockName", ""),
                    "stockItem": stock_item,
                    "sectorCode": sector,
                    "added": added,
                    "log": processed % SLEEP_EVERY == 0 or processed == len(items),
                    **summary,
                }
            )

    flush()
    summary["elapsed"] = round(time.time() - started, 1)
    summary["files"] = [summary["by_item"][key] for key in sorted(summary["by_item"])]
    return summary


def parse_trading_days(raw: str) -> int | None:
    """입력값을 기준 거래일 수로 바꾼다. 정수가 아니거나 범위를 벗어나면 None."""
    try:
        days = int(str(raw).strip())
    except ValueError:
        return None
    return days if 1 <= days <= MAX_TRADING_DAYS else None


def _render_base_results(results: list[dict]) -> None:
    """테이블별 기준 데이터 적재 결과를 표로 출력한다."""
    failed = [row for row in results if not row.get("ok")]
    total = sum(row["행"] for row in results)
    if failed:
        st.error(f"기준 데이터 INSERT 실패 — {len(failed)}개 테이블")
    else:
        st.success(f"기준 데이터 INSERT 완료 — {total:,}행")
    st.dataframe(
        [{key: value for key, value in row.items() if key != "ok"} for row in results],
        width="stretch",
        hide_index=True,
    )


def _render_stock_data_result(result: dict) -> None:
    """시세 적재 결과를 기준 데이터 INSERT와 같이 완료 배너·요약·표로 출력한다."""
    if not result.get("ok"):
        st.error(f"시세 데이터 INSERT 실패 — {result.get('error', '알 수 없는 오류')}")
        return

    summary = result["summary"]
    base_dates = summary.get("base_dates") or []
    if base_dates:
        date_text = f"{base_dates[0]} ~ {base_dates[-1]}"
    else:
        date_text = "없음"

    st.success(
        f"시세 데이터 INSERT 완료 — 레코드 {summary['records']:,}건 "
        f"(성공 {summary['ok_items']} / 실패 {summary['failed']}, {summary['elapsed']}초)"
    )
    columns = st.columns(5)
    columns[0].metric("종목", f"{summary['items']}건")
    columns[1].metric("성공", f"{summary['ok_items']}건")
    columns[2].metric("실패", f"{summary['failed']}건")
    columns[3].metric("레코드", f"{summary['records']:,}건")
    columns[4].metric("소요 시간", f"{summary['elapsed']}초")
    st.caption(
        f"적재 테이블: {STOCK_DATA_TABLE} · 컬럼 {len(STOCK_DATA_COLUMNS)}개 · "
        f"기준일 {len(base_dates)}일 ({date_text})"
    )
    if summary["failed"]:
        st.warning(f"{summary['failed']}개 종목은 시세 조회에 실패해 제외했습니다.")
    st.dataframe(summary["files"], width="stretch", hide_index=True)


def _require_project(api_key: str, found: list[str]) -> tuple[str, str] | None:
    """프로젝트 주소를 확인하고 실패하면 화면에 사유를 남긴 뒤 None을 돌려준다."""
    if not api_key or not found:
        st.error("secrets 설정을 먼저 채운 뒤 다시 실행하세요.")
        return None
    with st.spinner("프로젝트 주소를 확인하는 중입니다…"):
        name, project, attempts = resolve_project(api_key)
    st.dataframe(attempts, width="stretch", hide_index=True)
    if not project:
        st.error("접속되는 프로젝트 주소가 없습니다.")
        return None
    st.caption(f"{name} 값으로 접속합니다: {rest_url(project, '')}")
    return name, project


def _render_base_insert(api_key: str, found: list[str]) -> None:
    """SECTORS·STOCKS 기준 데이터 INSERT UI."""
    st.markdown("**기준 데이터 INSERT**")
    for target in TARGETS:
        path = str(target["file"].relative_to(BASE_DIR)).replace("\\", "/")
        st.caption(f"{path} → {target['table']} ({len(target['columns'])}개 컬럼)")

    with st.form("adm_insert_form", border=False):
        mode_col, button_col = st.columns([4, 2], vertical_alignment="bottom")
        with mode_col:
            mode = st.selectbox(
                "적재 방식",
                INSERT_MODES,
                key="adm_insert_mode",
                accept_new_options=False,
                filter_mode=None,
            )
        with button_col:
            clicked = st.form_submit_button("기준 데이터 INSERT", type="primary")

    if clicked:
        upsert, clear = parse_insert_mode(mode)
        absent = [t["file"] for t in TARGETS if not t["file"].exists()]
        if absent:
            st.error(f"기준 데이터 파일이 없습니다: {', '.join(f.name for f in absent)}")
        else:
            with st.status("기준 데이터 INSERT 진행 중입니다…", expanded=True) as status:
                resolved = _require_project(api_key, found)
                if resolved:
                    _, project = resolved
                    results = insert_base_data(project, api_key, upsert, clear)
                else:
                    results = [
                        {
                            "테이블": target["table"],
                            "파일": target["file"].name,
                            "행": 0,
                            "결과": "실패 — 접속되는 프로젝트 주소가 없습니다",
                            "ok": False,
                        }
                        for target in TARGETS
                    ]
                done = all(row.get("ok") for row in results)
                status.update(
                    label=f"기준 데이터 INSERT {'완료' if done else '실패'}",
                    state="complete" if done else "error",
                )
            st.session_state["adm_last_result"] = results

    if "adm_last_result" in st.session_state:
        _render_base_results(st.session_state["adm_last_result"])


def _render_stock_data_insert(api_key: str, found: list[str]) -> None:
    """시세 지표를 계산해 STOCK_DATA에 적재하는 컨트롤 UI."""
    st.markdown("**시세 데이터 INSERT**")
    st.caption(
        f"{STOCKS_TABLE} 테이블 종목의 시세를 API로 조회·계산해 {STOCK_DATA_TABLE}에 적재합니다. "
        "JSON 파일은 생성하지 않습니다. stockCode·stockItem·sectorCode·sectorItem은 STOCKS와 동일합니다."
    )

    with st.form("adm_stock_data_form", border=False):
        days_col, mode_col, button_col = st.columns([2, 4, 2], vertical_alignment="bottom")
        with days_col:
            raw_days = st.text_input(
                f"기준 숫자 (오늘 포함 최근 거래일 수, 1 ~ {MAX_TRADING_DAYS})",
                value="3",
                key="adm_stock_data_days",
            )
        with mode_col:
            mode = st.selectbox(
                "적재 방식",
                INSERT_MODES,
                key="adm_stock_data_mode",
                accept_new_options=False,
                filter_mode=None,
            )
        with button_col:
            clicked = st.form_submit_button("시세 데이터 INSERT", type="primary")

    if clicked:
        upsert, clear = parse_insert_mode(mode)
        trading_days = parse_trading_days(raw_days)
        if trading_days is None:
            st.error(
                f"기준 숫자는 1 ~ {MAX_TRADING_DAYS} 사이의 정수로 입력하세요. "
                f"입력값: '{raw_days}'"
            )
        else:
            with st.status(
                f"시세 데이터 INSERT 진행 중입니다… (기준 {trading_days}거래일)",
                expanded=True,
            ) as status:
                resolved = _require_project(api_key, found)
                result: dict = {
                    "ok": False,
                    "error": "접속되는 프로젝트 주소가 없습니다",
                }
                if resolved:
                    _, project = resolved
                    progress_bar = st.progress(0.0, text="시작하는 중…")
                    log_area = st.empty()
                    log_lines: list[str] = []

                    def on_progress(event: dict) -> None:
                        progress_bar.progress(
                            event["processed"] / max(1, event["items"]),
                            text=(
                                f"[{event['processed']}/{event['items']}] "
                                f"{event['stockItem']} {event['stockCode']} "
                                f"{event['stockName']} ({event['elapsed']}초)"
                            ),
                        )
                        if event["log"]:
                            log_lines.append(
                                f"[{event['processed']:>5}/{event['items']}] "
                                f"성공 {event['ok_items']:>4} · 실패 {event['failed']:>3} → "
                                f"레코드 {event['records']:>6}건  ({event['elapsed']}초)"
                            )
                            log_area.code("\n".join(log_lines), language="text")

                    try:
                        summary = insert_stock_data(
                            project,
                            api_key,
                            trading_days,
                            upsert,
                            clear,
                            progress=on_progress,
                        )
                        result = {"ok": True, "summary": summary}
                        progress_bar.progress(1.0, text="적재 완료")
                    except Exception as error:  # 원인을 UI에 그대로 노출한다
                        result = {"ok": False, "error": str(error)}
                        progress_bar.empty()

                done = bool(result.get("ok"))
                status.update(
                    label=f"시세 데이터 INSERT {'완료' if done else '실패'}",
                    state="complete" if done else "error",
                )
            st.session_state["adm_stock_data_result"] = result

    if "adm_stock_data_result" in st.session_state:
        _render_stock_data_result(st.session_state["adm_stock_data_result"])


def show() -> None:
    st.subheader("관리")
    st.caption(
        "기준 데이터(SECTORS·STOCKS)와 시세 지표(STOCK_DATA)를 Supabase에 적재합니다. "
        "접속 정보는 secrets의 SUPABASE_PROJECT·SUPABASE_ID·SUPABASE_KEY를 사용합니다."
    )

    api_key = _secret("SUPABASE_KEY")
    projects = {name: _secret(name) for name in PROJECT_SECRETS}
    found = [name for name, value in projects.items() if value]

    if not api_key:
        st.error("secrets에 SUPABASE_KEY가 없습니다.")
    elif not found:
        st.error(f"secrets에 {' 또는 '.join(PROJECT_SECRETS)}가 없습니다.")
    else:
        st.caption(
            f"프로젝트 주소 후보: {', '.join(found)} — 실행할 때 접속되는 값을 자동으로 씁니다."
        )

    with st.container(border=True):
        _render_base_insert(api_key, found)
    with st.container(border=True):
        _render_stock_data_insert(api_key, found)
