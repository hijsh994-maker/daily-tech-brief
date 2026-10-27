#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[2026-09-09 신설] 순위 점수의 **피처 추출** 공용 모듈.

## 왜 만들었나
종전 `rankscore.py` 는 점수를 계산하지 않았다. 내가 손으로 적은
`rankscore-<날짜>.csv`(W/I/C/S/D)를 읽어 정렬만 했다. 즉 "rank 는 인상이
아니라 점수로"라는 09-07 규칙은 **형식만 갖췄고 실체는 여전히 인상**이었다.

2026-09-09 실측(5일치 · '고' 254건 중 정답지 58건, leave-one-day-out):
    무작위 기대                    37.4%
    현행 rank(손으로 매긴 W/I/C/S/D)  45.7%   <- 무작위 대비 +8%p 뿐
    route(수집 통로)만              69.6%
    route + spread(보도 폭)        69.6%
    route + spread + 판정사유 태그    73.9%   <- 채택

**가장 잘 가르는 두 축이 종전 배점에 아예 없었다.**
    route  tier0+교차 50.0%(lift 2.19) / tier0-사람큐레이션 39.4%(1.73) /
           tier1 25.0%(1.09) / **tier2 4.2%(0.18)** / **링크미확보 0.0%(0/14)**
    spread 다수 29.4% / 소수 29.1% / **단독 9.6%(lift 0.42)**

해석: 편집자는 사람이 이미 한 번 거른 입력(tier0)과 여러 매체가 받은
사건을 싣는다. 구글뉴스에만 잡힌 단독 기사는 애초에 편집자 눈에 안 띈다.
링크미확보는 실을 수가 없으니 0/14 가 당연한데도 종전 배점은 이를 몰랐다.

## 피처
  route   수집 통로            (blind 목록 2번째 칸)
  spread  보도 폭 다수/소수/단독  (blind 목록 5번째 칸)
  cat     카테고리              (prompt JSONL 의 category)
  t_*     판정 사유 태그 9종      (verdict.csv 의 reason 을 정규식으로)

판정 사유 태그를 남긴 이유는 09-03 사용자 지시("순위는 판정 사유를 그대로
가중치로 쓴다")를 버리지 않기 위해서다. 실측상 태그 단독은 45.7% 로
약하지만 route·spread 위에 얹으면 69.6% -> 73.9% 로 기여한다.

## 주의
- **가중치를 여기에 적지 않는다.** 학습 결과는 `config/rankweights.json`
  이고 `cowork/rankfit.py` 가 만든다. 이 파일은 피처 정의만 갖는다.
- 태그 정규식을 늘릴 때는 반드시 `run.sh rankeval` 로 회귀 검증부터 한다.
  09-07 v2/v3 는 하루치로 배점을 흔들다 과적합을 냈다.
"""

import json
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(BASE, "out")

# 판정 사유(verdict.reason)에서 뽑는 태그. 이름 -> 정규식
TAG_PATTERNS = [
    ("F1",         r"F1|1천억|조원|억달러|억 달러|兆"),
    ("지정학",       r"지정학|미중|중국|수출통제|안보|관세"),
    ("소버린",       r"소버린|독파모|모두의ai|모두의 ai|주권"),
    ("국내정책",      r"국내정책|국내 정책|정부|국책|시행령|예산|공고|법 제정|규제"),
    ("F10경쟁사",    r"f10|경쟁사"),
    ("신규출시",      r"신규|출시|공개|ga\b|런칭"),
    ("휴머노이드로봇",   r"휴머노이드|로봇|피지컬|e-?b|e-?d"),
    ("AI규범사고",    r"규범|사고|보안|해킹|소송|안전|유출"),
    ("프런티어",      r"프런티어|빅테크|프론티어"),
]

BLIND_RE = re.compile(r"^([NLC]?\d+) \| ([^|]+)\|([^|]+)\|([^|]+)\|([^|]+)\| (.*)$")


def read_blind(day):
    """candidates-blind-<날짜>.txt -> {idx: {route, outlet, date, spread, title}}"""
    out = {}
    p = os.path.join(OUTDIR, "candidates-blind-%s.txt" % day)
    if not os.path.exists(p):
        return out
    for line in open(p, encoding="utf-8"):
        m = BLIND_RE.match(line.rstrip("\n"))
        if not m:
            continue
        title = re.sub(r"\s*\[중복의심.*", "", m.group(6).strip()).strip("*[] ")
        out[m.group(1)] = {
            "route": m.group(2).strip(),
            "outlet": m.group(3).strip(),
            "date": m.group(4).strip(),
            "spread": m.group(5).strip(),
            "title": title,
        }
    return out


def read_cands(day):
    """prompt-<날짜>.txt 의 후보 JSONL -> {idx: obj}"""
    out = {}
    p = os.path.join(OUTDIR, "prompt-%s.txt" % day)
    if not os.path.exists(p):
        return out
    for line in open(p, encoding="utf-8"):
        if not line.startswith('{"idx"'):
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        out[str(o["idx"])] = o
    return out


def features(idx, reason, blind, cands):
    """한 건의 피처 dict. blind/cands 는 read_* 결과."""
    b = blind.get(idx) or {}
    c = cands.get(idx) or {}
    f = {
        "route": b.get("route") or ("링크미확보" if idx.startswith("N") else "?"),
        "spread": b.get("spread") or "?",
        "cat": c.get("category") or "?",
    }
    s = (reason or "").lower()
    for name, pat in TAG_PATTERNS:
        f["t_" + name] = "Y" if re.search(pat, s) else "N"
    # [2026-09-10] ntag 는 **학습에서 뺐다**(FEATURE_KEYS 참조). 값은 감사용으로
    #   계속 계산해 판정 엑셀의 '태그개수' 열에만 쓴다.
    f["ntag"] = str(min(3, sum(1 for k in f if k.startswith("t_") and f[k] == "Y")))
    return f


# 학습·적용에 쓰는 키 목록. **여기가 정본이다.**
#
# [2026-09-10] `ntag`(태그 개수)를 뺐다. 09-09 에 넣을 때 근거였던
#   '71.7% -> 76.1%'는 **5일치 표본의 우연이었다.** 7일치로 늘리자 역전됐다.
#     top16 / 7일 LOO ('고' 300건 중 정답지 66건)
#       ntag 포함(종전)  44/66 = 66.7%
#       ntag 제외(채택)  47/66 = 71.2%     <- top15·17·18 에서도 일관되게 +3~4건
#   왜 해로웠나 - '태그 3개' 구간의 표본이 **9건뿐**인데 가중치 -0.56 이
#   붙었다. 실제 적중률은 22.2% 로 기준선 22.0% 과 같아 **정보가 없는
#   구간**인데 강한 감점을 받았고, prior 8 의 평활로도 못 막았다.
#   실측 피해 09-10: 'F1(20조)·지정학·프런티어' 태그 3개가 붙은
#   '구글, 핀란드 AI 인프라 150억달러'가 태그기여 -0.834 를 받아
#   rank 22 로 밀려 발송본에서 잘렸다. 그날 정답지였다.
#   그날 top16 적중이 1/8 -> 4/8 로 회복된다.
#
# 검증 이력(leave-one-day-out):
#   무작위 39.1% / 손매김 rank 44.4% / route 만 69.6% / route+spread 69.6%
#   route+spread+태그 **71.2%(채택)** / +태그개수 66.7%(2026-09-10 기각)
#   기각: +outlet 60.6% · +cat 67.4% · route/spread 감점 완화 63~70% ·
#         태그 가중치 2배 61.1% · 매체 채택률 59.1%
#   ※ prior 4 가 72.7% 로 조금 더 높으나, 평활을 약하게 하는 방향은
#     ntag 와 같은 과적합을 다시 부를 수 있어 prior 8 을 유지한다.
FEATURE_KEYS = ["route", "spread"] + ["t_" + n for n, _ in TAG_PATTERNS]
