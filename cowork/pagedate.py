#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[2026-11-09 사용자 지시] 후보 기사의 **페이지 발행일**을 확인한다 (`run.sh pagedate`).

[왜] 11-09 '네이버 이어 LG 출신 발탁…소버린 AI 무게 싣는 이재명 정부'(JTBC)는 2025-06-23 기사인데
  구글뉴스 RSS 가 발행일을 2026-11-07 로 줘서 후보 창을 통과했고, 판정 엑셀 발행일에도 '11-07' 로 찍혔다.
  사용자: "이 기사는 2025년 기사인데 왜 중으로 분류되있어? 엑셀보면 날짜도 이상하게 들어가있고"
  같은 날 후보 364건 중 페이지 날짜를 확인한 275건에서 7일 넘게 어긋난 것이 5건(2025-06-23·10-12·10-16·10-28·10-29).
  linkcheck 는 발송본·tier0 만 날짜를 대조했고, 판정 대상 후보 전체는 아무도 확인하지 않았다.

[무엇을] 링크가 있는 후보(prompt JSONL)의 페이지를 받아 linkcheck.pick_pubdate(메타·JSON-LD·<time>만, fail-open)로
  발행일을 뽑아 out/pagedate-<날짜>.json  {idx: {"page": "YYYY-MM-DD", "rss": "YYYY-MM-DD", "gap": N}} 에 쓴다.
  prejudge 는 페이지 발행일이 오늘보다 7일 넘게 이른 건을 '저'(자동:창밖)로 채우고(공식 블로그·뉴스룸 제외),
  verdict-xlsx 의 발행일 열은 페이지 날짜를 연도까지 쓴다(수집 날짜와 다르면 함께 표시).
  blind 다음, prejudge 앞에 부른다. 24병렬·건당 10초, 1~2분.
"""
import argparse, concurrent.futures as cf, datetime, json, os, re, sys, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import linkcheck as L

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    a = ap.parse_args()
    cands = []
    for line in open(os.path.join("out", "prompt-%s.txt" % a.date), encoding="utf-8"):
        if line.startswith('{"idx"'):
            try: o = json.loads(line)
            except Exception: continue
            if o.get("url"): cands.append(o)
    out_p = os.path.join("out", "pagedate-%s.json" % a.date)
    old = json.load(open(out_p, encoding="utf-8")) if os.path.exists(out_p) else {}
    todo = [o for o in cands if str(o["idx"]) not in old]
    def get(o):
        try:
            req = urllib.request.Request(o["url"], headers={"User-Agent": "Mozilla/5.0"})
            h = urllib.request.urlopen(req, timeout=10).read(400000).decode("utf-8", "ignore")
            return o, L.pick_pubdate(h)
        except Exception:
            return o, None
    with cf.ThreadPoolExecutor(24) as ex:
        for o, pd in ex.map(get, todo):
            rss = o.get("pub_date") or ""
            old[str(o["idx"])] = {"page": pd, "rss": rss, "gap": L.date_gap(pd, rss) if pd and rss else None,
                                  "official": L.is_official(re.sub(r"^https?://([^/]+).*$", r"\1", o["url"]))}
    json.dump(old, open(out_p, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    day = datetime.date.fromisoformat(a.date)
    stale = sorted([(i, v) for i, v in old.items() if v.get("page") and
                    (day - datetime.date.fromisoformat(v["page"])).days > 7 and not v.get("official")],
                   key=lambda x: x[1]["page"])
    print("[pagedate] 링크 있는 후보 %d건 / 새로 확인 %d건 / 페이지 날짜 확보 %d건"
          % (len(cands), len(todo), sum(1 for v in old.values() if v.get("page"))))
    print("[pagedate] 페이지 발행일이 7일 넘게 이른 건 %d건 (prejudge 가 '저'로 채움):" % len(stale))
    for i, v in stale:
        print("   %-5s 페이지 %s / 수집 %s" % (i, v["page"], v["rss"]))
    return 0

if __name__ == "__main__":
    sys.exit(main())
