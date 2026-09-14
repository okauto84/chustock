import json
from pathlib import Path

SECTORS = [
    {
        "sectorCode": "semiconductor",
        "sectorName": "반도체",
        "sectorItems": ["메모리", "반도체 소재", "반도체 장비", "비메모리", "팹리스", "테스트소켓"],
    },
    {
        "sectorCode": "energy",
        "sectorName": "전력/에너지",
        "sectorItems": ["원자력", "전력기기", "신재생"],
    },
    {
        "sectorCode": "infra",
        "sectorName": "인프라",
        "sectorItems": ["건설", "통신", "유틸리티", "건자재"],
    },
    {
        "sectorCode": "it",
        "sectorName": "IT/플랫폼",
        "sectorItems": ["SW/AI", "인터넷", "플랫폼", "게임"],
    },
    {
        "sectorCode": "bio",
        "sectorName": "바이오",
        "sectorItems": ["바이오신약", "제약", "의료기기", "CDMO"],
    },
    {
        "sectorCode": "chemistry",
        "sectorName": "화학/소재",
        "sectorItems": ["정유", "화학", "철강", "비철"],
    },
    {
        "sectorCode": "shipping",
        "sectorName": "조선/해운",
        "sectorItems": ["조선", "조선기자재", "해운"],
    },
    {
        "sectorCode": "battery",
        "sectorName": "2차전지",
        "sectorItems": ["배터리셀", "양극재", "전지장비", "음극재", "소재"],
    },
    {
        "sectorCode": "machine",
        "sectorName": "기계",
        "sectorItems": ["로봇", "자동화", "산업기계"],
    },
    {
        "sectorCode": "finance",
        "sectorName": "금융",
        "sectorItems": ["은행", "보험", "증권", "리츠", "부동산"],
    },
    {
        "sectorCode": "culture",
        "sectorName": "K-컬처",
        "sectorItems": ["화장품", "미용기기", "엔터", "미디어"],
    },
    {
        "sectorCode": "company",
        "sectorName": "지주사",
        "sectorItems": ["지주사"],
    },
    {
        "sectorCode": "goods",
        "sectorName": "소비재",
        "sectorItems": ["유통", "음식료", "여행", "레저", "패션", "의류"],
    },
    {
        "sectorCode": "automobile",
        "sectorName": "자동차",
        "sectorItems": ["자동차부품", "완성차", "타이어"],
    },
    {
        "sectorCode": "defense",
        "sectorName": "방산",
        "sectorItems": ["방위산업", "우주항공"],
    },
    {
        "sectorCode": "etc",
        "sectorName": "기타",
        "sectorItems": ["기타"],
    },
]


def main() -> None:
    out_path = Path(__file__).resolve().parents[1] / "stock" / "010_SectorList.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(SECTORS, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"created: {out_path}")


if __name__ == "__main__":
    main()
