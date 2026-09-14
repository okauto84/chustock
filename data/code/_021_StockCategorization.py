"""020_StockList.json을 sector·sectorlist별로 그룹핑해 itemcode 리스트를 저장한다."""

import json
from collections import defaultdict
from pathlib import Path

IN_FILE = Path(__file__).resolve().parents[1] / "stock" / "020_StockList.json"
OUT_FILE = Path(__file__).resolve().parents[1] / "stock" / "021_StockCategorization.json"


def main() -> None:
    with IN_FILE.open(encoding="utf-8") as f:
        stocks = json.load(f)

    # sector -> sectorlist -> itemcode[] (입력 순서 유지)
    grouped: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for row in stocks:
        sector = row.get("sector") or "기타"
        sectorlist = row.get("sectorlist") or "기타"
        itemcode = row.get("itemcode")
        if not itemcode:
            continue
        grouped[sector][sectorlist].append(itemcode)

    result = {
        sector: {sectorlist: codes for sectorlist, codes in sectorlists.items()}
        for sector, sectorlists in grouped.items()
    }

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")

    sector_count = len(result)
    sectorlist_count = sum(len(v) for v in result.values())
    item_count = sum(len(codes) for sl in result.values() for codes in sl.values())
    print(f"created: {OUT_FILE}")
    print(f"sectors={sector_count}, sectorlists={sectorlist_count}, items={item_count}")


if __name__ == "__main__":
    main()
