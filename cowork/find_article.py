#!/usr/bin/env python3
"""링크 미확보·본문 미확보 '고' 후보를 키워드로 다시 찾는다. [2026-10-27 신설]

[왜] 10-27 사용자 지적 — MS 6만명 희망퇴직·도요타 휴머노이드·브로드컴 630억달러
  ·LG 로봇 공장 투입 등을 "검색으로 못 찾아서" '중'에 두었다. 원인을 재 보니
  네 건 모두 **네이버 뉴스 API 키워드 검색 1회**로 국내 기사가 바로 나왔다.
    - `run.sh links` 는 후보 헤드라인과 제목 유사도 0.75 이상인 같은 기사만 채택한다.
      AI매터스처럼 네이버에 없는 매체의 제목은 다른 매체 표현과 달라 '못찾음'.
    - 판정 단계에서 쓴 WebSearch 는 미국 기준 검색이라 국내 기사가 거의 안 나온다.
    - WebFetch 는 대화에 나온 주소만 열 수 있어 bash 에서 얻은 주소를 못 연다.
  그래서 '같은 사건을 다룬 다른 매체 기사'로 바꿔 싣는 길(대체 링크)이 막혀 있었다.

[무엇을] 키워드(주체·숫자·핵심 낱말)로 네이버 뉴스 API 를 부르고, 발행일 창 안의
  결과를 매체·제목·원문 주소·네이버 사본 주소와 함께 찍는다. --body 를 주면 상위
  N건의 본문 앞부분을 body_rescore.fetch(네이버 사본 폴백 포함)로 받아 보여 주고
  bodycache 에 넣는다 — 그 주소를 verdict 후보 링크로 바꾸면 newfact·bodygo 가 통과한다.

  bash cowork/run.sh find "브로드컴 앤트로픽 630억"            # 목록
  bash cowork/run.sh find "도요타 휴머노이드" --body 2          # 상위 2건 본문
  bash cowork/run.sh find "MS 희망퇴직 6만명" --days 2
"""
import argparse
import datetime
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "cowork"))
import naver_api                      # noqa: E402
import body_rescore                   # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="+")
    ap.add_argument("--date", default=None)
    ap.add_argument("--days", type=int, default=3, help="발행일 창(오늘 포함 N일)")
    ap.add_argument("--body", type=int, default=0, help="상위 N건 본문 받기")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--gnews", action="store_true", help="구글뉴스(영문 포함)로도 찾기 — 질의는 헤드라인 그대로")
    ap.add_argument("--gnews-threshold", type=float, default=0.6)
    a = ap.parse_args()
    q = " ".join(a.query)
    day = datetime.date.fromisoformat(a.date) if a.date else datetime.date.today()
    lo = day - datetime.timedelta(days=a.days - 1)
    try:
        items = naver_api.search(q, display=30, sort="sim")
    except Exception as e:
        print("[find] 네이버 검색 실패: %s" % e)
        sys.exit(1)
    rows = []
    for it in items or []:
        pd = naver_api._pubdate(it.get("pubDate"))
        if pd and not (lo <= pd <= day):
            continue
        rows.append(dict(title=naver_api.strip_tags(it.get("title", "")),
                         url=it.get("originallink") or it.get("link"),
                         naver=it.get("link") if "n.news.naver.com" in (it.get("link") or "") else "",
                         date=str(pd or "")))
    print("[find] '%s' — 발행일 %s~%s 안 %d건 (전체 %d건)" % (q, lo, day, len(rows), len(items or [])))
    for i, r in enumerate(rows[:a.n], 1):
        print("  %2d. %s  [%s]\n      원문 %s%s" % (i, r["title"], r["date"], r["url"],
              ("\n      네이버 " + r["naver"]) if r["naver"] else ""))
    if a.body and rows:
        cpath = os.path.join(BASE, "out", "bodycache-%s.json" % day)
        cache = json.load(open(cpath, encoding="utf-8")) if os.path.exists(cpath) else {}
        for r in rows[:a.body]:
            body, err = body_rescore.fetch(r["url"], r["title"])
            print("\n[find] 본문 %s | %s" % ("OK %d자" % len(body) if body else "실패(%s)" % err, r["url"]))
            if body:
                print("      " + body[:500])
                cache[r["url"]] = body
        with open(cpath, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        print("\n[find] bodycache 반영: %s" % cpath)
    # [2026-10-27] 네이버에 없는 해외 영문 기사는 구글뉴스 주소 해독으로 찾는다(--gnews 또는 결과 0건).
    if a.gnews or not rows:
        import gnews_link
        try:
            g = gnews_link.resolve(q, "", lo, day, threshold=a.gnews_threshold)
        except Exception as e:
            g = None
            print("[find] 구글뉴스 해독 실패: %s" % str(e)[:60])
        if g:
            print("[find] 구글뉴스: %s (%s, 유사도 %s)\n      원문 %s" % (q, g["outlet"], g["score"], g["url"]))
            if a.body:
                body, err = body_rescore.fetch(g["url"], q)
                print("      본문 %s" % ("OK %d자 " % len(body) + body[:300] if body else "실패(%s)" % err))
                if body:
                    cpath = os.path.join(BASE, "out", "bodycache-%s.json" % day)
                    cache = json.load(open(cpath, encoding="utf-8")) if os.path.exists(cpath) else {}
                    cache[g["url"]] = body
                    json.dump(cache, open(cpath, "w", encoding="utf-8"), ensure_ascii=False)
            return
    if not rows:
        print("[find] 결과 없음 — 낱말을 줄이거나(주체+숫자) 영문 표기로 다시 찾을 것")
        sys.exit(2)


if __name__ == "__main__":
    main()
