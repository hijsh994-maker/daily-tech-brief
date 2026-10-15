# -*- coding: utf-8 -*-
"""
tier2_par.py - ingest_tier2.py 의 '병렬 실행' 래퍼 (Cowork 전용)

왜 필요한가:
  Cowork 샌드박스는 bash 호출 1건당 45초 제한이고, 호출이 끝나면 백그라운드
  프로세스도 함께 죽는다. 원본 ingest_tier2.py 는 77개 쿼리를 0.3초 sleep 과
  함께 순차 실행하므로 약 80초가 걸려 그대로는 완주할 수 없다.

무엇을 하는가:
  원본의 함수(all_queries / relevant / norm_key)와 samsung_resolve.gnews 를
  그대로 재사용하고, 쿼리 루프만 ThreadPool 로 바꾼다. 판정 로직·출력 스키마는
  원본과 동일하므로 refine.py 는 차이를 알지 못한다.

  ※ 원본 ingest_tier2.py 는 수정하지 않는다. 윈도우 예약작업(run_daily.ps1)은
    계속 원본을 쓰고, Cowork 에서만 이 래퍼를 쓴다.

사용:
  python3 cowork/tier2_par.py --date 2026-07-31 --window 1d --workers 8
"""
import argparse
import datetime
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import truststore  # noqa: E402
truststore.inject_into_ssl()

import yaml                      # noqa: E402
import samsung_resolve as SR     # noqa: E402
import ingest_tier2 as T2
import datewin as DW        # noqa: E402

OUTDIR = os.path.join(BASE, "out")
KW = os.path.join(BASE, "config", "keywords.yaml")


def log(m):
    print("[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), m))
    sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--window", default="1d")
    ap.add_argument("--workers", type=int, default=8,
                    help="동시 요청 수. 너무 올리면 구글뉴스가 429 를 준다.")
    a = ap.parse_args()

    day = (datetime.date.fromisoformat(a.date) if a.date
           else datetime.date.today())
    cfg = yaml.safe_load(open(KW, encoding="utf-8"))
    pol = cfg.get("policy", {})
    # 창 길이는 datewin 이 공휴일 보정까지 계산한다(연휴 다음날 확대).
    win = DW.window_days(pol, day)
    lo, hi = DW.window(pol, day)
    allow_undated = pol.get("allow_undated_raw", False)

    qs = T2.all_queries(cfg)
    log("쿼리 %d개 병렬 실행 (workers=%d, window=%s, 발행일 %s~%s)"
        % (len(qs), a.workers, a.window, lo, hi))

    t0 = time.time()

    def fetch(item):
        group, q = item
        loc = "en" if group == "en_track" else "ko"
        try:
            return group, q, loc, SR.gnews(q, a.window, locale=loc), None
        except Exception as e:
            return group, q, loc, [], str(e)[:60]

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        results = list(ex.map(fetch, qs))

    # 취합은 단일 스레드에서 원본과 동일한 순서·규칙으로 수행한다.
    pool, stats = {}, {}
    dropped_date = dropped_rel = dropped_src = 0
    errs = []
    for group, q, loc, items, err in results:
        if err:
            errs.append((q, err))
            continue
        n_new = 0
        for c in items:
            if c["date"] is None:
                if not allow_undated:
                    dropped_date += 1
                    continue
            elif not (lo <= c["date"] <= hi):
                dropped_date += 1
                continue
            if SR.source_rank(c["outlet"]) < 0 or SR.is_roundup(c["headline"]):
                dropped_src += 1
                continue
            ok, _ = T2.relevant(c["headline"], cfg)
            if not ok:
                dropped_rel += 1
                continue
            k = T2.norm_key(c["headline"])
            if not k:
                continue
            if k in pool:
                if q not in pool[k]["queries"]:
                    pool[k]["queries"].append(q)
                    pool[k]["groups"] = sorted(set(pool[k]["groups"]) | {group})
            else:
                pool[k] = {"src": "gnews", "title": c["headline"],
                           "outlet": c["outlet"],
                           "pub_date": str(c["date"]) if c["date"] else None,
                           "queries": [q], "groups": [group], "locale": loc,
                           "cluster": 1, "url": None, "confidence": "unresolved"}
                n_new += 1
        stats[q] = (len(items), n_new)

    items = list(pool.values())
    multi = sum(1 for x in items if len(x["queries"]) > 1)
    log("완료: 고유 %d건 (복수쿼리 %d) | 날짜탈락 %d / 소스탈락 %d / 노이즈탈락 %d | %.0f초"
        % (len(items), multi, dropped_date, dropped_src, dropped_rel,
           time.time() - t0))
    if errs:
        log("쿼리 실패 %d개: %s" % (len(errs),
            ", ".join("%s(%s)" % (q, e) for q, e in errs[:5])))

    os.makedirs(OUTDIR, exist_ok=True)
    p = os.path.join(OUTDIR, "tier2-%s.json" % day.isoformat())
    json.dump({"date": day.isoformat(), "query_count": len(qs),
               "items": items}, open(p, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    log("저장: %s" % p)
    # 실패 쿼리가 전체의 20% 를 넘으면 실패로 본다(구글뉴스 차단 감지).
    if errs and len(errs) > len(qs) * 0.2:
        log("경고: 실패 쿼리 비중이 20%% 초과. workers 를 낮추세요.")
        sys.exit(2)


if __name__ == "__main__":
    main()
