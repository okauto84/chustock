"""NAVER finance API로 코스피/코스닥 전 종목과 ETF를 수집해 020_StockList.json으로 저장한다.

ETF 구성종목은 NAVER가 공개하는 상위 10종목까지 제공된다.
"""

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

API_BASE = "https://m.stock.naver.com/api"
ETF_LIST_URL = "https://finance.naver.com/api/sise/etfItemList.nhn"
ETF_ANALYSIS_URL = f"{API_BASE}/stock/{{code}}/etfAnalysis"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://m.stock.naver.com/",
    "Accept": "application/json",
}

PAGE_SIZE = 50
SLEEP_SEC = 1  # API 부하 방지: 50건당 1초
MAX_RETRY = 3

SECTOR_FILE = Path(__file__).resolve().parents[1] / "stock" / "010_SectorList.json"
OUT_FILE = Path(__file__).resolve().parents[1] / "stock" / "020_StockList.json"

# 네이버 업종명 -> (sectorCode, sectorItem)
INDUSTRY_MAP = {
    "반도체와반도체장비": ("semiconductor", "반도체 장비"),
    "디스플레이장비및부품": ("semiconductor", "반도체 장비"),
    "디스플레이패널": ("semiconductor", "반도체 소재"),
    "전기장비": ("energy", "전력기기"),
    "전기제품": ("energy", "전력기기"),
    "에너지장비및서비스": ("energy", "신재생"),
    "건설": ("infra", "건설"),
    "운송인프라": ("infra", "건설"),
    "도로와철도운송": ("infra", "건설"),
    "건축제품": ("infra", "건자재"),
    "건축자재": ("infra", "건자재"),
    "전기유틸리티": ("infra", "유틸리티"),
    "가스유틸리티": ("infra", "유틸리티"),
    "복합유틸리티": ("infra", "유틸리티"),
    "다각화된통신서비스": ("infra", "통신"),
    "무선통신서비스": ("infra", "통신"),
    "통신장비": ("infra", "통신"),
    "소프트웨어": ("it", "SW/AI"),
    "IT서비스": ("it", "SW/AI"),
    "컴퓨터와주변기기": ("it", "SW/AI"),
    "사무용전자제품": ("it", "SW/AI"),
    "전자제품": ("it", "SW/AI"),
    "전자장비와기기": ("it", "SW/AI"),
    "핸드셋": ("it", "SW/AI"),
    "양방향미디어와서비스": ("it", "인터넷"),
    "인터넷과카탈로그소매": ("it", "인터넷"),
    "게임엔터테인먼트": ("it", "게임"),
    "제약": ("bio", "제약"),
    "건강관리업체및서비스": ("bio", "제약"),
    "생물공학": ("bio", "바이오신약"),
    "생명과학도구및서비스": ("bio", "CDMO"),
    "건강관리장비와용품": ("bio", "의료기기"),
    "건강관리기술": ("bio", "의료기기"),
    "석유와가스": ("chemistry", "정유"),
    "화학": ("chemistry", "화학"),
    "포장재": ("chemistry", "화학"),
    "종이와목재": ("chemistry", "화학"),
    "철강": ("chemistry", "철강"),
    "비철금속": ("chemistry", "비철"),
    "조선": ("shipping", "조선"),
    "해운사": ("shipping", "해운"),
    "항공화물운송과물류": ("shipping", "해운"),
    "기계": ("machine", "산업기계"),
    "상업서비스와공급품": ("machine", "산업기계"),
    "은행": ("finance", "은행"),
    "카드": ("finance", "은행"),
    "생명보험": ("finance", "보험"),
    "손해보험": ("finance", "보험"),
    "증권": ("finance", "증권"),
    "기타금융": ("finance", "증권"),
    "창업투자": ("finance", "증권"),
    "부동산": ("finance", "부동산"),
    "화장품": ("culture", "화장품"),
    "방송과엔터테인먼트": ("culture", "미디어"),
    "출판": ("culture", "미디어"),
    "광고": ("culture", "미디어"),
    "복합기업": ("company", "지주사"),
    "백화점과일반상점": ("goods", "유통"),
    "식품과기본식료품소매": ("goods", "유통"),
    "전문소매": ("goods", "유통"),
    "판매업체": ("goods", "유통"),
    "무역회사와판매업체": ("goods", "유통"),
    "가구": ("goods", "유통"),
    "가정용품": ("goods", "유통"),
    "가정용기기와용품": ("goods", "유통"),
    "문구류": ("goods", "유통"),
    "식품": ("goods", "음식료"),
    "음료": ("goods", "음식료"),
    "담배": ("goods", "음식료"),
    "항공사": ("goods", "여행"),
    "호텔,레스토랑,레저": ("goods", "레저"),
    "레저용장비와제품": ("goods", "레저"),
    "다각화된소비자서비스": ("goods", "레저"),
    "교육서비스": ("goods", "레저"),
    "섬유,의류,신발,호화품": ("goods", "의류"),
    "자동차": ("automobile", "완성차"),
    "자동차부품": ("automobile", "자동차부품"),
    "우주항공과국방": ("defense", "방위산업"),
    "기타": ("etc", "기타"),
}

# 업종 분류만으로는 드러나지 않는 테마. 업종 분류보다 우선한다.
THEME_RULES = [
    (("스팩", "기업인수목적"), "etc", "기타"),
    (("리츠",), "finance", "리츠"),
    (("원자력", "원전"), "energy", "원자력"),
    (("태양광", "솔라", "풍력", "수소", "그린에너지"), "energy", "신재생"),
    (("로봇", "로보"), "machine", "로봇"),
    (("양극재",), "battery", "양극재"),
    (("음극재",), "battery", "음극재"),
    (("배터리",), "battery", "배터리셀"),
    (("항공우주", "우주항공"), "defense", "우주항공"),
    (("방산", "디펜스"), "defense", "방위산업"),
    (("타이어",), "automobile", "타이어"),
    (("홀딩스", "지주"), "company", "지주사"),
]

# 섹터가 정해진 뒤 세부 분류를 보정한다.
DETAIL_RULES = {
    "semiconductor": [
        (("소켓", "테스트"), "테스트소켓"),
        (("소재", "머티리얼", "마이크로", "케미칼"), "반도체 소재"),
        (("반도체", "장비", "이엔지", "엔지니어링"), "반도체 장비"),
    ],
    "bio": [
        (("바이오로직스", "CDMO"), "CDMO"),
        (("제약", "약품", "파마"), "제약"),
        (("의료", "덴탈", "임플란트", "메디칼", "메디컬", "헬스케어"), "의료기기"),
        (("바이오",), "바이오신약"),
    ],
    "it": [
        (("게임", "게임즈"), "게임"),
        (("플랫폼", "커머스", "쇼핑"), "플랫폼"),
        (("인터넷", "네트웍", "네트워크"), "인터넷"),
    ],
    "culture": [
        (("엔터", "뮤직", "이엔엠"), "엔터"),
        (("화장품", "코스메", "코스맥스", "뷰티"), "화장품"),
    ],
    "goods": [
        (("투어", "여행", "관광"), "여행"),
        (("패션", "어패럴", "모드"), "패션"),
        (("식품", "제과", "유업", "농산"), "음식료"),
    ],
    "shipping": [
        (("해운", "상선", "로지스"), "해운"),
        (("기자재", "엔진"), "조선기자재"),
    ],
    "chemistry": [
        (("정유", "오일"), "정유"),
        (("제철", "제강", "철강", "특수강", "스틸"), "철강"),
        (("비철", "아연", "알루미", "구리"), "비철"),
    ],
    "machine": [
        (("자동화", "오토", "메카"), "자동화"),
    ],
    "infra": [
        (("텔레콤", "통신", "브로드밴드"), "통신"),
        (("시멘트", "레미콘", "유리", "건자재"), "건자재"),
        (("건설", "건산", "산업개발", "토건"), "건설"),
    ],
    "finance": [
        (("증권", "투자", "캐피탈"), "증권"),
        (("화재", "해상", "생명", "보험"), "보험"),
        (("은행", "금융"), "은행"),
    ],
    "battery": [
        (("전자재료", "신소재", "소재"), "소재"),
        (("장비", "기술", "이앤에스"), "전지장비"),
    ],
}

# 업종/이름 규칙으로 잡히지 않는 대표 종목을 직접 지정한다.
CODE_OVERRIDES = {
    "005930": ("semiconductor", "메모리"),  # 삼성전자
    "000660": ("semiconductor", "메모리"),  # SK하이닉스
    "042700": ("semiconductor", "비메모리"),  # 한미반도체
    "000990": ("semiconductor", "비메모리"),  # DB하이텍
    "403870": ("semiconductor", "팹리스"),  # HPSP
    "108320": ("semiconductor", "팹리스"),  # LX세미콘
    "094170": ("semiconductor", "팹리스"),  # 동운아나텍
    "058470": ("semiconductor", "테스트소켓"),  # 리노공업
    "095340": ("semiconductor", "테스트소켓"),  # ISC
    "131290": ("semiconductor", "테스트소켓"),  # 티에스이
    "357780": ("semiconductor", "반도체 소재"),  # 솔브레인
    "034020": ("energy", "원자력"),  # 두산에너빌리티
    "052690": ("energy", "원자력"),  # 한전기술
    "051600": ("energy", "원자력"),  # 한전KPS
    "083650": ("energy", "원자력"),  # 비에이치아이
    "010120": ("energy", "전력기기"),  # LS ELECTRIC
    "006400": ("battery", "배터리셀"),  # 삼성SDI
    "373220": ("battery", "배터리셀"),  # LG에너지솔루션
    "247540": ("battery", "양극재"),  # 에코프로비엠
    "086520": ("battery", "양극재"),  # 에코프로
    "066970": ("battery", "양극재"),  # 엘앤에프
    "003670": ("battery", "양극재"),  # 포스코퓨처엠
    "005070": ("battery", "양극재"),  # 코스모신소재
    "020150": ("battery", "음극재"),  # 롯데에너지머티리얼즈
    "011790": ("battery", "음극재"),  # SKC
    "078600": ("battery", "음극재"),  # 대주전자재료
    "348370": ("battery", "소재"),  # 엔켐
    "093370": ("battery", "소재"),  # 후성
    "278280": ("battery", "소재"),  # 천보
    "121600": ("battery", "소재"),  # 나노신소재
    "393890": ("battery", "소재"),  # 더블유씨피
    "089980": ("battery", "소재"),  # 상아프론테크
    "365340": ("battery", "소재"),  # 성일하이텍
    "107600": ("battery", "소재"),  # 새빗켐
    "137400": ("battery", "전지장비"),  # 피엔티
    "005490": ("chemistry", "철강"),  # POSCO홀딩스
    "035420": ("it", "플랫폼"),  # NAVER
    "035720": ("it", "플랫폼"),  # 카카오
    "036570": ("it", "게임"),  # NC
    "251270": ("it", "게임"),  # 넷마블
    "259960": ("it", "게임"),  # 크래프톤
    "005380": ("automobile", "완성차"),  # 현대차
    "000270": ("automobile", "완성차"),  # 기아
    "012330": ("automobile", "자동차부품"),  # 현대모비스
    "012450": ("defense", "방위산업"),  # 한화에어로스페이스
    "079550": ("defense", "방위산업"),  # LIG디펜스앤에어로스페이스
    "047810": ("defense", "우주항공"),  # 한국항공우주
    "064350": ("defense", "방위산업"),  # 현대로템
    "042660": ("shipping", "조선"),  # 한화오션
    "009540": ("shipping", "조선"),  # HD한국조선해양
    "329180": ("shipping", "조선"),  # HD현대중공업
    "010140": ("shipping", "조선"),  # 삼성중공업
    "100090": ("shipping", "조선기자재"),  # SK오션플랜트
    "011200": ("shipping", "해운"),  # HMM
    "207940": ("bio", "CDMO"),  # 삼성바이오로직스
    "068270": ("bio", "바이오신약"),  # 셀트리온
    "128940": ("bio", "제약"),  # 한미약품
    "090430": ("culture", "화장품"),  # 아모레퍼시픽
    "192820": ("culture", "화장품"),  # 코스맥스
    "352820": ("culture", "엔터"),  # 하이브
    "041510": ("culture", "엔터"),  # 에스엠
    "214150": ("culture", "미용기기"),  # 클래시스
    "336570": ("culture", "미용기기"),  # 원텍
}

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


def fetch_paged(url_template: str, key: str) -> list[dict]:
    """페이지 단위(50건)로 전체 목록을 수집한다."""
    items: list[dict] = []
    page = 1
    while True:
        payload = fetch_json(url_template.format(page=page, size=PAGE_SIZE))
        chunk = payload.get(key) or []
        items.extend(chunk)
        if len(chunk) < PAGE_SIZE:
            break
        page += 1
        time.sleep(SLEEP_SEC)
    return items


def fetch_market_stocks() -> list[dict]:
    """코스피/코스닥의 개별 주식 종목을 시가총액 순으로 수집한다."""
    stocks: list[dict] = []
    for market in ("KOSPI", "KOSDAQ"):
        url = f"{API_BASE}/stocks/marketValue/{market}?page={{page}}&pageSize={{size}}"
        rows = fetch_paged(url, "stocks")
        stocks.extend(row for row in rows if row.get("stockEndType") == "stock")
        print(f"  {market}: {len(rows)}건 수신")
    return stocks


def fetch_industry_by_code() -> dict[str, str]:
    """종목코드 -> 네이버 업종명 매핑을 만든다."""
    groups = fetch_paged(f"{API_BASE}/stocks/industry?page={{page}}&pageSize={{size}}", "groups")
    industry_by_code: dict[str, str] = {}
    for index, group in enumerate(groups, start=1):
        url = f"{API_BASE}/stocks/industry/{group['no']}?page={{page}}&pageSize={{size}}"
        for row in fetch_paged(url, "stocks"):
            industry_by_code[row["itemCode"]] = group["name"]
        if index % 10 == 0:
            print(f"  업종 {index}/{len(groups)} 처리")
        time.sleep(SLEEP_SEC)
    return industry_by_code


def fetch_etf_list() -> list[dict]:
    """상장된 전체 ETF 목록을 가져온다."""
    payload = fetch_json(ETF_LIST_URL, encoding="euc-kr")
    return payload["result"]["etfItemList"]


def fetch_etf_constituents(etf_code: str) -> list[dict]:
    """ETF 구성종목(NAVER 공개 범위인 상위 10종목)을 가져온다."""
    try:
        payload = fetch_json(ETF_ANALYSIS_URL.format(code=etf_code))
    except RuntimeError:
        return []
    return payload.get("etfTop10MajorConstituentAssets") or []


def to_weight(raw_value: str | None) -> float:
    try:
        return float(str(raw_value).replace("%", "").replace(",", ""))
    except ValueError:
        return 0.0


def match_detail(sector_code: str, stock_name: str, default: str) -> str:
    for keywords, detail in DETAIL_RULES.get(sector_code, []):
        if any(keyword in stock_name for keyword in keywords):
            return detail
    return default


def classify(stock_code: str, stock_name: str, industry: str | None) -> tuple[str, str]:
    """종목코드/종목명/업종으로 (sectorCode, sectorItem)을 판단한다."""
    if stock_code in CODE_OVERRIDES:
        return CODE_OVERRIDES[stock_code]

    for keywords, sector_code, detail in THEME_RULES:
        if any(keyword in stock_name for keyword in keywords):
            return sector_code, detail

    if industry in INDUSTRY_MAP:
        sector_code, detail = INDUSTRY_MAP[industry]
        return sector_code, match_detail(sector_code, stock_name, detail)

    return "etc", "기타"


def classify_etf(
    etf_name: str,
    constituents: list[dict],
    sector_by_stock: dict[str, tuple[str, str]],
) -> tuple[str, str]:
    """구성종목의 섹터 비중으로 (sectorCode, sectorItem)을 판단한다."""
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
        sector_code = max(sector_weight, key=sector_weight.get)
        details = {key: value for key, value in detail_weight.items() if key[0] == sector_code}
        return max(details, key=details.get)

    if any(keyword in etf_name for keyword in NON_EQUITY_KEYWORDS):
        return "etc", "기타"

    for keywords, sector_code, sector_item in ETF_NAME_RULES:
        if any(keyword in etf_name for keyword in keywords):
            return sector_code, sector_item

    return "etc", "기타"


def main() -> None:
    sectors = json.loads(SECTOR_FILE.read_text(encoding="utf-8"))
    sector_by_code = {item["sectorCode"]: item for item in sectors}

    print("[1/5] 시가총액 기준 전 종목 수집")
    stocks = fetch_market_stocks()
    print(f"  개별 종목 {len(stocks)}건")

    print("[2/5] 업종 정보 수집")
    industry_by_code = fetch_industry_by_code()
    print(f"  업종 매핑 {len(industry_by_code)}건")

    print("[3/5] 섹터 분류")
    classified = {
        stock["itemCode"]: classify(
            stock["itemCode"], stock["stockName"], industry_by_code.get(stock["itemCode"])
        )
        for stock in stocks
    }
    # 우선주는 업종 정보가 없는 경우가 많아 보통주 분류를 따른다.
    for stock_code in classified:
        base_code = stock_code[:5] + "0"
        if stock_code[5] != "0" and base_code in classified:
            classified[stock_code] = classified[base_code]

    def validate(name: str, sector_code: str, sector_item: str) -> None:
        sector = sector_by_code[sector_code]
        if sector_item not in sector["sectorItems"]:
            raise ValueError(f"{name}: '{sector_item}'는 {sector_code} 하위 분류가 아니다")

    result = []
    for stock in stocks:
        stock_code = stock["itemCode"]
        stock_name = stock["stockName"]
        sector_code, sector_item = classified[stock_code]
        validate(stock_name, sector_code, sector_item)
        result.append(
            {
                "stockCode": stock_code,
                "stockName": stock_name,
                "stockItem": stock["stockExchangeType"]["code"],
                "sectorCode": sector_code,
                "sectorItem": sector_item,
            }
        )

    print("[4/5] ETF 목록 및 구성종목 수집")
    etfs = fetch_etf_list()
    print(f"  ETF {len(etfs)}건")
    constituents_by_code: dict[str, list[dict]] = {}
    for index, etf in enumerate(etfs, start=1):
        constituents_by_code[etf["itemcode"]] = fetch_etf_constituents(etf["itemcode"])
        if index % PAGE_SIZE == 0:
            print(f"  {index}/{len(etfs)} 처리")
            time.sleep(SLEEP_SEC)

    print("[5/5] ETF 섹터 분류 및 저장")
    sector_by_stock = {row["stockCode"]: (row["sectorCode"], row["sectorItem"]) for row in result}
    for etf in etfs:
        etf_code = etf["itemcode"]
        etf_name = etf["itemname"]
        constituents = constituents_by_code[etf_code]
        sector_code, sector_item = classify_etf(etf_name, constituents, sector_by_stock)
        validate(etf_name, sector_code, sector_item)
        result.append(
            {
                "stockCode": etf_code,
                "stockName": etf_name,
                "stockItem": "ETF",
                "stockItems": [asset["itemName"] for asset in constituents],
                "sectorCode": sector_code,
                "sectorItem": sector_item,
            }
        )

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"created: {OUT_FILE} (종목 {len(stocks)}건 + ETF {len(etfs)}건 = {len(result)}건)")


if __name__ == "__main__":
    main()
