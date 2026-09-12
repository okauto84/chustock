"""컨트롤 탭: ETF·주식 지표를 종목별 JSON으로 갱신하고 GitHub에 push한다."""

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
LOOKBACK_DAYS = 900  # 기준일 250거래일 + ma150 계산에 필요한 여유 기간
MAX_TRADING_DAYS = 250  # 약 1년치 거래일
MA_WINDOWS = (10, 20, 30, 50, 100, 150)
SLEEP_SEC = 1  # API 부하 방지: 종목 50개 처리마다 1초
SLEEP_EVERY = 50
MAX_RETRY = 3

GIT_TIMEOUT = 120  # 로컬 git 명령이 멈춰 앱이 잠기지 않도록 제한
GIT_NET_TIMEOUT = 600  # 종목 파일이 많아 push·pull은 더 넉넉히 준다
REJECT_HINTS = ("rejected", "non-fast-forward", "fetch first")

GIT_USER_NAME = "okauto84"
GIT_USER_EMAIL = "okauto84@gmail.com"
GITHUB_REPO = "okauto84/chustock"

ROW_PATTERN = re.compile(r'\["(\d{8})",([^\]]*)\]')
# https://host/owner/repo(.git), git@host:owner/repo(.git), ssh://git@host/owner/repo 모두 처리
REMOTE_PATTERN = re.compile(
    r"^(?:[a-z]+://)?(?:[^@/]+@)?(?P<host>[^/:]+)[/:](?P<path>[^\s]+?)(?:\.git)?/?$"
)


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
        for window in MA_WINDOWS:
            record[f"ma{window}"] = moving_average(closes, position, window)
        records.append(record)
    return records


def merge_records(out_file: Path, records: list[dict]) -> list[dict]:
    """기존 파일의 날짜는 남기고 이번에 계산한 기준일만 덮어써 날짜순으로 돌려준다."""
    stored: list[dict] = []
    if out_file.exists():
        try:
            loaded = json.loads(out_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):  # 깨진 파일은 새로 만든다
            loaded = []
        if isinstance(loaded, list):
            stored = [row for row in loaded if isinstance(row, dict) and row.get("date")]

    by_date = {row["date"]: row for row in stored}
    by_date.update({row["date"]: row for row in records})
    return [by_date[day] for day in sorted(by_date)]


def update_item_values(
    list_file: Path,
    out_dir: Path,
    trading_days: int,
    extra_fields: tuple[str, ...] = (),
    progress=None,
) -> dict:
    """기준일 수만큼 종목 지표를 계산해 {itemcode}.json 파일마다 병합 저장한다."""
    items = json.loads(list_file.read_text(encoding="utf-8"))

    kospi_series = fetch_daily(KOSPI_SYMBOL)
    kospi_close_by_date = {day: close for day, close, _ in kospi_series}
    # 오늘(가장 최근 거래일)을 반드시 포함한 최근 N 거래일
    base_dates = [day for day, _, _ in kospi_series][-max(1, trading_days) :]

    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    summary = {
        "base_dates": base_dates,
        "files": [],
        "records": 0,
        "created": 0,
        "updated": 0,
        "failed": 0,
        "items": len(items),
        "sectors": 0,
        "elapsed": 0.0,
    }

    by_sector: dict[str, dict] = {}
    for processed, item in enumerate(items, start=1):
        itemcode = str(item.get("itemcode", "")).strip()
        sector = item.get("sector") or "기타"
        stats = by_sector.setdefault(
            sector,
            {"섹터": sector, "종목": 0, "신규": 0, "갱신": 0, "레코드": 0, "실패": 0},
        )
        stats["종목"] += 1

        added = 0
        if itemcode:
            out_file = out_dir / f"{itemcode}.json"
            is_new = not out_file.exists()
            try:
                series = fetch_daily(itemcode)
                records = build_records(
                    item, series, kospi_close_by_date, base_dates, extra_fields
                )
                merged = merge_records(out_file, records)
                with out_file.open("w", encoding="utf-8") as f:
                    json.dump(merged, f, ensure_ascii=False, indent=2)
                    f.write("\n")
            except (RuntimeError, OSError):
                summary["failed"] += 1
                stats["실패"] += 1
            else:
                added = len(records)
                summary["records"] += added
                summary["created" if is_new else "updated"] += 1
                stats["신규" if is_new else "갱신"] += 1
                stats["레코드"] += added
        else:
            summary["failed"] += 1
            stats["실패"] += 1

        if processed % SLEEP_EVERY == 0:  # API 부하 방지
            time.sleep(SLEEP_SEC)

        summary["elapsed"] = round(time.time() - started, 1)
        if progress is not None:
            progress(
                {
                    "processed": processed,
                    "itemcode": itemcode,
                    "itemname": item.get("itemname", ""),
                    "sector": sector,
                    "added": added,
                    # 종목마다 로그를 쌓으면 화면이 밀리므로 50종목 단위로만 남긴다
                    "log": processed % SLEEP_EVERY == 0 or processed == len(items),
                    **summary,
                }
            )

    summary["files"] = [by_sector[sector] for sector in sorted(by_sector)]
    summary["sectors"] = len(by_sector)
    summary["elapsed"] = round(time.time() - started, 1)
    return summary


def _secret(name: str) -> str:
    """설정값을 st.secrets에서 읽고, 없으면 환경변수로 대체한다."""
    try:
        value = st.secrets.get(name, "")
    except Exception:  # secrets.toml이 없으면 환경변수만 사용한다
        value = ""
    return str(value or os.environ.get(name, "")).strip()


def _run_git(
    *args: str, token: str = "", timeout: int = GIT_TIMEOUT
) -> subprocess.CompletedProcess:
    # 토큰이 만료돼도 자격 증명 입력을 기다리며 앱이 멈추지 않도록 프롬프트와 helper를 끈다
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    try:
        result = subprocess.run(
            ["git", "-c", "credential.helper=", *args],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            args, 1, "", f"git 명령이 {timeout}초 안에 끝나지 않아 중단했습니다."
        )
    if token:
        result.stdout = (result.stdout or "").replace(token, "***")
        result.stderr = (result.stderr or "").replace(token, "***")
    return result


def git_identity() -> tuple[str, ...]:
    """배포 환경에는 git user 설정이 없어 커밋·rebase가 실패하므로 직접 지정한다."""
    return (
        "-c",
        f"user.name={_secret('GIT_USER_NAME') or GIT_USER_NAME}",
        "-c",
        f"user.email={_secret('GIT_USER_EMAIL') or GIT_USER_EMAIL}",
    )


def is_rejected(stderr: str) -> bool:
    """원격에 다른 커밋이 있어 push가 거부된 경우인지 판별한다."""
    lowered = stderr.lower()
    return any(hint in lowered for hint in REJECT_HINTS)


def push_url(origin: str, token: str) -> str:
    """origin 주소를 토큰이 붙은 https 주소로 바꾼다.

    SSH 주소(git@github.com:owner/repo.git)를 그대로 쓰면 호스트 키가 없는 배포 환경에서
    'Host key verification failed'로 실패하므로 항상 https로 변환한다.
    """
    matched = REMOTE_PATTERN.match(origin)
    host, path = (matched.group("host"), matched.group("path")) if matched else ("", "")
    if not path:  # origin이 없거나 형식을 못 읽으면 설정된 저장소로 push한다
        host, path = "github.com", _secret("GITHUB_REPO") or GITHUB_REPO
    return f"https://x-access-token:{token}@{host or 'github.com'}/{path}.git"


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

    committed = _run_git(*git_identity(), "commit", "-m", message, "--", target, token=token)
    if committed.returncode != 0:
        return False, committed.stderr.strip() or committed.stdout.strip()

    remote = _run_git("remote", "get-url", "origin", token=token)
    origin = push_url(remote.stdout.strip() if remote.returncode == 0 else "", token)

    branch = _run_git("rev-parse", "--abbrev-ref", "HEAD", token=token).stdout.strip() or "main"
    if branch == "HEAD":  # detached HEAD에서는 브랜치 이름을 알 수 없다
        branch = "main"

    pushed = _run_git("push", origin, f"HEAD:{branch}", token=token, timeout=GIT_NET_TIMEOUT)
    rebased_note = ""
    if pushed.returncode != 0 and is_rejected(pushed.stderr):
        # 원격에 다른 커밋이 있으면 rebase로 합친 뒤 한 번만 다시 시도한다
        rebased = _run_git(
            *git_identity(),
            "pull",
            "--rebase",
            "--autostash",
            origin,
            branch,
            token=token,
            timeout=GIT_NET_TIMEOUT,
        )
        if rebased.returncode != 0:
            _run_git("rebase", "--abort", token=token)
            return False, (
                "원격 커밋과 합치지 못해 push를 중단했습니다(커밋은 로컬에 남아 있습니다) — "
                f"{rebased.stderr.strip() or rebased.stdout.strip()}"
            )
        rebased_note = " (원격 커밋과 rebase 후 재시도)"
        pushed = _run_git("push", origin, f"HEAD:{branch}", token=token, timeout=GIT_NET_TIMEOUT)

    if pushed.returncode != 0:
        return False, pushed.stderr.strip() or pushed.stdout.strip()

    headline = committed.stdout.strip().splitlines()
    return True, f"{branch} 브랜치로 push 완료{rebased_note}: {headline[0] if headline else message}"


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
        progress_bar.progress(
            event["processed"] / max(1, event["items"]),
            text=f"[{event['processed']}/{event['items']}] {event['sector']} "
            f"{event['itemcode']} {event['itemname']} ({event['elapsed']}초)",
        )
        if event["log"]:
            log_lines.append(
                f"[{event['processed']:>5}/{event['items']}] "
                f"파일 신규 {event['created']:>4} · 갱신 {event['updated']:>4} → "
                f"{event['records']:>6}건  실패 {event['failed']:>3}  "
                f"({event['elapsed']}초)"
            )
            log_area.code("\n".join(log_lines), language="text")

    result: dict = {"ok": False, "job": job_key}
    try:
        result["summary"] = update_item_values(
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
            try:
                pushed, detail = push_to_github(
                    token,
                    f"{job['commit']} {result['summary']['base_dates'][-1]}",
                    job["out_dir"],
                )
            except Exception as error:  # push가 깨져도 갱신 결과는 화면에 남긴다
                pushed, detail = False, str(error)
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
        f"{job['label']} 완료 — 기준일 {len(summary['base_dates'])}일 "
        f"({summary['base_dates'][0]} ~ {summary['base_dates'][-1]})"
    )

    columns = st.columns(6)
    columns[0].metric(job["unit"], f"{summary['items']}종목")
    columns[1].metric("신규 파일", f"{summary['created']}개")
    columns[2].metric("갱신 파일", f"{summary['updated']}개")
    columns[3].metric("레코드", f"{summary['records']:,}건")
    columns[4].metric("실패", f"{summary['failed']}종목")
    columns[5].metric("소요 시간", f"{summary['elapsed']}초")

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


def parse_trading_days(raw: str) -> int | None:
    """입력값을 기준 거래일 수로 바꾼다. 정수가 아니거나 범위를 벗어나면 None."""
    try:
        days = int(str(raw).strip())
    except ValueError:
        return None
    return days if 1 <= days <= MAX_TRADING_DAYS else None


def _render_job(job_key: str) -> None:
    """작업 하나의 입력·버튼·결과를 독립된 영역으로 그린다."""
    job = JOBS[job_key]
    state_key = f"admin_last_result_{job_key}"

    st.markdown(f"**{job['label']}**")
    out_path = str(job["out_dir"].relative_to(BASE_DIR)).replace("\\", "/")
    st.caption(f"{job['list_file'].name} → {out_path}/{{itemcode}}.json")

    # 폼으로 묶으면 입력창에서 Enter를 누르지 않고 버튼을 눌러도 입력값이 함께 전달된다
    with st.form(f"admin_form_{job_key}", border=False):
        input_col, button_col, _ = st.columns([2, 2, 3], vertical_alignment="bottom")
        with input_col:
            raw_days = st.text_input(
                f"기준 숫자 (오늘 포함 최근 거래일 수, 1 ~ {MAX_TRADING_DAYS})",
                value="3",
                key=f"admin_days_{job_key}",
            )
        with button_col:
            clicked = st.form_submit_button(job["label"], type="primary")

    if clicked:
        if not job["list_file"].exists():
            st.error(f"목록 파일이 없습니다: {job['list_file']}")
            return
        trading_days = parse_trading_days(raw_days)
        if trading_days is None:
            st.error(
                f"기준 숫자는 1 ~ {MAX_TRADING_DAYS} 사이의 정수로 입력하세요. "
                f"입력값: '{raw_days}'"
            )
            return
        with st.status(
            f"{job['label']} 진행 중입니다… (기준 {trading_days}거래일)", expanded=True
        ) as status:
            result = _run_update(job_key, trading_days, github_token())
            status.update(
                label=f"{job['label']} {'완료' if result.get('ok') else '실패'}",
                state="complete" if result.get("ok") else "error",
            )
        st.session_state[state_key] = result

    if state_key in st.session_state:
        _render_result(st.session_state[state_key])


def show() -> None:
    st.subheader("컨트롤")
    st.caption(
        "기준 숫자만큼의 최근 거래일에 대해 지표를 계산해 종목별 JSON으로 저장합니다. "
        "오늘(가장 최근 거래일)은 항상 포함됩니다."
    )

    # 두 작업의 실행 결과를 각각 유지하도록 컨테이너와 세션 키를 분리한다
    with st.container(border=True):
        _render_job("etf")
    with st.container(border=True):
        _render_job("stock")
