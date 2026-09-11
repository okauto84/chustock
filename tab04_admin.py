"""컨트롤 탭: ETF·주식 지표를 섹터별 JSON으로 갱신하고 GitHub에 push한다."""

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
STOCK_LIST_FILE = BASE_DIR / "data" / "stock" / "020_StockList.json"
ETF_OUT_DIR = BASE_DIR / "data" / "values" / "etf"
STOCK_OUT_DIR = BASE_DIR / "data" / "values" / "stock"

JOBS = {
    "etf": {
        "label": "ETF data update",
        "unit": "ETF",
        "list_file": ETF_LIST_FILE,
        "out_dir": ETF_OUT_DIR,
        "commit": "ETF values update",
        # EtfList에만 있는 구성 종목 목록
        "extra_fields": ("itemlist",),
    },
    "stock": {
        "label": "KS-KQ data update",
        "unit": "주식",
        "list_file": STOCK_LIST_FILE,
        "out_dir": STOCK_OUT_DIR,
        "commit": "KS-KQ values update",
        # StockList에만 있는 시장 구분(KS·KQ)과 시가총액
        "extra_fields": ("stock", "marketsum"),
    },
}

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
SLEEP_SEC = 1  # API 부하 방지: 종목 50개 처리마다 1초
SLEEP_EVERY = 50
MAX_RETRY = 3

GIT_USER_NAME = "okauto84"
GIT_USER_EMAIL = "okauto84@gmail.com"

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


def item_fields(item: dict, extra_fields: tuple[str, ...]) -> dict:
    """목록 파일에서 레코드로 그대로 옮길 종목 정보를 뽑는다."""
    fields = {key: item.get(key, "") for key in extra_fields}
    if "itemlist" in fields:
        fields["itemlist"] = item.get("itemlist", [])
    if "marketsum" in fields:  # 억원 단위 문자열이라 숫자로 바꿔 저장한다
        try:
            fields["marketsum"] = float(fields["marketsum"])
        except (TypeError, ValueError):
            fields["marketsum"] = 0.0
    return fields


def build_records(
    item: dict,
    series: list[tuple[str, float, int]],
    kospi_close_by_date: dict[str, float],
    base_dates: list[str],
    extra_fields: tuple[str, ...] = (),
) -> list[dict]:
    """한 종목에 대해 기준일별 지표 레코드를 만든다."""
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
    carried = item_fields(item, extra_fields)
    records = []
    for base_date in base_dates:
        position = index_by_date.get(base_date)
        if position is None:
            continue

        record = {
            "itemcode": item["itemcode"],
            "itemname": item["itemname"],
            **carried,
            "sectorcode": item["sectorcode"],
            "sector": item["sector"],
            "sectorlist": item["sectorlist"],
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


def update_values(
    list_file: Path,
    out_dir: Path,
    trading_days: int,
    extra_fields: tuple[str, ...] = (),
    progress=None,
) -> dict:
    """기준일 수만큼 목록 파일의 종목 지표를 계산해 섹터별 JSON으로 저장한다."""
    items = json.loads(list_file.read_text(encoding="utf-8"))

    kospi_series = fetch_daily(KOSPI_SYMBOL)
    kospi_close_by_date = {day: close for day, close, _ in kospi_series}
    # 오늘(가장 최근 거래일)을 반드시 포함한 최근 N 거래일
    base_dates = [day for day, _, _ in kospi_series][-max(1, trading_days) :]

    sectors: dict[str, list[dict]] = {}
    for item in items:
        sectors.setdefault(item["sector"], []).append(item)

    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    summary = {
        "base_dates": base_dates,
        "files": [],
        "records": 0,
        "failed": 0,
        "items": len(items),
        "sectors": len(sectors),
        "elapsed": 0.0,
    }

    processed = 0
    for order, (sector, members) in enumerate(sorted(sectors.items()), start=1):
        if progress is not None:
            progress(
                {
                    "stage": "start",
                    "order": order,
                    "sector": sector,
                    "members": len(members),
                    "processed": processed,
                    **summary,
                }
            )

        records = []
        for item in members:
            try:
                series = fetch_daily(item["itemcode"])
                records.extend(
                    build_records(
                        item, series, kospi_close_by_date, base_dates, extra_fields
                    )
                )
            except RuntimeError:
                summary["failed"] += 1
            processed += 1
            if processed % SLEEP_EVERY == 0:
                time.sleep(SLEEP_SEC)

        out_file = out_dir / safe_filename(sector)
        with out_file.open("w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
            f.write("\n")

        summary["files"].append(
            {
                "섹터": sector,
                "파일": out_file.name,
                "종목": len(members),
                "레코드": len(records),
            }
        )
        summary["records"] += len(records)
        summary["elapsed"] = round(time.time() - started, 1)

        if progress is not None:
            progress(
                {
                    "stage": "done",
                    "order": order,
                    "sector": sector,
                    "members": len(members),
                    "records": len(records),
                    "processed": processed,
                    **summary,
                }
            )

    summary["elapsed"] = round(time.time() - started, 1)
    return summary


def _secret(name: str) -> str:
    """설정값을 st.secrets에서 읽고, 없으면 환경변수로 대체한다."""
    try:
        value = st.secrets.get(name, "")
    except Exception:  # secrets.toml이 없으면 환경변수만 사용한다
        value = ""
    return str(value or os.environ.get(name, "")).strip()


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


def push_to_github(token: str, message: str, out_dir: Path) -> tuple[bool, str]:
    """저장 폴더의 변경분만 커밋해 origin으로 push한다."""
    target = str(out_dir.relative_to(BASE_DIR)).replace("\\", "/")

    status = _run_git("status", "--porcelain", "--", target, token=token)
    if status.returncode != 0:
        return False, status.stderr.strip()
    if not status.stdout.strip():
        return True, "변경된 파일이 없어 push를 건너뛰었습니다."

    added = _run_git("add", "--", target, token=token)
    if added.returncode != 0:
        return False, added.stderr.strip()

    # 배포 환경에는 git user 설정이 없어 커밋이 실패하므로 identity를 직접 지정한다
    committed = _run_git(
        "-c",
        f"user.name={_secret('GIT_USER_NAME') or GIT_USER_NAME}",
        "-c",
        f"user.email={_secret('GIT_USER_EMAIL') or GIT_USER_EMAIL}",
        "commit",
        "-m",
        message,
        "--",
        target,
        token=token,
    )
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


def github_token() -> str:
    """GITHUB_TOKEN을 st.secrets에서 읽고, 없으면 환경변수로 대체한다."""
    return _secret("GITHUB_TOKEN")


def _run_update(job_key: str, trading_days: int, token: str) -> dict:
    """진행 상황을 실시간으로 그리면서 갱신과 push를 수행한다."""
    job = JOBS[job_key]
    progress_bar = st.progress(0.0, text="시작하는 중…")
    log_area = st.empty()
    log_lines: list[str] = []

    def on_progress(event: dict) -> None:
        ratio = event["processed"] / max(1, event["items"])
        if event["stage"] == "start":
            progress_bar.progress(
                ratio,
                text=f"[{event['order']}/{event['sectors']}] {event['sector']} 수집 중 "
                f"({job['unit']} {event['members']}종목)",
            )
            return
        progress_bar.progress(
            ratio,
            text=f"[{event['order']}/{event['sectors']}] {event['sector']} 완료 "
            f"({event['processed']}/{event['items']} 종목, {event['elapsed']}초)",
        )
        log_lines.append(
            f"[{event['order']:>2}/{event['sectors']}] {event['sector']:<8} "
            f"{job['unit']} {event['members']:>4}종목 → {event['records']:>5}건  "
            f"({event['elapsed']}초)"
        )
        log_area.code("\n".join(log_lines), language="text")

    result: dict = {"ok": False, "job": job_key}
    try:
        result["summary"] = update_values(
            job["list_file"],
            job["out_dir"],
            trading_days,
            job["extra_fields"],
            progress=on_progress,
        )
        result["ok"] = True
    except Exception as error:  # 수집 실패 원인을 UI에 그대로 노출한다
        result["error"] = str(error)
        progress_bar.empty()
        return result

    progress_bar.progress(1.0, text="갱신 완료")

    if token:
        with st.spinner("GitHub에 push 하는 중입니다…"):
            pushed, detail = push_to_github(
                token,
                f"{job['commit']} {result['summary']['base_dates'][-1]}",
                job["out_dir"],
            )
        result["push"] = {"ok": pushed, "detail": detail}
    return result


def _render_result(result: dict) -> None:
    """마지막 실행 결과를 요약 지표와 표로 출력한다."""
    job = JOBS[result.get("job", "etf")]
    if not result.get("ok"):
        st.error(f"{job['label']} 실패 — {result.get('error', '알 수 없는 오류')}")
        return

    summary = result["summary"]
    st.success(
        f"{job['label']} 완료 — 기준일 {summary['base_dates'][0]} ~ {summary['base_dates'][-1]}"
    )

    columns = st.columns(5)
    columns[0].metric("섹터", f"{summary['sectors']}개")
    columns[1].metric(job["unit"], f"{summary['items']}종목")
    columns[2].metric("레코드", f"{summary['records']:,}건")
    columns[3].metric("실패", f"{summary['failed']}종목")
    columns[4].metric("소요 시간", f"{summary['elapsed']}초")

    if summary["failed"]:
        st.warning(f"{summary['failed']}개 종목은 시세 조회에 실패해 제외했습니다.")

    st.caption(f"저장 위치: {job['out_dir']}")
    st.dataframe(summary["files"], width="stretch", hide_index=True)

    push = result.get("push")
    if push is None:
        st.info("secrets에 GITHUB_TOKEN이 없어 push를 건너뛰었습니다.")
    elif push["ok"]:
        st.success(f"GitHub push 성공 — {push['detail']}")
    else:
        st.error(f"GitHub push 실패 — {push['detail']}")


def show() -> None:
    st.subheader("컨트롤")
    st.caption(
        "기준 숫자만큼의 최근 거래일에 대해 지표를 계산하고 섹터별 JSON으로 저장합니다. "
        "오늘(가장 최근 거래일)은 항상 포함됩니다."
    )

    input_col, etf_col, stock_col, _ = st.columns([2, 2, 2, 1], vertical_alignment="bottom")
    with input_col:
        trading_days = st.number_input(
            "기준 숫자 (오늘 포함 최근 거래일 수)",
            min_value=1,
            max_value=120,
            value=3,
            step=1,
            key="admin_trading_days",
        )
    with etf_col:
        etf_clicked = st.button(JOBS["etf"]["label"], type="primary")
    with stock_col:
        stock_clicked = st.button(JOBS["stock"]["label"], type="primary")

    job_key = "etf" if etf_clicked else "stock" if stock_clicked else ""
    if job_key:
        job = JOBS[job_key]
        if not job["list_file"].exists():
            st.error(f"목록 파일이 없습니다: {job['list_file']}")
            return
        with st.status(f"{job['label']} 진행 중입니다…", expanded=True) as status:
            result = _run_update(job_key, int(trading_days), github_token())
            status.update(
                label=f"{job['label']} {'완료' if result.get('ok') else '실패'}",
                state="complete" if result.get("ok") else "error",
            )
        st.session_state["admin_last_result"] = result

    if "admin_last_result" in st.session_state:
        st.divider()
        _render_result(st.session_state["admin_last_result"])
