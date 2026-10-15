#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[2026-09-09 신설] 순위 가중치 **학습 + 회귀 검증**.

사용:
  python3 cowork/rankfit.py --eval      # leave-one-day-out 검증만 (쓰기 없음)
  python3 cowork/rankfit.py --fit       # 전체 날짜로 학습해 config/rankweights.json 갱신
  python3 cowork/rankfit.py --eval --n 15

## 무엇을 학습하나
과거 날짜의 **'고' 판정 건**만 대상으로, 그 건이 그날 정답지에 실렸는지를
라벨(hit)로 삼아 피처값별 로그-lift 를 구한다. 점수는 로그-lift 의 합이다.

    score(r) = Σ_k  log( P(hit | k=v) / P(hit) )

라플라스 평활을 건다(`--prior`, 기본 8). 표본이 적은 값이 극단으로 튀는 것을
막는다. 실측상 prior 3~15 구간에서 결과가 같았고 30 부터 나빠졌다.

## 왜 '고' 만 쓰나
rank 는 '고' 안에서만 매긴다. '중·저'까지 넣으면 판정 단계의 성능이 섞여
순위 단계만의 개선을 잴 수 없다.

## 과적합 방지 — 이 파일의 존재 이유
`--eval` 은 **그날을 빼고 학습해 그날을 맞히는** leave-one-day-out 이다.
09-07 의 v2/v3 배점 사고(하루치 62건으로 배점을 갈아엎어 다음 날 무너짐)를
되풀이하지 않으려고 만들었다. **배점·피처를 바꾸면 반드시 --eval 부터
돌리고, 숫자가 나빠지면 되돌린다.**

## 2026-09-09 최초 실측 (5일치 / '고' 254건 중 정답지 58건)
    무작위 기대                       37.4%
    현행 rank(손으로 매긴 W/I/C/S/D)     45.7%
    route 만                        69.6%
    route + spread                  69.6%
    route + spread + 판정사유 태그 9종    73.9%   <- 채택
    (+outlet 은 67.4% 로 나빠졌다. 매체별 표본이 5~20건이라 과적합한다.)
"""

import argparse
import csv
import json
import math
import os
import sys
from collections import Counter

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
OUTDIR = os.path.join(BASE, "out")
CFG = os.path.join(BASE, "config", "rankweights.json")

from cowork import rankfeat  # noqa: E402
from cowork import match     # noqa: E402

SIM_HIT = 0.45   # 정답지와 같은 사건으로 볼 제목 유사도


def days_available():
    out = []
    for fn in sorted(os.listdir(OUTDIR)):
        if not (fn.startswith("verdict-") and fn.endswith(".csv")):
            continue
        d = fn[len("verdict-"):-len(".csv")]
        if len(d) != 10:
            continue
        if not os.path.exists(os.path.join(BASE, "truth-%s.json" % d)):
            continue
        out.append(d)
    return out


def load_day(day):
    """그날의 '고' 건 목록. rank 가 비어 있는 구형식 날짜는 건너뛴다."""
    vp = os.path.join(OUTDIR, "verdict-%s.csv" % day)
    tp = os.path.join(BASE, "truth-%s.json" % day)
    blind = rankfeat.read_blind(day)
    cands = rankfeat.read_cands(day)
    truth = json.load(open(tp, encoding="utf-8"))
    items = truth["items"] if isinstance(truth, dict) else truth
    rows = []
    for r in csv.DictReader(open(vp, encoding="utf-8")):
        if r.get("grade", "").strip() != "고":
            continue
        idx = r["idx"].strip()
        b = blind.get(idx) or {}
        c = cands.get(idx) or {}
        title = b.get("title") or c.get("headline") or ""
        if not title:
            continue
        sim = max((match.title_sim(title, t["title"]) for t in items), default=0)
        f = rankfeat.features(idx, r.get("reason", ""), blind, cands)
        f.update({"day": day, "idx": idx, "title": title,
                  "rank": r.get("rank", ""), "hit": 1 if sim >= SIM_HIT else 0})
        rows.append(f)
    return rows


def learn(train, keys, prior):
    base = sum(r["hit"] for r in train) / max(1, len(train))
    W = {}
    for k in keys:
        n, h = Counter(), Counter()
        for r in train:
            n[r[k]] += 1
            h[r[k]] += r["hit"]
        W[k] = {v: round(math.log(((h[v] + prior * base) / (n[v] + prior)) / base), 4)
                for v in n}
    return W, base


def score(r, W):
    return sum(tbl.get(r.get(k), 0.0) for k, tbl in W.items())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--fit", action="store_true")
    ap.add_argument("--n", type=int, default=15, help="발송본 건수 가정")
    ap.add_argument("--prior", type=float, default=8.0)
    a = ap.parse_args()

    days = days_available()
    allrows = []
    for d in days:
        rs = load_day(d)
        if not any(r["rank"] for r in rs):
            continue          # rank 열이 없는 구형식 날짜는 제외
        allrows += rs
    days = sorted({r["day"] for r in allrows})
    if len(days) < 2:
        print("[rankfit] 학습에 쓸 날짜가 부족합니다 (%d일)" % len(days))
        return 1
    keys = rankfeat.FEATURE_KEYS
    tot = sum(r["hit"] for r in allrows)
    print("[rankfit] %d일치 / '고' %d건 / 그중 정답지 %d건"
          % (len(days), len(allrows), tot))

    if a.eval or not a.fit:
        # 기준 1) 현행 rank
        cin = 0
        for d in days:
            te = [r for r in allrows if r["day"] == d and r["rank"]]
            te.sort(key=lambda r: int(r["rank"]))
            cin += sum(r["hit"] for r in te[:a.n])
        # 기준 2) 무작위 기대
        exp = 0.0
        for d in days:
            te = [r for r in allrows if r["day"] == d]
            exp += min(a.n, len(te)) / len(te) * sum(r["hit"] for r in te)
        # 신 모델
        nin = 0
        per = []
        for d in days:
            tr = [r for r in allrows if r["day"] != d]
            te = [r for r in allrows if r["day"] == d]
            W, _ = learn(tr, keys, a.prior)
            t2 = sorted(te, key=lambda r: -score(r, W))[:a.n]
            hi = sum(r["hit"] for r in t2)
            per.append((d, hi, sum(r["hit"] for r in te)))
            nin += hi
        print("  [기준] 무작위 기대          %5.1f/%d = %4.1f%%" % (exp, tot, exp / tot * 100))
        print("  [기준] 현행 rank 열         %5d/%d = %4.1f%%" % (cin, tot, cin / tot * 100))
        print("  [신] route+spread+태그      %5d/%d = %4.1f%%   %s"
              % (nin, tot, nin / tot * 100,
                 " ".join("%s:%d/%d" % (d[5:], x, y) for d, x, y in per)))
        if nin <= cin:
            print("  [경고] 신 모델이 현행보다 낫지 않습니다. --fit 하지 마세요.")

    if a.fit:
        W, base = learn(allrows, keys, a.prior)
        os.makedirs(os.path.dirname(CFG), exist_ok=True)
        json.dump({"trained_on": days, "n": len(allrows), "hits": tot,
                   "base": round(base, 4), "prior": a.prior, "weights": W},
                  open(CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("[rankfit] 저장: config/rankweights.json (%d일치 학습)" % len(days))
        for k in ("route", "spread"):
            print("  %s: %s" % (k, ", ".join(
                "%s %+.2f" % (v, w) for v, w in sorted(W[k].items(), key=lambda x: -x[1]))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
