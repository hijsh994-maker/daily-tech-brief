# -*- coding: utf-8 -*-
"""body_audit.py - `run.sh body` 단계가 값을 하고 있는지 누적으로 잰다.

## 왜 만들었나 (2026-08-27 사용자 지시)
body 단계는 하루 약 5분을 쓴다(전체 39분의 13%). 본문을 300건 받아
관심 주제(소버린·지정학·디지털트윈) 신호를 재채점해 가점을 준다.
도입 근거(2026-08-07 '경북도 가상공장' 건)는 타당했지만, 그 뒤로
**실제로 정답지 적중에 기여했는지 한 번도 재보지 않았다.**

첫 측정(2026-08-27, 채점된 6일 기준):
    본문 가점 75건 -> 정답지와 같은 사건 3건 (4.0%)
    가점 '전' 점수대: 6~11점 56건 / 12점+ 17건 / 6점 미만 9건
    신호 어휘: 소버린 65(AI 주권·모티프·엑사원·국산 AI 모델 등),
               지정학 9(수출통제), 디지털트윈 8
    -> 어휘 자체는 정확했다(오탐 아님). 다만 3건 중 가점이 결정적이었을
       법한 건 1건뿐이다(로봇 8->10점). 나머지 둘은 가점 없이도 18·12점.

표본이 얇아 이 한 번으로 폐지를 결정하지 않는다. 채점일이 쌓이면
다시 돌려 추세를 본다. **적중이 계속 4% 안팎이면 limit 축소나 폐지를
검토한다.**

## 쓰는 법
    bash cowork/run.sh body-audit
정답지(truth-*.json)와 out/refined-*.json 이 둘 다 있는 날만 센다.
"""
import argparse
import collections
import glob
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(BASE, "out")

BONUS = 2          # body_rescore 가 신호 1건당 주는 점수
HIT = 0.45         # 제목 낱말 겹침이 이 이상이면 '같은 사건'으로 본다


def toks(s):
    return {p for p in re.split(r"[^0-9a-z가-힣]+", (s or "").lower())
            if len(p) >= 2}


def why_of(it):
    w = it.get("why")
    return " ".join(w) if isinstance(w, list) else str(w or "")


def pool_of(data):
    out = []
    for k in ("items", "rescue_nolink", "own_track"):
        out += data.get(k) or []
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-08-13",
                    help="body 단계가 도입된 날 이후만 센다")
    a = ap.parse_args()

    days = sorted(os.path.basename(p)[6:16]
                  for p in glob.glob(os.path.join(BASE, "truth-*.json")))
    days = [d for d in days if d >= a.since]

    band = collections.Counter()
    sigs = collections.Counter()
    n_bonus = n_hit = n_day = 0
    hits = []
    for day in days:
        rp = os.path.join(OUTDIR, "refined-%s.json" % day)
        tp = os.path.join(BASE, "truth-%s.json" % day)
        if not (os.path.exists(rp) and os.path.exists(tp)):
            continue
        n_day += 1
        data = json.load(open(rp, encoding="utf-8"))
        truth = json.load(open(tp, encoding="utf-8"))
        tl = truth.get("items") if isinstance(truth, dict) else truth
        ths = [(x.get("headline") or x.get("title") or "") for x in tl]

        for it in pool_of(data):
            w = why_of(it)
            if "(본문)" not in w:
                continue
            n_bonus += 1
            for m in re.findall(r"(\S+?)\(본문\)\+\d+\((.+?)\)", w):
                sigs[m] += 1
            base = (it.get("score") or 0) - BONUS * w.count("(본문)")
            band["12점+" if base >= 12 else
                 "6~11점" if base >= 6 else "6점 미만"] += 1
            a_t = toks(it.get("title"))
            best, bt = 0.0, ""
            for h in ths:
                r = len(a_t & toks(h)) / max(len(a_t), 1)
                if r > best:
                    best, bt = r, h
            if best >= HIT:
                n_hit += 1
                hits.append((day, base, it.get("score"),
                             (it.get("title") or "")[:52], bt[:52]))

    if not n_day:
        print("[body-audit] 정답지와 refined 가 함께 있는 날이 없습니다")
        return 1
    print("[body-audit] 채점일 %d일 / 본문 가점 %d건" % (n_day, n_bonus))
    if not n_bonus:
        return 0
    print("[body-audit] 정답지와 같은 사건: %d건 (%.1f%%)"
          % (n_hit, 100.0 * n_hit / n_bonus))
    print("[body-audit] 가점 '전' 점수대: %s"
          % " / ".join("%s %d" % (k, v) for k, v in band.most_common()))
    print("[body-audit] 신호 어휘 상위: %s"
          % ", ".join("%s<-%s %d" % (s, t, n)
                      for (s, t), n in sigs.most_common(6)))
    for day, base, sc, mine, tru in hits:
        print("  [%s] %d->%d  %s" % (day, base, sc, mine))
        print("            정답지: %s" % tru)
    # 가점이 실제로 판을 바꿨을 법한 건 - 가점 전 12점 미만이었던 적중분
    dec = [h for h in hits if h[1] < 12]
    print("[body-audit] 그중 가점이 결정적이었을 법한 건(가점 전 12점 미만): %d건"
          % len(dec))
    if n_hit and 100.0 * n_hit / n_bonus < 3.0 and n_day >= 15:
        print("[body-audit] [검토] 적중률이 3%% 미만이고 표본도 충분합니다. "
              "limit 축소나 폐지를 검토하세요")
    return 0


if __name__ == "__main__":
    sys.exit(main())
