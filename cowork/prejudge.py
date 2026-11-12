#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[2026-11-09 사용자 지시] 기계로 거를 수 있는 '저'를 판정 전에 코드가 먼저 채운다 (`run.sh prejudge`).

[왜] 11-09 사용자: "왜 자꾸 건너뛰는게 생기는거야? … 원인과 대안을 찾아봐" → "1~4까지 모두 진행해"
  550건을 1시간에 전수 판정하려다 그룹으로 묶어 스크립트로 사유를 찍고(spread 게이트를 헤드라인 덧붙이기로 통과),
  정작 '고' 확인(중복·배치·사건일)을 건너뛰었다. 판정 노력을 '고'에 모으려면 볼 필요 없는 건을 먼저 덜어야 한다.

[무엇을] 헤드라인이 아래 셋 중 하나에 **확실히** 걸리고, 어젠다 낱말(AI·데이터센터·모델·로봇·투자유치·인수·수주·출시 등)이
  **하나도 없을 때만** '저'로 채운다. 사유는 `자동:<코드>(<걸린 낱말>)` — spread 게이트는 '자동:' 사유를 집계에서 뺀다.
    스포츠 / 시황(주가·지수·ETF·수급) / 실적(분기 매출·영업이익·가이던스 — CATEGORY_RULES 0번)
  어젠다 낱말이 하나라도 있으면 손대지 않는다(사람이 판정). 확신이 없으면 넘기는 쪽으로 짰다.
  verdict-<날짜>.csv 가 이미 있으면 **빈 idx 만** 채운다(사람이 쓴 판정은 건드리지 않는다).
  남은 후보만 모은 목록 out/candidates-focus-<날짜>.txt 를 만든다 — 판정은 이 파일로 한다.

  bash cowork/run.sh prejudge          # 채우기 + focus 목록
  bash cowork/run.sh prejudge --dry    # 무엇이 자동 '저'가 되는지만 찍기
"""
import argparse, csv, json, os, re, sys, datetime

SPORTS = re.compile(r"\[KS\d?\]|\bKS\d\b|한국시리즈|프로야구|프로농구|프로배구|감독 \"|감독,|선발|홈런|만루|MVP|준우승|이닝|타석|\d+-\d+ (제압|승|패)|구자욱|이강철|PS \d+G")
MARKET = re.compile(r"순매수|순매도|수익률 \d|인버스|레버리지|SOXL|목표가|\[주간증시전망\]|\[마감시황\]|증시전망|주가동향|코스피 \d{4}|5000선|시가총액 .*(증발|사라)|시총 .*(증발|사라)|재산 .*증발|ETF.{0,6}상장|상장.{0,6}ETF|자사주 소각|배당|주가|증시|나스닥|코스피|코스닥|뉴욕증시|폭락|급락|급등|반등|매수세|시총")
EARN = re.compile(r"[1-4]Q\d\d|\d분기 (매출|영업이익|실적)|분기 실적|실적 (발표|주요 내용)|가이던스|어닝")
AGENDA = re.compile(r"AI|인공지능|에이전트|모델|데이터센터|AIDC|DC\b|GPU|HBM|반도체 (투자|공장|수주)|로봇|휴머노이드|자율주행|독파모|모두의|소버린|투자 유치|투자유치|인수|합병|수주|출시|공개|계약|MOU|IPO|상장 추진|규제|법안|몸값|기업가치|오픈AI|OpenAI|앤트로픽|앤스로픽|Anthropic|엔비디아|Nvidia|구글|Google|메타|Meta|마이크로소프트|Microsoft|\bMS\b|애플|아마존|테슬라|네이버|카카오|(?<![A-Za-z])KT(?![A-Za-z&])|LG유플러스|LGU\+|SK텔레콤|SKT", re.I)

def classify(head):
    if AGENDA.search(head):
        return None
    for code, rx in (("AI무관(스포츠)", SPORTS), ("시황실적", MARKET), ("시황실적", EARN)):
        m = rx.search(head)
        if m:
            return "자동:%s(%s)" % (code, m.group(0))
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    blind = os.path.join("out", "candidates-blind-%s.txt" % a.date)
    if not os.path.exists(blind):
        print("[prejudge] %s 없음 — run.sh blind 먼저" % blind); return 2
    lines = open(blind, encoding="utf-8").read().split("\n")
    heads, order = {}, []
    for l in lines:
        m = re.match(r"^([NLC]?\d+) \| [^|]+\| [^|]+\| [^|]*\| [^|]+\| (.*)$", l)
        if m:
            h = re.sub(r"⟨[^⟩]*⟩\s*", "", m.group(2))
            h = re.sub(r"\s*\[(후보만|반향|기자재|링크없음).*$", "", h).strip("[] ")
            heads[m.group(1)] = h; order.append(m.group(1))
    vpath = os.path.join("out", "verdict-%s.csv" % a.date)
    existing = {}
    if os.path.exists(vpath):
        for r in csv.DictReader(open(vpath, encoding="utf-8")):
            if (r.get("grade") or "").strip():
                existing[r["idx"].strip()] = r
    # [2026-11-09] 페이지 발행일이 7일 넘게 이른 옛 기사(구글뉴스 RSS 날짜 오기) — run.sh pagedate 결과
    pd = {}
    pdp = os.path.join("out", "pagedate-%s.json" % a.date)
    if os.path.exists(pdp):
        pd = json.load(open(pdp, encoding="utf-8"))
    else:
        print("[prejudge] [경고] %s 없음 — run.sh pagedate 를 먼저(옛 기사 걸러내기 생략됨)" % pdp)
    day = datetime.date.fromisoformat(a.date)
    auto = {}
    for i in order:
        if i in existing:
            continue
        v = pd.get(i) or {}
        if v.get("page") and not v.get("official") and (day - datetime.date.fromisoformat(v["page"])).days > 7:
            auto[i] = "자동:창밖(페이지 발행일 %s, 수집 날짜 %s)" % (v["page"], v.get("rss") or "?")
            continue
        c = classify(heads[i])
        if c:
            auto[i] = c
    print("[prejudge] 후보 %d건 / 이미 판정 %d건 / 자동 '저' %d건 / 사람 판정 대상 %d건"
          % (len(order), len(existing), len(auto), len(order) - len(existing) - len(auto)))
    for i, c in auto.items():
        print("   %-5s %s | %s" % (i, c, heads[i][:50]))
    if a.dry:
        return 0
    rows = []
    for i in order:
        if i in existing:
            r = existing[i]; rows.append([i, r["grade"], r.get("reason", ""), r.get("rank", "")])
        elif i in auto:
            rows.append([i, "저", auto[i], ""])
    with open(vpath, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f); w.writerow(["idx", "grade", "reason", "rank"]); w.writerows(rows)
    done = set(existing) | set(auto)
    fpath = os.path.join("out", "candidates-focus-%s.txt" % a.date)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write("# 사람 판정 대상만 (자동 '저' %d건·기판정 %d건 제외) — %s\n" % (len(auto), len(existing), a.date))
        _skip = False
        for l in lines:
            m = re.match(r"^([NLC]?\d+) \| ", l)
            if m:
                _skip = m.group(1) in done
            elif _skip and l.lstrip().startswith("↳"):
                continue                      # [2026-11-12] 뺀 건의 '같은 묶음' 줄도 함께 뺀다
            if m and _skip:
                continue
            f.write(l + "\n")
    print("[prejudge] 저장: %s (판정 %d행) / 판정 목록: %s" % (vpath, len(rows), fpath))
    return 0

if __name__ == "__main__":
    sys.exit(main())
