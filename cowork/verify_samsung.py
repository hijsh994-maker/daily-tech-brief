# -*- coding: utf-8 -*-
"""
verify_samsung.py - 삼성 섹션 링크 오매칭을 걷어낸다.

## 왜 필요한가 (2026-07-31 실측)
삼성 텔레그램 섹션은 원문 링크 없이 '■ 요약 한 문장'만 온다. samsung_resolve.py
가 그 문장에서 변별토큰을 뽑아 구글뉴스·네이버에서 원문을 역추적하는데,
링크가 붙은 것 중 **절반 가까이가 다른 기사**였다.

    07-30  링크 5건 중 맞는 것 2건 (40%)
    07-31  링크 8건 중 맞는 것 5건 (63%)

실제 오매칭 예:
    요약 "AWS, 오리 헌슈타트 영입"   -> 기사 "아마존 분기 매출 2000억달러 돌파"
    요약 "메타, 7,000억 달러 계약"   -> 기사 "메타-블랙록 140억달러 데이터센터"
    요약 "Nscale, Investor Day/상장" -> 기사 "Nscale, Anyscale 인수"
공통 기업명 하나만 겹쳐도 매칭되는 것이 원인이다. 토큰 기반으로는 '같은 회사의
다른 사건'을 구분할 수 없다 - 이건 의미 판단이라 LLM 이 맡는 편이 낫다.

## 왜 그냥 두면 안 되는가
오매칭은 링크 하나 틀리고 끝나지 않는다. 세 가지가 연쇄로 망가진다.
  1. 엉뚱한 클러스터에 병합되어 그 기사가 '사람큐레이션+3 / 교차소스+2' 를
     부당하게 받는다. 07-31 Nscale 기사는 이 +5 가 없었으면 3점으로 임계(6) 미달인데
     오매칭 덕에 후보로 올라와 실제 브리프에까지 실렸다.
  2. 진짜 삼성 뉴스(메타 7000억 계약 등)는 조용히 사라진다.
  3. 브리프에 '요약과 다른 기사 링크'가 실릴 수 있다.

## 어떻게 고치는가
링크를 억지로 고치지 않는다. 판정이 어긋난 항목은 **링크 없는 상태로 되돌린다**.
링크 없는 삼성 항목은 원래도 정상 경로다(오늘 11건 중 3건이 그랬다). 잘못된 링크보다
링크 없음이 낫다 - 후자는 브리프에서 자동 제외되지만 전자는 오보가 된다.

제목까지 함께 지우는 것이 핵심이다. url 만 지우면 잘못된 제목이 남아 refine 이
여전히 같은 클러스터로 병합해 버린다.

사용:
  python3 cowork/verify_samsung.py --date 2026-07-31 --list
  python3 cowork/verify_samsung.py --date 2026-07-31 --reject 3,6,10
"""
import argparse
import datetime
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(BASE, "out")


def load(day):
    p = os.path.join(OUTDIR, "tier0-%s.json" % day)
    if not os.path.exists(p):
        print("[오류] %s 없음. tier0 를 먼저 실행" % p)
        sys.exit(1)
    return p, json.load(open(p, encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--list", action="store_true",
                    help="판정 대상(링크가 붙은 삼성 항목)을 번호와 함께 출력")
    ap.add_argument("--reject", default="",
                    help="오매칭 번호. 쉼표구분. 예: 3,6,10")
    a = ap.parse_args()
    day = (datetime.date.fromisoformat(a.date) if a.date
           else datetime.date.today()).isoformat()
    path, data = load(day)

    # 번호는 '링크가 붙은 삼성 항목' 안에서의 1-based 순서다.
    linked = [x for x in data["items"]
              if x.get("src") == "samsung" and x.get("url")]

    if a.list or not a.reject:
        print("[verify] %s 삼성 링크부여 %d건 - 요약과 기사가 같은 사건인지 판정하세요"
              % (day, len(linked)))
        for i, x in enumerate(linked, 1):
            print()
            print(" [%d] 요약 : %s" % (i, x.get("summary", "")))
            print("     기사 : %s" % (x.get("title") or ""))
            print("     매체 : %s | %s" % (x.get("outlet"), x.get("url")))
        if not a.reject:
            return

    idx = set()
    for t in a.reject.split(","):
        t = t.strip()
        if t.isdigit():
            idx.add(int(t))
    bad = [i for i in idx if not (1 <= i <= len(linked))]
    if bad:
        print("[오류] 범위를 벗어난 번호: %s (1~%d)" % (bad, len(linked)))
        sys.exit(1)

    n = 0
    for i in sorted(idx):
        x = linked[i - 1]
        print("[verify] %d번 해제: %s" % (i, (x.get("title") or "")[:56]))
        # 제목까지 지워야 refine 이 엉뚱한 클러스터로 병합하지 않는다.
        x["url"] = None
        x["title"] = None
        x["url_via"] = None
        x["confidence"] = "unresolved"
        x["rejected_by"] = "cowork/verify_samsung"
        n += 1

    json.dump(data, open(path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    rest = sum(1 for x in data["items"]
               if x.get("src") == "samsung" and x.get("url"))
    print("[verify] %d건 해제 완료. 삼성 링크 %d -> %d건. 저장: %s"
          % (n, len(linked), rest, path))


if __name__ == "__main__":
    main()
