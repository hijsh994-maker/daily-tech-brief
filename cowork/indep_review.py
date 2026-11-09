#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[2026-11-09 사용자 지시] '고' 독립 검토 — 오늘 판정 맥락이 없는 별도 에이전트가 다시 본다 (`run.sh indep`).

[왜] 11-09 중복 2건·배치 1건을 전부 '내가 내 판단을 검증하는' 구조에서 놓쳤다(11-04 '남은 계획 ④').
  검토자에게는 **내 판정 사유를 주지 않는다**(답을 보여주면 확인만 해 온다). 기사 본문·30일 발송본·배치 규칙만 준다.

  bash cowork/run.sh indep --pack    # out/indep-pack-<날짜>.md + out/sent30-<날짜>.txt 생성
  → Agent 도구(general-purpose)에 docs/독립검토-프롬프트.md 를 그대로 주고 실행.
    검토자는 out/indep-review-<날짜>.csv (idx,고타당,중복,배치번호,이유) 를 쓴다.
  bash cowork/run.sh indep           # 내 판정(verdict·gocheck·placement)과 대조. 어긋난 건마다 '처리' 칸 요구
    처리 = '반영:<무엇을 바꿨나>' 또는 '유지:<왜 내 판단이 맞나, 10자 이상>'. 빈칸이면 종료코드 1.
"""
import argparse, csv, datetime, glob, json, os, re, sys

def load_cand(day):
    cand = {}
    for line in open(os.path.join("out", "prompt-%s.txt" % day), encoding="utf-8"):
        if line.startswith('{"idx"'):
            try: o = json.loads(line); cand[str(o["idx"])] = o
            except Exception: pass
        elif line.startswith('{"nolink_idx"'):
            try: o = json.loads(line); cand["N%s" % o["nolink_idx"]] = o
            except Exception: pass
    return cand

def gold(day):
    return [r for r in csv.DictReader(open(os.path.join("out", "verdict-%s.csv" % day), encoding="utf-8"))
            if r["grade"].strip() == "고"]

def brief_urls(day):
    """발송본(있으면)의 url -> (섹션, 헤드라인). 링크를 바꾼 '고'도 잡으려고."""
    p = os.path.join("out", "brief-%s.md" % day); out = {}
    if not os.path.exists(p): return out
    sec = None
    for line in open(p, encoding="utf-8"):
        m = re.match(r"^\*\*■\s*(.+?)\*\*", line)
        if m: sec = m.group(1).strip(); continue
        m = re.match(r"^\*\*\d+\\?\.\s*(.+?)\*\*.*?\]\((https?://[^)]+)\)", line)
        if m: out[m.group(2)] = (sec, m.group(1))
    return out

def pack(day, mnt):
    cand = load_cand(day)
    cp = os.path.join("out", "bodycache-%s.json" % day)
    cache = json.load(open(cp, encoding="utf-8")) if os.path.exists(cp) else {}
    gc = {}
    gp = os.path.join("out", "gocheck-%s.csv" % day)
    if os.path.exists(gp):
        gc = {r["idx"]: r for r in csv.DictReader(open(gp, encoding="utf-8"))}
    # 발송본에 실린 링크(바꾼 링크 포함)를 idx 와 잇기: placement 표의 url -> 헤드라인
    pl = {}
    pp = os.path.join("out", "placement-%s.csv" % day)
    if os.path.exists(pp):
        pl = {r["url"]: r for r in csv.DictReader(open(pp, encoding="utf-8"))}
    L = ["# 독립 검토 자료 %s — '고' %d건" % (day, len(gold(day))), "",
         "아래 기사마다 본문 앞부분이 있다. 판정 사유는 일부러 넣지 않았다.", ""]
    for r in gold(day):
        i = r["idx"]; c = cand.get(i) or {}
        url = c.get("url") or ""
        body = cache.get(url, "")
        L += ["## idx %s" % i, "- 헤드라인: %s" % c.get("headline", ""), "- 매체: %s / 발행일: %s" % (c.get("outlet", ""), c.get("pub_date", "")),
              "- 후보 링크: %s" % url]
        alt = [u for u in pl if u != url and (pl[u].get("헤드라인", "")[:12] in c.get("headline", "") or c.get("headline", "")[:12] in pl[u].get("헤드라인", ""))]
        if alt:
            L.append("- 발송본에 쓴 링크: %s" % alt[0])
            body = body or cache.get(alt[0], "")
        L += ["- 본문 앞부분: %s" % (re.sub(r"\s+", " ", body)[:900] if body else "(본문 캐시 없음 — 헤드라인과 링크로 판단, 필요하면 WebFetch)"), ""]
    open(os.path.join("out", "indep-pack-%s.md" % day), "w", encoding="utf-8").write("\n".join(L))
    # 30일 발송본 원문(헤드라인+주요 내용)
    d0 = datetime.date.fromisoformat(day); S = []
    for p in sorted(glob.glob(os.path.join(mnt, "out", "brief-20??-??-??.md"))):
        ds = os.path.basename(p)[6:16]
        try: d = datetime.date.fromisoformat(ds)
        except ValueError: continue
        if d0 - datetime.timedelta(days=30) <= d < d0:
            for line in open(p, encoding="utf-8"):
                if re.match(r"^\*\*\d+\\?\.", line) or "주요 내용" in line:
                    S.append("%s | %s" % (ds[5:], line.strip()[:300]))
    open(os.path.join("out", "sent30-%s.txt" % day), "w", encoding="utf-8").write("\n".join(S))
    print("[indep] 자료: out/indep-pack-%s.md ('고' %d건) / out/sent30-%s.txt (30일 발송 %d줄)"
          % (day, len(gold(day)), day, len(S)))
    print("[indep] 다음: Agent(general-purpose)에 docs/독립검토-프롬프트.md 내용을 날짜만 바꿔 그대로 준다 → out/indep-review-%s.csv" % day)

def check(day):
    rp = os.path.join("out", "indep-review-%s.csv" % day)
    if not os.path.exists(rp):
        print("[indep] [실패] %s 없음 — `run.sh indep --pack` 후 독립 검토 에이전트를 돌릴 것" % rp); return 1
    rows = list(csv.DictReader(open(rp, encoding="utf-8")))
    cols = rows[0].keys() if rows else []
    rv = {r["idx"].strip(): r for r in rows}
    gc = {}
    gp = os.path.join("out", "gocheck-%s.csv" % day)
    if os.path.exists(gp):
        gc = {r["idx"].strip(): r for r in csv.DictReader(open(gp, encoding="utf-8"))}
    cand = load_cand(day)
    pl = {}
    pp = os.path.join("out", "placement-%s.csv" % day)
    if os.path.exists(pp):
        pl = {r["url"]: r for r in csv.DictReader(open(pp, encoding="utf-8"))}
    errs, dis = [], 0
    for r in gold(day):
        i = r["idx"]; x = rv.get(i)
        tag = "%s %s" % (i, ((cand.get(i) or {}).get("headline") or "")[:28])
        if not x:
            errs.append("검토 행 없음: " + tag); continue
        why = []
        if (x.get("고타당") or "").strip().startswith("아니"):
            why.append("검토자: '고' 아님 — %s" % (x.get("이유") or "")[:80])
        dup = (x.get("중복") or "").strip()
        myflow = ((gc.get(i) or {}).get("흐름") or "").strip()
        if dup and dup != "없음" and myflow == "신규":
            why.append("검토자: 기발송 %s / 내 흐름 신규" % dup[:40])
        u = (cand.get(i) or {}).get("url", "")
        mine = (pl.get(u) or {}).get("배치번호", "").strip()
        theirs = (x.get("배치번호") or "").strip()
        if mine and theirs and mine != theirs:
            why.append("배치 검토자 %s / 내 표 %s" % (theirs, mine))
        if why:
            dis += 1
            done = (x.get("처리") or "").strip()
            ok = done.startswith("반영:") or (done.startswith("유지:") and len(done) >= 13)
            print("  [불일치] %s — %s | 처리: %s" % (tag, " / ".join(why), done or "(빈칸)"))
            if not ok:
                errs.append("불일치인데 처리 칸이 '반영:…' / '유지:<10자 이상>' 가 아님: " + tag)
    print("[indep] '고' %d건 / 검토 %d행 / 불일치 %d건" % (len(gold(day)), len(rows), dis))
    for e in errs: print("  [실패] " + e)
    if errs:
        print("[indep] 실패 %d건 — 불일치마다 실제로 다시 보고 처리 칸을 채울 것" % len(errs)); return 1
    print("[indep] 통과"); return 0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--mnt", required=True)
    ap.add_argument("--pack", action="store_true")
    a = ap.parse_args()
    if a.pack:
        pack(a.date, a.mnt); return 0
    return check(a.date)

if __name__ == "__main__":
    sys.exit(main())
