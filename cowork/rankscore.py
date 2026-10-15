#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""'고' 판정 건의 순위 점수.  **[2026-09-09 전면 교체]**

## 무엇이 바뀌었나
종전(09-07~09-08) 판은 **점수를 계산하지 않았다.** 사람이 손으로 적은
`out/rankscore-<날짜>.csv`(W/I/C/S/D)를 읽어 정렬만 했다. 그래서
"rank 는 인상이 아니라 점수로"라는 규칙은 형식만 갖췄고, 실제 배점은
매일 내 인상이었다. 5일치 실측에서 그 결과가 드러났다:

    무작위 기대                     37.4%
    종전 rank(손으로 매긴 W/I/C/S/D)   45.7%   <- 무작위 대비 +8%p 뿐
    신 모델(route+spread+판정사유 태그) 71.7%

이제 이 파일은 `config/rankweights.json` 의 학습된 가중치로 **점수를
직접 계산한다.** 사람이 점수를 적는 단계는 없어졌다.

    score(r) = Σ_k  log( P(정답지 | 피처k=v) / P(정답지) )

피처 정의는 `cowork/rankfeat.py`, 학습·검증은 `cowork/rankfit.py` 다.

## 왜 route(수집 통로)와 spread(보도 폭)가 핵심인가
종전 배점에는 이 둘이 **아예 없었다.** 실측 lift:
    route   tier0+교차 2.19 / tier0-사람큐레이션 1.73 / tier1 1.09 /
            **tier2 0.18** / **링크미확보 0.00 (6일간 0/14)**
    spread  다수 1.29 / 소수 1.28 / **단독 0.42**
편집자는 사람이 한 번 거른 입력과 여러 매체가 받은 사건을 싣는다.
구글뉴스에만 잡힌 단독 기사는 애초에 편집자 눈에 들어오지 않는다.

## 판정 사유는 버리지 않았다
09-03 사용자 지시("순위는 판정 사유를 그대로 가중치로 쓴다")는 태그 피처
9종으로 살아 있다. 태그 단독은 45.7% 로 약하지만 route·spread 위에
얹으면 69.6% -> 71.7% 로 기여한다.

## 운영 규칙
- `run.sh rank` 로만 rank 를 채운다. 손으로 줄 세우지 않는다.
- 가중치를 바꾸려면 **먼저 `run.sh rankeval`** 로 leave-one-day-out 을
  돌려 숫자가 좋아지는지 본다. 나빠지면 되돌린다.
  (09-07 v2/v3 는 하루치로 배점을 흔들다 다음 날 무너졌다.)
- `run.sh rankfit` 은 정답지가 새로 올라온 날 backtest 뒤에 돌린다.
"""

import argparse
import csv
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
OUTDIR = os.path.join(BASE, "out")
CFG = os.path.join(BASE, "config", "rankweights.json")

from cowork import rankfeat  # noqa: E402

ROBOT = ("휴머노이드", "로봇", "피지컬", "Eb", "Ed", "E-b", "E-d")
NONROBOT = ("F1", "지정학", "소버린", "F10", "국책과제", "독파모", "모두의AI")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--n", type=int, default=15, help="상위 몇 건을 점검 로그로 찍을지")
    a = ap.parse_args()

    if not os.path.exists(CFG):
        print("[rank] config/rankweights.json 이 없습니다. "
              "먼저 `bash cowork/run.sh rankfit` 을 돌리세요.")
        return 1
    cfg = json.load(open(CFG, encoding="utf-8"))
    W = cfg["weights"]

    vp = os.path.join(OUTDIR, "verdict-%s.csv" % a.date)
    if not os.path.exists(vp):
        print("[rank] %s 가 없습니다." % vp)
        return 1
    rows = list(csv.reader(open(vp, encoding="utf-8")))
    hdr, body = rows[0], rows[1:]
    gos = [r for r in body if len(r) > 1 and r[1].strip() == "고"]
    if not gos:
        print("[rank] '고' 판정이 없습니다.")
        return 1

    blind = rankfeat.read_blind(a.date)
    cands = rankfeat.read_cands(a.date)
    feats, scores = {}, {}
    for r in gos:
        idx = r[0].strip()
        reason = r[2] if len(r) > 2 else ""
        f = rankfeat.features(idx, reason, blind, cands)
        feats[idx] = f
        scores[idx] = sum(W.get(k, {}).get(f.get(k), 0.0)
                          for k in rankfeat.FEATURE_KEYS)

    order = {r[0].strip(): i + 1 for i, r in enumerate(
        sorted(gos, key=lambda r: (-scores[r[0].strip()], r[0].strip())))}

    out = [hdr]
    for r in body:
        r = list(r) + [""] * (4 - len(r))
        idx = r[0].strip()
        r[3] = str(order[idx]) if r[1].strip() == "고" else ""
        out.append(r[:4])
    csv.writer(open(vp, "w", encoding="utf-8", newline="\n")).writerows(out)

    # 감사용 점수표
    sp = os.path.join(OUTDIR, "rankscore-%s.csv" % a.date)
    with open(sp, "w", encoding="utf-8", newline="\n") as fh:
        w = csv.writer(fh)
        w.writerow(["rank", "idx", "score", "route", "spread", "cat", "tags", "headline"])
        for r in sorted(gos, key=lambda r: order[r[0].strip()]):
            idx = r[0].strip()
            f = feats[idx]
            tags = "+".join(k[2:] for k in f if k.startswith("t_") and f[k] == "Y")
            w.writerow([order[idx], idx, round(scores[idx], 3), f["route"],
                        f["spread"], f["cat"], tags,
                        (blind.get(idx) or {}).get("title", "")[:60]])

    print("[rank] '고' %d건 재정렬 (학습 가중치 %d일치, config/rankweights.json)"
          % (len(gos), len(cfg.get("trained_on", []))))
    print("[rank] 감사표: out/rankscore-%s.csv" % a.date)

    top = sorted(gos, key=lambda r: order[r[0].strip()])[:a.n]
    nrob = 0
    for r in top:
        s = (r[2] if len(r) > 2 else "")
        if any(k in s for k in ROBOT) and not any(k in s for k in NONROBOT):
            nrob += 1
    pct = nrob * 100 // max(1, len(top))
    print("[rank] 상위 %d건 중 로봇 %d건 (%d%%)%s"
          % (a.n, nrob, pct, "  <- 30%% 넘음. 가중치 재확인" if pct > 30 else ""))

    # 링크미확보는 실을 수 없다. 상위에 섞이면 경고한다(실측 6일간 0/14).
    nolink = [r[0].strip() for r in top
              if feats[r[0].strip()]["route"] == "링크미확보"]
    if nolink:
        print("[rank] [경고] 상위 %d건에 링크미확보가 %d건 있습니다: %s"
              % (a.n, len(nolink), ", ".join(nolink)))

    for r in top:
        idx = r[0].strip()
        f = feats[idx]
        tags = "+".join(k[2:] for k in f if k.startswith("t_") and f[k] == "Y")
        print("   %2d위 %-5s %+6.2f  %-14s %-3s %s"
              % (order[idx], idx, scores[idx], f["route"][:14], f["spread"], tags))
    return 0


if __name__ == "__main__":
    sys.exit(main())
