# -*- coding: utf-8 -*-
"""
persona.py - 사람이 실제로 고른 기사에서 '이 사람의 선별 기준'을 뽑아낸다.

## 왜 필요한가
브리프 선별은 LLM 이 한다. 그런데 LLM 에게 주는 지시문은 대부분 사람이
손으로 적은 문장이라, 정답지가 34일치 522건 쌓여도 반영되는 건
'카테고리 평균 분포' 한 줄뿐이었다. 그 결과 두 가지 문제가 생겼다.

  (1) 후보가 기계 점수 내림차순으로 나열돼 있어 LLM 이 상위권에 앵커링된다.
      그런데 실측하면 사람이 실은 기사의 **중앙값은 10점, 49%가 9점 이하**다.
      점수 상위권을 고르는 전략은 명백히 틀렸는데 그 사실이 어디에도 없었다.

  (2) 손으로 적은 지침이 데이터와 반대인 경우를 못 잡는다. 실측 결과
      '국내 경쟁사 건은 한 단계 올려 보라'는 지침이 있었지만 `경쟁사`
      신호가 붙은 기사의 채택률은 0.9%로 전체 평균(7.0%)의 **1/8**이었다.
      1년을 더 돌려도 사람이 직접 눈치채기 전엔 안 고쳐진다.

그래서 정답지와 그날 후보(refined)를 대조해 신호별 채택률을 계산하고,
평균 대비 배수(lift)로 '올릴 신호 / 내릴 신호'를 자동으로 만든다.

## 무엇을 계산하나
  - 채택 기사의 기계 점수 분포        -> 점수 앵커링을 깨는 근거
  - signal(why 태그) 별 채택률·lift   -> 올릴 신호 / 내릴 신호
  - 수집 통로(tier) 별 채택률·lift
  - 매체별 채택 건수                  -> 사람이 반복해 고르는 매체

## 어떻게 쓰나
  bash cowork/run.sh persona     # out/persona.json 갱신
  brief.py 가 out/persona.json 을 읽어 프롬프트에 블록을 붙인다.
  파일이 없으면 아무것도 안 붙는다(파이프라인은 그대로 돈다).

[주의] 대조는 refined 산출물이 남아 있는 날만 가능하다. 정답지만 있고
  그날 후보가 없으면 '안 실은 기사'를 알 수 없어 채택률을 못 낸다.
"""
import argparse
import collections
import datetime
import glob
import json
import os
import re
import statistics
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(BASE, "out")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scorecard import norm_url, same_event                      # noqa: E402

PERSONA = os.path.join(OUTDIR, "persona.json")

# [2026-08-25] cowork/tiers.py 로 일원화. 경위는 그 파일 주석 참조.
from tiers import tier_of as _tier_of_one   # noqa: E402


class _TierMap(object):
    @staticmethod
    def get(s, default=None):
        return _tier_of_one(s) or default


TIER_OF = _TierMap()

# 신호 이름만 남긴다: '지정학+2(미중)' -> '지정학'
RE_SIG = re.compile(r"[+\-]\d+.*$")

# lift 를 신뢰할 최소 표본. 이보다 적으면 우연이라 버린다.
MIN_N = 30


def log(m):
    print("[persona] %s" % m)


def _load(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


def pairs(max_days=40):
    """(날짜, refined, truth items) 목록. 둘 다 있는 날만."""
    out = []
    for p in sorted(glob.glob(os.path.join(OUTDIR, "refined-*.json"))):
        d = os.path.basename(p)[8:18]
        t = _load(os.path.join(BASE, "truth-%s.json" % d))
        if not t:
            continue
        rf = _load(p)
        if not rf:
            continue
        out.append((d, rf, t.get("items") or []))
    return out[-max_days:]


def build(max_days=40):
    data = pairs(max_days)
    if not data:
        return None

    sel_scores = []
    sig_sel, sig_all = collections.Counter(), collections.Counter()
    tier_sel, tier_all = collections.Counter(), collections.Counter()
    outlet_sel = collections.Counter()
    n_sel = n_all = 0

    for day, rf, truth in data:
        turls = {norm_url(t.get("url")) for t in truth if t.get("url")}
        ttls = [t.get("title") or "" for t in truth]
        # 매체는 정답지 원문에서 센다. refined 클러스터의 대표 outlet 을
        # 쓰면 같은 사건을 물어온 저품질 매체가 대표로 잡혀 순위가 뒤집힌다
        # (실측: 'finance.biggo.com', 'Moomoo' 가 전자신문보다 위로 왔다).
        for t in truth:
            if t.get("outlet"):
                outlet_sel[t["outlet"]] += 1
        for it in (rf.get("items") or []):
            us = {norm_url(u) for u in (it.get("member_urls") or []) if u}
            if it.get("url"):
                us.add(norm_url(it["url"]))
            hit = bool(us & turls) or any(
                same_event(it.get("title") or "", x) for x in ttls)
            n_all += 1
            if hit:
                n_sel += 1
                sel_scores.append(it.get("score") or 0)

            ts = {TIER_OF.get(s) for s in (it.get("sources") or [])}
            ts.discard(None)
            key = "+".join(t for t in ("tier0", "tier1", "tier2") if t in ts)
            if key:
                tier_all[key] += 1
                if hit:
                    tier_sel[key] += 1

            for w in (it.get("why") or []):
                k = RE_SIG.sub("", w).strip()
                if not k:
                    continue
                sig_all[k] += 1
                if hit:
                    sig_sel[k] += 1

    if not n_sel:
        return None
    base = 100.0 * n_sel / n_all

    def rows(sel, all_, min_n):
        out = []
        for k, a in all_.items():
            if a < min_n:
                continue
            r = 100.0 * sel[k] / a
            out.append({"name": k, "rate": round(r, 1), "n": a,
                        "hit": sel[k], "lift": round(r / base, 2)})
        return sorted(out, key=lambda x: -x["lift"])

    bands = collections.Counter()
    for x in sel_scores:
        bands["~5" if x < 6 else "6-9" if x < 10 else "10-14" if x < 15
              else "15-19" if x < 20 else "20+"] += 1

    return {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "days": len(data),
        "day_range": [data[0][0], data[-1][0]],
        "n_candidates": n_all,
        "n_selected": n_sel,
        "base_rate": round(base, 2),
        "score_median": statistics.median(sel_scores),
        "score_mean": round(sum(sel_scores) / len(sel_scores), 1),
        "score_bands": {k: bands[k] for k in ("~5", "6-9", "10-14",
                                              "15-19", "20+")},
        "signals": rows(sig_sel, sig_all, MIN_N),
        "tiers": rows(tier_sel, tier_all, 20),
        "outlets": [{"name": o, "n": c} for o, c in outlet_sel.most_common(15)],
    }


def render(p=None):
    """프롬프트에 붙일 '[개인화 선별 기준]' 블록. 파일이 없으면 빈 리스트."""
    p = p or _load(PERSONA)
    if not p:
        return []
    b, tot = p["score_bands"], p["n_selected"]
    low = b["~5"] + b["6-9"]
    L = ["", "[개인화 선별 기준 - 사람이 실제로 고른 %d건에서 계산했습니다]"
         % p["n_selected"],
         "(%s ~ %s, %d일치 정답지를 그날 후보와 대조. 전체 채택률 %.1f%%)"
         % (p["day_range"][0], p["day_range"][1], p["days"], p["base_rate"]),
         "",
         "**A. 점수 상위권을 고르지 마세요. 통계가 반대입니다.**",
         "  사람이 실제로 실은 기사의 기계 점수 분포:"]
    for k in ("~5", "6-9", "10-14", "15-19", "20+"):
        n = b[k]
        L.append("    %-6s %3d건 (%2.0f%%) %s"
                 % (k, n, 100.0 * n / tot, "#" * int(round(30.0 * n / tot))))
    L.append("  중앙값 %s점, 평균 %s점. **%.0f%%가 9점 이하입니다.**"
             % (p["score_median"], p["score_mean"], 100.0 * low / tot))
    L.append("  후보는 점수 내림차순으로 나열돼 있지만, 그 순서대로 위에서")
    L.append("  30건을 자르면 사람 브리프와 크게 어긋납니다. 점수는 '이 기사가")
    L.append("  기계 눈에 얼마나 띄었나'일 뿐 중요도가 아닙니다.")

    up = [s for s in p["signals"] if s["lift"] >= 1.3][:8]
    dn = [s for s in p["signals"] if s["lift"] <= 0.6][-8:]
    if up:
        L += ["", "**B. 채택률이 평균보다 높았던 신호 (signals 에서 확인)**"]
        for s in up:
            L.append("    %-12s 채택률 %4.1f%%  (평균의 %.1f배, 표본 %d건)"
                     % (s["name"], s["rate"], s["lift"], s["n"]))
        L.append("  이 신호가 붙은 항목은 점수가 낮아도 먼저 검토하세요.")
    if dn:
        L += ["", "**C. 채택률이 평균보다 낮았던 신호 - 점수가 높아도 의심하세요**"]
        for s in sorted(dn, key=lambda x: x["lift"]):
            L.append("    %-12s 채택률 %4.1f%%  (평균의 %.1f배, 표본 %d건)"
                     % (s["name"], s["rate"], s["lift"], s["n"]))
    if p.get("tiers"):
        L += ["", "**D. 수집 통로별 채택률**"]
        for s in p["tiers"]:
            L.append("    %-18s %4.1f%%  (평균의 %.1f배, 표본 %d건)"
                     % (s["name"], s["rate"], s["lift"], s["n"]))
    if p.get("outlets"):
        L += ["", "**E. 사람이 반복해서 고른 매체 (채택 건수)**",
              "    " + ", ".join("%s %d" % (o["name"], o["n"])
                                 for o in p["outlets"])]
        L.append("  매체만 보고 고르지는 마세요. 다만 같은 사건에 여러 후보가")
        L.append("  있으면 이 목록에 있는 매체를 대표로 쓰는 편이 맞습니다.")
    return L


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=40)
    ap.add_argument("--show", action="store_true", help="프롬프트 블록 미리보기")
    a = ap.parse_args()

    if a.show:
        print("\n".join(render()))
        return 0

    p = build(a.days)
    if not p:
        log("대조 가능한 날이 없습니다(정답지+refined 둘 다 필요).")
        return 0
    os.makedirs(OUTDIR, exist_ok=True)
    json.dump(p, open(PERSONA, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    log("%d일치 / 후보 %d건 중 채택 %d건 (%.1f%%) -> %s"
        % (p["days"], p["n_candidates"], p["n_selected"],
           p["base_rate"], PERSONA))
    log("점수 중앙값 %s / 9점 이하 %.0f%%"
        % (p["score_median"],
           100.0 * (p["score_bands"]["~5"] + p["score_bands"]["6-9"])
           / p["n_selected"]))
    top = p["signals"][:3]
    if top:
        log("상위 신호: " + ", ".join("%s %.1fx" % (s["name"], s["lift"])
                                   for s in top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
