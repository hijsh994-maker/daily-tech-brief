# -*- coding: utf-8 -*-
"""
samsung_seed.py - 삼성 섹션 요약문을 tier0 json 에 '링크 없는 껍데기'로 심는다.

## 왜 필요한가
ingest_tier0.do_samsung() 은 구글뉴스로 헤드라인을 역추적(discover)하는데
한 건당 3초 안팎이 걸려 9건이면 25초를 넘긴다. tier0 전체가 45초 제한을
초과해 죽는 원인이었다(2026-08-03 실측).

그런데 그 역추적 결과는 어차피 쓰지 않는다. 바로 다음 단계인
samsung_match.py(dump -> search -> apply)에서 LLM 이 질의를 만들고 후보를
직접 골라 다시 붙이기 때문이다. 기계 역추적 정확도는 40~63%로 낮았고
LLM 경로가 이를 대체하려고 만들어졌다.

그래서 tier0 단계에서는 '요약문 자리만' 잡아둔다. 네트워크 호출이 없어
1초 미만이다. samsung_match.py --dump 가 이 껍데기를 읽어 목록을 낸다.

사용:
  python3 cowork/samsung_seed.py --date 2026-08-03 --file log-2026-08-03.md
"""
import argparse
import datetime
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import samsung_resolve as SR   # noqa: E402

OUTDIR = os.path.join(BASE, "out")
RAW = "https://raw.githubusercontent.com/"  # do_samsung 과 동일한 로그 소스


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--file", help="로컬 로그 파일 경로")
    a = ap.parse_args()

    day = datetime.date.fromisoformat(a.date).isoformat()
    p = os.path.join(OUTDIR, "tier0-%s.json" % day)
    if not os.path.exists(p):
        print("[samsung-seed] %s 없음. tier0 를 먼저 실행" % p)
        return

    data = json.load(open(p, encoding="utf-8"))
    if any(x.get("src") == "samsung" for x in data["items"]):
        print("[samsung-seed] 이미 삼성 항목이 있음. 건너뜀")
        return

    if not a.file or not os.path.exists(a.file):
        print("[samsung-seed] 로그 파일이 없어 건너뜀 (--file)")
        return

    md = open(a.file, encoding="utf-8").read()
    bullets = SR.parse_samsung(md)
    if not bullets:
        print("[samsung-seed] 삼성 섹션 0건 (헤더 없음/포맷 변경 가능성)")
        return

    for s in bullets:
        data["items"].append({
            "src": "samsung", "summary": s, "title": None, "outlet": None,
            "pub_date": None, "cluster": 1, "topic": None,
            "url": None, "url_via": None,
            "confidence": "unresolved", "seen_in": ["samsung"],
        })
    json.dump(data, open(p, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("[samsung-seed] 삼성 요약문 %d건 주입 (링크는 samsung-search/apply 에서)"
          % len(bullets))


if __name__ == "__main__":
    main()
