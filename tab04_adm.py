"""관리 탭: 기준 데이터 JSON을 Supabase 테이블(SECTORS·STOCKS)에 적재한다."""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import streamlit as st

BASE_DIR = Path(__file__).resolve().parent
SECTOR_FILE = BASE_DIR / "data" / "stock" / "010_SectorList.json"
STOCK_FILE = BASE_DIR / "data" / "stock" / "020_StockList.json"

CHUNK_SIZE = 500  # PostgREST 한 번에 보낼 행 수
TIMEOUT = 60
MAX_RETRY = 3

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

DDL_HINT = """-- 컬럼명이 JSON key와 대소문자까지 같아야 하므로 큰따옴표로 만든다
create table if not exists "SECTORS" (
  "sectorCode"  text primary key,
  "sectorName"  text,
  "sectorItems" jsonb
);

create table if not exists "STOCKS" (
  "stockCode"  text primary key,
  "stockName"  text,
  "stockItem"  text,
  "stockItems" jsonb,
  "sectorCode" text,
  "sectorItem" text
);"""


def _secret(name: str) -> str:
    """설정값을 st.secrets에서 읽고, 없으면 환경변수로 대체한다."""
    try:
        value = st.secrets.get(name, "")
    except Exception:  # secrets.toml이 없으면 환경변수만 사용한다
        value = ""
    return str(value or os.environ.get(name, "")).strip()


def rest_url(project: str, table: str) -> str:
    """SUPABASE_PROJECT를 REST 엔드포인트로 바꾼다. 프로젝트 ref와 전체 URL을 모두 받는다."""
    project = project.strip().rstrip("/")
    base = project if project.startswith("http") else f"https://{project}.supabase.co"
    return f"{base}/rest/v1/{urllib.parse.quote(table)}"


def call_rest(url: str, api_key: str, method: str, prefer: str, payload: list | None) -> str:
    """Supabase REST를 호출하고 응답 본문을 돌려준다. 실패 시 지수 백오프로 재시도한다."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Prefer": prefer,
    }
    last_error: Exception | None = None
    for attempt in range(MAX_RETRY):
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:  # 원인이 본문에 담겨 오므로 그대로 올린다
            detail = error.read().decode("utf-8", "replace").strip()
            raise RuntimeError(f"HTTP {error.code} {detail}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = error
            time.sleep(2**attempt)
    raise RuntimeError(f"요청 실패: {url} — {last_error}")


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
    url: str, api_key: str, rows: list[dict], key_column: str, upsert: bool, progress=None
) -> int:
    """행을 CHUNK_SIZE 단위로 나눠 적재하고 적재된 행 수를 돌려준다."""
    prefer = "return=minimal"
    endpoint = url
    if upsert:  # 같은 키가 이미 있으면 갱신한다
        prefer += ",resolution=merge-duplicates"
        endpoint = f"{url}?{urllib.parse.urlencode({'on_conflict': key_column})}"

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


def _render_results(results: list[dict]) -> None:
    """테이블별 적재 결과를 표로 출력한다."""
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


def show() -> None:
    st.subheader("관리")
    st.caption(
        "010_SectorList.json을 SECTORS 테이블, 020_StockList.json을 STOCKS 테이블에 적재합니다. "
        "접속 정보는 secrets의 SUPABASE_PROJECT·SUPABASE_KEY를 사용합니다."
    )

    project = _secret("SUPABASE_PROJECT")
    api_key = _secret("SUPABASE_KEY")

    with st.container(border=True):
        st.markdown("**기준 데이터 INSERT**")
        for target in TARGETS:
            path = str(target["file"].relative_to(BASE_DIR)).replace("\\", "/")
            st.caption(f"{path} → {target['table']} ({len(target['columns'])}개 컬럼)")

        missing = [
            name
            for name, value in (("SUPABASE_PROJECT", project), ("SUPABASE_KEY", api_key))
            if not value
        ]
        if missing:
            st.error(f"secrets에 {', '.join(missing)}가 없습니다.")
        else:
            st.caption(f"대상 프로젝트: {rest_url(project, '')}")

        with st.expander("테이블이 없다면 아래 DDL로 먼저 생성하세요"):
            st.code(DDL_HINT, language="sql")

        with st.form("adm_insert_form", border=False):
            mode_col, clear_col, button_col = st.columns([3, 2, 2], vertical_alignment="bottom")
            with mode_col:
                mode = st.radio(
                    "중복 처리",
                    ("덮어쓰기(upsert)", "그대로 INSERT"),
                    horizontal=True,
                    key="adm_insert_mode",
                )
            with clear_col:
                clear = st.checkbox("기존 데이터 삭제 후 적재", key="adm_insert_clear")
            with button_col:
                clicked = st.form_submit_button("기준 데이터 INSERT", type="primary")

        if clicked:
            absent = [t["file"] for t in TARGETS if not t["file"].exists()]
            if missing:
                st.error("secrets 설정을 먼저 채운 뒤 다시 실행하세요.")
            elif absent:
                st.error(f"기준 데이터 파일이 없습니다: {', '.join(f.name for f in absent)}")
            else:
                with st.status("기준 데이터 INSERT 진행 중입니다…", expanded=True) as status:
                    results = insert_base_data(
                        project, api_key, mode.startswith("덮어쓰기"), clear
                    )
                    done = all(row.get("ok") for row in results)
                    status.update(
                        label=f"기준 데이터 INSERT {'완료' if done else '실패'}",
                        state="complete" if done else "error",
                    )
                st.session_state["adm_last_result"] = results

        if "adm_last_result" in st.session_state:
            _render_results(st.session_state["adm_last_result"])
