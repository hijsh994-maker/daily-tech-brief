# -*- coding: utf-8 -*-
"""
ingest_tier2.py - 구글뉴스 키워드 검색 수집 (discovery backbone)

config/keywords.yaml 의 discovery_queries 를 전부 돌려 후보 기사를 모은다.
백테스트에서 이 쿼리 세트가 과거 브리프 421건의 91%를 커버했다.

[중요] 여기서는 링크를 해결하지 않는다.
  구글뉴스는 직링크를 주지 않으므로 링크 해결에 항목당 1~3초가 든다.
  하루 후보가 수백 건인데 전부 해결하면 수십 분이 걸리고, 그중 대부분은
  점수 미달로 버려진다. 링크 해결은 refine.py 가 점수로 추린 뒤에 한다.

출력: out/tier2-YYYY-MM-DD.json
      {headline, outlet, date, queries[]}  - queries 는 이 기사를 잡아낸 쿼리들
      여러 쿼리에 동시에 걸린 기사는 그만큼 주제 적합도가 높다는 신호다.
"""
import argparse
import datetime
import json
import os
import re
import sys
import time

import truststore
truststore.inject_into_ssl()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yaml                                  # noqa: E402
import samsung_resolve as SR
import datewin as DW                 # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.join(BASE, "out")
KW = os.path.join(BASE, "config", "keywords.yaml")


def log(m):
    print("[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), m))


def norm_key(headline):
    """중복 판정용 키. 같은 기사가 여러 쿼리에 걸리므로 필수."""
    s = re.sub(r"\[[^\]]{0,20}\]", "", headline)
    s = re.sub(r"[^\w가-힣]", "", s)
    return s.lower()[:60]


def all_queries(cfg):
    dq = cfg.get("discovery_queries", {})
    out = []
    for group, qs in dq.items():
        for q in qs or []:
            out.append((group, q))
    # 인물 단독 이벤트 쿼리도 포함 (people.event_queries)
    for q in (cfg.get("people", {}).get("event_queries") or []):
        out.append(("people", q))
    return out


def relevant(headline, cfg):
    """원시 수집이므로 relevance 하드필터를 적용한다.

    tier0(큐레이션 뉴스레터)와 달리 여기는 잡음이 그대로 들어온다.
    실측 노이즈: '한국게임과학고, 피지컬 AI 특강', '인천대 인재 양성' 등.
    """
    rel = cfg.get("relevance", {})
    for p in rel.get("must_none") or []:
        if str(p) in headline:
            return False, "must_none:%s" % p
    return True, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--window", default="1d", help="구글뉴스 when: (기본 1d)")
    ap.add_argument("--groups", help="쉼표구분. 예: ko_topic,ko_entity")
    ap.add_argument("--sleep", type=float, default=0.3)
    a = ap.parse_args()

    day = (datetime.date.fromisoformat(a.date) if a.date else datetime.date.today())
    cfg = yaml.safe_load(open(KW, encoding="utf-8"))
    pol = cfg.get("policy", {})
    # 창 길이는 datewin 이 공휴일 보정까지 계산한다(연휴 다음날 확대).
    win = DW.window_days(pol, day)
    lo, hi = DW.window(pol, day)
    # 원시 수집(tier2)은 발간일 미상 기사를 제외한다(오래된 기사 혼입 방지).
    allow_undated = pol.get("allow_undated_raw", False)
    qs = all_queries(cfg)
    if a.groups:
        want = {g.strip() for g in a.groups.split(",")}
        qs = [(g, q) for g, q in qs if g in want]
    log("쿼리 %d개 실행 (window=%s, 발행일 %s~%s)" % (len(qs), a.window, lo, hi))

    pool, stats, t0 = {}, {}, time.time()
    dropped_date = dropped_rel = dropped_src = 0

    for i, (group, q) in enumerate(qs, 1):
        # en_track 쿼리는 영문 로케일로 돌려야 영문 원문이 잡힌다.
        loc = "en" if group == "en_track" else "ko"
        try:
            items = SR.gnews(q, a.window, locale=loc)
        except Exception as e:
            log("  ERR %s (%s)" % (q, str(e)[:50]))
            continue
        n_new = 0
        for c in items:
            # 발간일 창(전일~당일) 밖은 제외. 미상 기사도 정책에 따라 제외.
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
            ok, _ = relevant(c["headline"], cfg)
            if not ok:
                dropped_rel += 1
                continue
            k = norm_key(c["headline"])
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
        if i % 10 == 0:
            log("  %d/%d 쿼리, 누적 고유 %d건" % (i, len(qs), len(pool)))
        time.sleep(a.sleep)

    items = list(pool.values())
    # 여러 쿼리에 걸린 기사 = 주제 적합도 신호. 점수 단계에서 쓴다.
    multi = sum(1 for x in items if len(x["queries"]) > 1)
    log("완료: 고유 %d건 (복수쿼리 %d) | 날짜탈락 %d / 소스탈락 %d / 노이즈탈락 %d | %.0f초"
        % (len(items), multi, dropped_date, dropped_src, dropped_rel, time.time() - t0))

    top = sorted(stats.items(), key=lambda x: -x[1][1])[:8]
    log("기여 상위 쿼리: " + ", ".join("%s(%d)" % (q, n) for q, (_, n) in top))
    zero = [q for q, (_, n) in stats.items() if n == 0]
    if zero:
        log("신규 0건 쿼리 %d개: %s" % (len(zero), ", ".join(zero[:10])))

    os.makedirs(OUTDIR, exist_ok=True)
    p = os.path.join(OUTDIR, "tier2-%s.json" % day.isoformat())
    json.dump({"date": day.isoformat(), "query_count": len(qs),
               "items": items}, open(p, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    log("저장: %s" % p)


if __name__ == "__main__":
    main()
