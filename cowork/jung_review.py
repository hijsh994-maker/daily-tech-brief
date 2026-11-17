#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[2026-11-16 사용자 지시] '중' 재검토 — 본문이 있는 '중'을 별도 에이전트가 다시 읽고 '고' 후보만 골라 온다 (`run.sh jung`).

[왜] 11-16 사용자가 '중' 3건(LLM 재킹·디지털 리얼티·피클 인터뷰)을 '고'감으로 지적했다. '중'은 제목만 보고 내리는 일이 잦고
  아무도 다시 안 본다(11-04 facts 게이트 머리말과 같은 진단). '중' 300건을 내가 전부 다시 읽으면 20~30분이 늘고
  한 대화에 판정이 쌓여 뒤쪽이 거칠어진다 → 본문이 이미 있는 '중'만 **판정 사유 없이** 별도 에이전트에 준다(5~10분, 병렬).
  본문이 없는 '중'은 judge 게이트 J1 범위(사람이 고른 통로·보안 낱말)만 find 로 확보한다 — 전부 찾지 않는다.
  사용자: "10분 정도는 괜찮을 것 같아" (11-16).

  bash cowork/run.sh jung --pack    # out/jung-pack-<날짜>.md (본문 있는 '중', '발송근거:' 건 제외 — J2 가 본다)
  → Agent 도구(general-purpose)에 docs/중재검토-프롬프트.md 를 날짜만 바꿔 그대로 준다.
    검토자는 out/jung-review-<날짜>.csv (idx,올림,중복,이유) 를 쓴다. 올림=예 인 건만 적어도 된다.
  bash cowork/run.sh jung           # 올림=예 마다 내 verdict 사유에 `중재검토:` 가 있어야 통과
    올렸으면 '고' 로 바꾸고 사유에 `중재검토:반영 …`, 안 올리면 '중' 사유에 `중재검토:유지:<본문 근거 10자 이상>`.
    verdict 가 자동 호출한다(2026-11-17 판정부터 강제 — 검토 파일이 없으면 실패).
"""
import argparse, csv, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bodypeek  # noqa: E402
# bodypeek 은 자기 위치(저장소) 기준 out/ 을 읽는다. verdict 는 작업본(W)에서 push 전에 돌므로 작업본 out/ 을 보게 한다
#   (11-16 사본 시험: 저장소에 당일 candidates-blind 가 없어 judge·jung 이 멈췄다).
if os.path.isdir(os.path.join(os.getcwd(), "out")):
    bodypeek.OUT = os.path.join(os.getcwd(), "out")

FROM = "2026-11-17"
SKIP = re.compile(r"발송근거:")  # 기발송 근거 건은 judge J2 가 앵글로 본다


def pack(day):
    meta, urls, cache = bodypeek.load(day)
    rows = list(csv.DictReader(open(os.path.join("out", "verdict-%s.csv" % day), encoding="utf-8")))
    L, n = [], 0
    for r in rows:
        i = r["idx"].strip()
        if r["grade"].strip() != "중" or SKIP.search(r.get("reason") or ""):
            continue
        url = urls.get(i, "")
        body = cache.get(url) if url else None
        if isinstance(body, dict):
            body = body.get("text") or body.get("body") or ""
        lead = bodypeek.clean(body or "")[:700]
        if len(lead) < 200:
            continue
        m = meta.get(i) or {}
        n += 1
        L += ["## [%s] %s" % (i, m.get("head", "")),
              "- 통로: %s / 매체: %s / 발행일: %s / 보도: %s" % (m.get("route", ""), m.get("outlet", ""), m.get("date", ""), m.get("cov", "")),
              "- 링크: %s" % url, "", lead, ""]
    head = ["# '중' 재검토 자료 %s — %d건" % (day, n), "",
            "판정자가 '중'으로 둔 기사 중 본문이 있는 것. 판정 사유는 일부러 넣지 않았다.", ""]
    p = os.path.join("out", "jung-pack-%s.md" % day)
    open(p, "w", encoding="utf-8").write("\n".join(head + L))
    print("[jung] 자료: %s ('중' %d건, 본문 있는 것만·발송근거 건 제외)" % (p, n))
    print("[jung] 다음: Agent(general-purpose)에 docs/중재검토-프롬프트.md 를 날짜만 바꿔 그대로 준다 → out/jung-review-%s.csv" % day)
    return 0


def check(day):
    if day < FROM:
        print("[jung] %s 이전 판정은 강제 대상 아님" % FROM)
    rp = os.path.join("out", "jung-review-%s.csv" % day)
    if not os.path.exists(rp):
        if day < FROM:
            return 0
        print("[jung] [실패] %s 없음 — run.sh jung --pack 뒤 재검토 에이전트를 돌릴 것" % rp)
        return 1
    V = {r["idx"].strip(): r for r in csv.DictReader(open(os.path.join("out", "verdict-%s.csv" % day), encoding="utf-8"))}
    ups = [x for x in csv.DictReader(open(rp, encoding="utf-8")) if (x.get("올림") or "").strip() == "예"]
    try:
        meta = bodypeek.load(day)[0]
    except SystemExit:
        meta = {}
    errs = []
    for x in ups:
        i = (x.get("idx") or "").strip()
        v = V.get(i)
        if not v:
            errs.append("%s — verdict 에 없는 idx" % i); continue
        rs = v.get("reason") or ""
        g = v["grade"].strip()
        mark = re.search(r"중재검토:(반영|유지:(.{10,}))", rs)
        # 헤드라인을 같이 찍는다 — 11-16 시험에서 검토자가 idx 46(엔비디아·삼전닉스)을 '최태원 세미나'로 잘못 적었다
        print("  [올림제안] %-5s 지금 %s | 제목: %s\n             검토자: %s | 내 처리: %s"
              % (i, g, (meta.get(i) or {}).get("head", "?")[:50], (x.get("이유") or "")[:70],
                 (mark.group(0)[:60] if mark else "(없음)")))
        if not mark:
            errs.append("%s — 사유에 `중재검토:반영` 또는 `중재검토:유지:<본문 근거>` 없음" % i)
        elif mark.group(1) == "반영" and g != "고":
            errs.append("%s — '반영'이라 적었는데 등급이 %s" % (i, g))
    print("[jung] 올림 제안 %d건 / 미처리 %d건" % (len(ups), len(errs)))
    for e in errs:
        print("  [실패] " + e)
    return 1 if (errs and day >= FROM) else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--pack", action="store_true")
    a = ap.parse_args()
    return pack(a.date) if a.pack else check(a.date)


if __name__ == "__main__":
    sys.exit(main())
