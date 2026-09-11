"""컨트롤 탭: ETF 지표를 섹터별 JSON으로 갱신하고 GitHub에 push한다."""

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

import streamlit as st

BASE_DIR = Path(__file__).resolve().parent
ETF_LIST_FILE = BASE_DIR / "data" / "stock" / "030_EtfList.json"
OUT_DIR = BASE_DIR / "data" / "values" / "etf"

SISE_URL = (
    "https://api.finance.naver.com/siseJson.naver"
    "?symbol={symbol}&requestType=1&startTime={start}&endTime={end}&timeframe=day"
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
}

KOSPI_SYMBOL = "KOSPI"
LOOKBACK_DAYS = 600  # ma150·52주 신고가 계산에 필요한 여유 기간
MA_WINDOWS = (10, 20, 30, 50, 100, 150)
RS_WINDOWS = (20, 50)
SLEEP_SEC = 1  # API 부하 방지: 섹터 1개 처리마다 1초
MAX_RETRY = 3

ROW_PATTERN = re.compile(r'\["(\d{8})",([^\]]*)\]')


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


def high_52weeks(dates: list[str], closes: list[float], end_index: int) -> float:
    """기준일로부터 1년 이내 최고 종가."""
    limit = (datetime.strptime(dates[end_index], "%Y%m%d") - timedelta(days=365)).strftime("%Y%m%d")
    window = [close for day, close in zip(dates[: end_index + 1], closes) if day > limit]
    return round(max(window), 2) if window else 0.0


def build_records(
    etf: dict,
    series: list[tuple[str, float, int]],
    kospi_close_by_date: dict[str, float],
    base_dates: list[str],
) -> list[dict]:
    """ETF 한 종목에 대해 기준일별 지표 레코드를 만든다."""
    dates = [row[0] for row in series]
    closes = [row[1] for row in series]
    volumes = [row[2] for row in series]
    index_by_date = {day: position for position, day in enumerate(dates)}

    # Mansfield 상대강도: 종가 ÷ 코스피 종가 × 100
    rs_series = [
        close / kospi_close_by_date[day] * 100 if kospi_close_by_date.get(day) else 0.0
        for day, close in zip(dates, closes)
    ]

    latest_base_date = base_dates[-1]
    records = []
    for base_date in base_dates:
        position = index_by_date.get(base_date)
        if position is None:
            continue

        record = {
            "itemcode": etf["itemcode"],
            "itemname": etf["itemname"],
            "itemlist": etf.get("itemlist", []),
            "sectorcode": etf["sectorcode"],
            "sector": etf["sector"],
            "sectorlist": etf["sectorlist"],
            "date": base_date,
            "value": closes[position],
            "proc": volumes[position],
            "kospi": kospi_close_by_date.get(base_date, 0.0),
            "Top52": high_52weeks(dates, closes, position) if base_date == latest_base_date else 0,
        }
        for window in RS_WINDOWS:
            start = position - window + 1
            record[f"RS{window}"] = (
                round(sum(rs_series[start : position + 1]) / window, 4) if start >= 0 else 0.0
            )
        for window in MA_WINDOWS:
            record[f"ma{window}"] = moving_average(closes, position, window)
        records.append(record)
    return records


def safe_filename(sector: str) -> str:
    """'전력/에너지'처럼 경로 구분자가 들어간 섹터명을 파일명으로 바꾼다."""
    return re.sub(r'[\\/:*?"<>|]', "-", sector) + ".json"


def update_etf_values(trading_days: int, progress=None) -> dict:
    """기준일 수만큼 ETF 지표를 계산해 섹터별 JSON으로 저장한다."""
    etfs = json.loads(ETF_LIST_FILE.read_text(encoding="utf-8"))

    kospi_series = fetch_daily(KOSPI_SYMBOL)
    kospi_close_by_date = {day: close for day, close, _ in kospi_series}
    # 오늘(가장 최근 거래일)을 반드시 포함한 최근 N 거래일
    base_dates = [day for day, _, _ in kospi_series][-max(1, trading_days) :]

    sectors: dict[str, list[dict]] = {}
    for etf in etfs:
        sectors.setdefault(etf["sector"], []).append(etf)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = {"base_dates": base_dates, "files": [], "records": 0, "failed": 0}

    for order, (sector, members) in enumerate(sorted(sectors.items()), start=1):
        records = []
        for etf in members:
            try:
                series = fetch_daily(etf["itemcode"])
            except RuntimeError:
                summary["failed"] += 1
                continue
            records.extend(build_records(etf, series, kospi_close_by_date, base_dates))

        out_file = OUT_DIR / safe_filename(sector)
        with out_file.open("w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
            f.write("\n")

        summary["files"].append({"sector": sector, "file": out_file.name, "records": len(records)})
        summary["records"] += len(records)

        if progress is not None:
            progress(order, len(sectors), sector, len(records))
        time.sleep(SLEEP_SEC)

    return summary


def _run_git(*args: str, token: str = "") -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", *args],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if token:
        result.stdout = (result.stdout or "").replace(token, "***")
        result.stderr = (result.stderr or "").replace(token, "***")
    return result


def push_to_github(token: str, message: str) -> tuple[bool, str]:
    """data/values/etf 변경분만 커밋해 origin으로 push한다."""
    target = str(OUT_DIR.relative_to(BASE_DIR)).replace("\\", "/")

    status = _run_git("status", "--porcelain", "--", target, token=token)
    if status.returncode != 0:
        return False, status.stderr.strip()
    if not status.stdout.strip():
        return True, "변경된 파일이 없어 push를 건너뛰었습니다."

    added = _run_git("add", "--", target, token=token)
    if added.returncode != 0:
        return False, added.stderr.strip()

    committed = _run_git("commit", "-m", message, "--", target, token=token)
    if committed.returncode != 0:
        return False, committed.stderr.strip() or committed.stdout.strip()

    remote = _run_git("remote", "get-url", "origin", token=token)
    if remote.returncode != 0:
        return False, remote.stderr.strip()
    origin = remote.stdout.strip()
    if origin.startswith("https://"):
        origin = f"https://x-access-token:{token}@{origin.split('://', 1)[1].split('@')[-1]}"

    branch = _run_git("rev-parse", "--abbrev-ref", "HEAD", token=token).stdout.strip() or "main"
    pushed = _run_git("push", origin, f"HEAD:{branch}", token=token)
    if pushed.returncode != 0:
        return False, pushed.stderr.strip()
    return True, f"{branch} 브랜치로 push 완료: {committed.stdout.strip().splitlines()[0]}"


def show() -> None:
    st.subheader("컨트롤")

    trading_days = st.number_input(
        "기준 숫자 (오늘 포함 거래일 수)",
        min_value=1,
        max_value=120,
        value=3,
        step=1,
    )
    github_token = st.text_input(
        "GITHUB_TOKEN",
        value=os.environ.get("GITHUB_TOKEN", ""),
        type="password",
        help="비워두면 JSON 생성까지만 진행합니다.",
    )

    if not st.button("ETF data update"):
        return

    if not ETF_LIST_FILE.exists():
        st.error(f"ETF 목록 파일이 없습니다: {ETF_LIST_FILE}")
        return

    status_area = st.empty()
    progress_bar = st.progress(0.0)

    def on_progress(order: int, total: int, sector: str, count: int) -> None:
        progress_bar.progress(order / total)
        status_area.write(f"[{order}/{total}] {sector} — {count}건 저장")

    with st.spinner("ETF 데이터를 갱신하는 중입니다."):
        try:
            summary = update_etf_values(int(trading_days), progress=on_progress)
        except Exception as error:  # 수집 실패 시 UI에 그대로 노출한다
            progress_bar.empty()
            st.error(f"ETF data update 실패: {error}")
            return

    progress_bar.progress(1.0)
    st.success(
        f"ETF data update 완료 — 기준일 {summary['base_dates'][0]}~{summary['base_dates'][-1]}, "
        f"{len(summary['files'])}개 섹터 / {summary['records']}건 저장"
    )
    if summary["failed"]:
        st.warning(f"{summary['failed']}개 종목은 시세 조회에 실패해 제외했습니다.")
    st.dataframe(summary["files"], width="stretch")

    if not github_token:
        st.info("GITHUB_TOKEN이 비어 있어 push를 건너뛰었습니다.")
        return

    with st.spinner("GitHub에 push 하는 중입니다."):
        ok, detail = push_to_github(
            github_token,
            f"ETF values update {summary['base_dates'][-1]}",
        )

    if ok:
        st.success(f"GitHub push 성공 — {detail}")
    else:
        st.error(f"GitHub push 실패 — {detail}")
