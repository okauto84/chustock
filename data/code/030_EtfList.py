"""NAVER finance API로 전체 ETF 종목과 구성종목을 수집해 03_etflist.json으로 저장한다.

구성종목은 NAVER가 공개하는 상위 10개(etfTop10MajorConstituentAssets)까지 제공된다.
"""

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

ETF_LIST_URL = "https://finance.naver.com/api/sise/etfItemList.nhn"
ETF_ANALYSIS_URL = "https://m.stock.naver.com/api/stock/{code}/etfAnalysis"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://m.stock.naver.com/",
    "Accept": "application/json",
}

CHUNK_SIZE = 50
SLEEP_SEC = 1  # API 부하 방지: 50건당 1초
MAX_RETRY = 3

DATA_DIR = Path(__file__).resolve().parents[1] / "stock"
SECTOR_FILE = DATA_DIR / "010_SectorList.json"
STOCK_FILE = DATA_DIR / "020_StockList.json"
OUT_FILE = DATA_DIR / "030_EtfList.json"

# 채권/통화/파생형 ETF는 업종 테마로 볼 수 없어 기타로 둔다.
NON_EQUITY_KEYWORDS = (
    "국채", "회사채", "특수채", "통안채", "채권", "금리", "CD", "달러", "엔화", "인버스",
    "레버리지", "커버드콜", "선물", "원유", "구리", "금현물", "골드", "머니마켓", "TDF", "만기",
)

# 구성종목으로 섹터를 판단할 수 없을 때(해외/채권/원자재 ETF 등) ETF명으로 보정한다.
ETF_NAME_RULES = [
    (("파운드리", "TSMC"), "semiconductor", "비메모리"),
    (("반도체", "필라델피아"), "semiconductor", "반도체 장비"),
    (("2차전지", "이차전지", "배터리"), "battery", "배터리셀"),
    (("원자력", "원전", "SMR"), "energy", "원자력"),
    (("태양광", "풍력", "신재생", "그린에너지", "수소", "친환경"), "energy", "신재생"),
    (("전력", "전선"), "energy", "전력기기"),
    (("바이오", "헬스케어", "제약", "의료", "비만", "치료제", "항암"), "bio", "바이오신약"),
    (("리츠", "REITs", "부동산"), "finance", "리츠"),
    (("은행", "금융", "증권", "보험"), "finance", "은행"),
    (("게임",), "it", "게임"),
    (("인터넷", "플랫폼", "커머스"), "it", "인터넷"),
    (("AI", "소프트웨어", "클라우드", "빅데이터", "테크", "IT", "양자"), "it", "SW/AI"),
    (("K-POP", "케이팝", "엔터", "미디어", "콘텐츠"), "culture", "엔터"),
    (("화장품", "뷰티"), "culture", "화장품"),
    (("조선", "해운"), "shipping", "조선"),
    (("자동차", "모빌리티", "전기차"), "automobile", "완성차"),
    (("방산", "우주", "항공"), "defense", "방위산업"),
    (("로봇", "로보", "기계"), "machine", "로봇"),
    (("철강",), "chemistry", "철강"),
    (("화학", "소재"), "chemistry", "화학"),
    (("건설", "인프라", "통신"), "infra", "건설"),
    (("소비재", "유통", "음식료", "여행", "레저"), "goods", "유통"),
    (("지주",), "company", "지주사"),
]


def fetch_json(url: str, encoding: str = "utf-8") -> dict:
    """네이버 API를 호출해 JSON을 반환한다. 실패 시 지수 백오프로 재시도한다."""
    last_error: Exception | None = None
    for attempt in range(MAX_RETRY):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.loads(response.read().decode(encoding, "replace"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
            time.sleep(2**attempt)
    raise RuntimeError(f"요청 실패: {url}") from last_error


def fetch_etf_list() -> list[dict]:
    """상장된 전체 ETF 목록을 가져온다."""
    payload = fetch_json(ETF_LIST_URL, encoding="euc-kr")
    return payload["result"]["etfItemList"]


def fetch_constituents(itemcode: str) -> list[dict]:
    """ETF 구성종목(NAVER 공개 범위인 상위 10종목)을 가져온다."""
    try:
        payload = fetch_json(ETF_ANALYSIS_URL.format(code=itemcode))
    except RuntimeError:
        return []
    return payload.get("etfTop10MajorConstituentAssets") or []


def to_weight(raw_value: str | None) -> float:
    try:
        return float(str(raw_value).replace("%", "").replace(",", ""))
    except ValueError:
        return 0.0


def classify(
    itemname: str,
    constituents: list[dict],
    sector_by_stock: dict[str, tuple[str, str]],
) -> tuple[str, str]:
    """구성종목의 섹터 비중으로 (sectorcode, sectorlist)를 판단한다."""
    sector_weight: dict[str, float] = {}
    detail_weight: dict[tuple[str, str], float] = {}
    for asset in constituents:
        matched = sector_by_stock.get(asset.get("itemCode", ""))
        if matched is None:
            continue
        weight = to_weight(asset.get("etfWeight"))
        sector_weight[matched[0]] = sector_weight.get(matched[0], 0.0) + weight
        detail_weight[matched] = detail_weight.get(matched, 0.0) + weight

    if sector_weight:
        sectorcode = max(sector_weight, key=sector_weight.get)
        details = {key: value for key, value in detail_weight.items() if key[0] == sectorcode}
        return max(details, key=details.get)

    if any(keyword in itemname for keyword in NON_EQUITY_KEYWORDS):
        return "etc", "기타"

    for keywords, sectorcode, sectorlist in ETF_NAME_RULES:
        if any(keyword in itemname for keyword in keywords):
            return sectorcode, sectorlist

    return "etc", "기타"


def main() -> None:
    sectors = json.loads(SECTOR_FILE.read_text(encoding="utf-8"))
    sector_by_code = {item["sectorcode"]: item for item in sectors}
    stocks = json.loads(STOCK_FILE.read_text(encoding="utf-8"))
    sector_by_stock = {row["itemcode"]: (row["sectorcode"], row["sectorlist"]) for row in stocks}

    print("[1/3] ETF 목록 수집")
    etfs = fetch_etf_list()
    print(f"  ETF {len(etfs)}건")

    print("[2/3] ETF 구성종목 수집")
    constituents_by_code: dict[str, list[dict]] = {}
    for index, etf in enumerate(etfs, start=1):
        constituents_by_code[etf["itemcode"]] = fetch_constituents(etf["itemcode"])
        if index % CHUNK_SIZE == 0:
            print(f"  {index}/{len(etfs)} 처리")
            time.sleep(SLEEP_SEC)

    print("[3/3] 섹터 분류 및 저장")
    result = []
    for etf in etfs:
        itemcode = etf["itemcode"]
        itemname = etf["itemname"]
        constituents = constituents_by_code[itemcode]
        sectorcode, sectorlist = classify(itemname, constituents, sector_by_stock)
        sector = sector_by_code[sectorcode]
        if sectorlist not in sector["sectorlist"]:
            raise ValueError(f"{itemname}: '{sectorlist}'는 {sectorcode} 하위 분류가 아니다")
        result.append(
            {
                "itemcode": itemcode,
                "itemname": itemname,
                "stock": "ETF",
                "itemlist": [asset["itemName"] for asset in constituents],
                "sectorcode": sectorcode,
                "sector": sector["sector"],
                "sectorlist": sectorlist,
            }
        )

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"created: {OUT_FILE} ({len(result)}건)")


if __name__ == "__main__":
    main()
