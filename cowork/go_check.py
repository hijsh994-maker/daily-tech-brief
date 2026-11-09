#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[2026-11-09 사용자 지시] '고' 확인표 — 건마다 리드·30일 발송 이력·흐름을 직접 확인한 흔적을 남긴다 (`run.sh gocheck`).

[왜] 11-09 브룩필드-네이버(10-19·10-28·11-02 기발송)·텍사스 승인 중단(11-05 발송본 주요 내용에 기실림)을 '고'로 다시 보냈다.
  판정 사유에 '이미 알려진 파트너십일 수 있어'라고 써 놓고도 발송본을 찾아보지 않았고, repeat·hist 는 제목 낱말 대조라
  금액 표기(12兆 vs 90억달러)·주요 내용 속 사실을 못 잡았고, facts '흐름' 칸은 스크립트 기본값('신규')으로 채웠다.
  사용자: "왜 자꾸 건너뛰는게 생기는거야? … 원인과 대안을 찾아봐" → "1~4까지 모두 진행해"

[무엇을] out/gocheck-<날짜>.csv  — '고' 1건당 1행
    idx,헤드라인,주체검색어,리드인용,검색결과,흐름,판단
    주체검색어 : 핵심 주체·제품·사업 이름을 '|' 로 (국문·영문 표기 모두. 예: 브룩필드|Brookfield)
    리드인용   : 본문 앞부분에서 그대로 복사한 15자 이상(본문이 없으면 '본문없음:<사유>')
    검색결과   : **코드가 채운다**(--search). 최근 30일 out/brief-*.md 의 헤드라인·주요 내용에서 검색어가 걸린 기사
    흐름       : 신규 | MM-DD (이미 실은 같은 사건의 발송일)
    판단       : 흐름=MM-DD 이면 '새사실:<그때 없던 것>' / 검색결과가 있는데 신규라 적으면 '다른 사건:<왜 다른가>'

  bash cowork/run.sh gocheck --template   # '고' 행 만들기(있는 행은 보존)
  (주체검색어·리드인용 기입)
  bash cowork/run.sh gocheck --search     # 30일 발송본 검색 결과를 표에 채움
  (흐름·판단 기입 — 검색결과를 실제로 읽고)
  bash cowork/run.sh gocheck              # 검사. 실패 = 종료코드 1 (verdict 가 자동 호출, send 는 경고)

[검사] 행 없음 / 검색어 없음 / 리드인용이 본문에 없음 / 검색을 안 돌림(표의 결과 ≠ 지금 검색 결과) /
  검색 결과가 있는데 신규이면서 '다른 사건:' 없음 / 흐름=MM-DD 인데 '새사실:' 없음 / facts '흐름' 칸과 불일치.
"""
import argparse, csv, datetime, glob, json, os, re, sys

COLS = ["idx", "헤드라인", "주체검색어", "리드인용", "검색결과", "흐름", "판단"]

def norm(s):
    return re.sub(r"[\s　·…\.\,\"'“”‘’\[\]\(\)]+", "", s or "").lower()

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

def load_gold(day):
    rows = list(csv.DictReader(open(os.path.join("out", "verdict-%s.csv" % day), encoding="utf-8")))
    return [r["idx"].strip() for r in rows if r["grade"].strip() == "고"]

def sent_items(mnt, day, days=30):
    d0 = datetime.date.fromisoformat(day)
    out = []
    for p in sorted(glob.glob(os.path.join(mnt, "out", "brief-20??-??-??.md"))):
        ds = os.path.basename(p)[6:16]
        try: d = datetime.date.fromisoformat(ds)
        except ValueError: continue
        if not (d0 - datetime.timedelta(days=days) <= d < d0):
            continue
        cur = None
        for line in open(p, encoding="utf-8"):
            m = re.match(r"^\*\*\d+\\?\.\s*(.+?)\*\*", line)
            if m:
                cur = {"date": ds[5:], "head": m.group(1).strip(), "text": m.group(1)}
                out.append(cur); continue
            if cur is not None and ("주요 내용" in line or "시사점" in line):
                cur["text"] += " " + line.strip()
    return out

def search(items, terms):
    hits = []
    ts = [t.strip() for t in (terms or "").split("|") if t.strip()]
    # 영문·숫자 검색어는 낱말 경계로(ING 가 'thinking' 에 걸리지 않게), 한글은 부분 일치로
    rx = [re.compile(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(t), re.I) if re.fullmatch(r"[A-Za-z0-9 .+&-]+", t)
          else re.compile(re.escape(t), re.I) for t in ts]
    for it in items:
        if any(x.search(it["text"]) for x in rx):
            hits.append("%s %s" % (it["date"], it["head"][:40]))
    return " ; ".join(hits) if hits else "없음"

def body_of(cache, c):
    u = (c or {}).get("url")
    return cache.get(u, "") if u else ""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--mnt", required=True)
    ap.add_argument("--template", action="store_true")
    ap.add_argument("--search", action="store_true")
    a = ap.parse_args()
    path = os.path.join("out", "gocheck-%s.csv" % a.date)
    gold = load_gold(a.date)
    cand = load_cand(a.date)
    rows = {}
    if os.path.exists(path):
        for r in csv.DictReader(open(path, encoding="utf-8")):
            rows[r["idx"].strip()] = r

    def save():
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLS); w.writeheader()
            for i in gold:
                if i in rows: w.writerow({k: rows[i].get(k, "") for k in COLS})
            for i, r in rows.items():
                if i not in gold: w.writerow({k: r.get(k, "") for k in COLS})

    if a.template:
        for i in gold:
            if i not in rows:
                rows[i] = {k: "" for k in COLS}
                rows[i]["idx"] = i
                rows[i]["헤드라인"] = (cand.get(i) or {}).get("headline", "")
        save()
        print("[gocheck] 표 저장: %s ('고' %d건). 주체검색어·리드인용을 채운 뒤 --search" % (path, len(gold)))
        return 0

    items = sent_items(a.mnt, a.date)
    if a.search:
        n = 0
        for i in gold:
            r = rows.get(i)
            if r and r.get("주체검색어", "").strip():
                r["검색결과"] = search(items, r["주체검색어"]); n += 1
        save()
        print("[gocheck] 최근 30일 발송 %d건에서 %d행 검색 → %s" % (len(items), n, path))
        for i in gold:
            r = rows.get(i) or {}
            print("   %-5s [%s] %s" % (i, r.get("주체검색어", "")[:24], (r.get("검색결과") or "-")[:150]))
        print("[gocheck] 검색결과를 **읽고** 흐름(신규|MM-DD)·판단을 채울 것")
        return 0

    # 검사
    cp = os.path.join("out", "bodycache-%s.json" % a.date)
    cache = json.load(open(cp, encoding="utf-8")) if os.path.exists(cp) else {}
    facts = {}
    fp = os.path.join("out", "facts-%s.csv" % a.date)
    if os.path.exists(fp):
        facts = {r["idx"].strip(): r for r in csv.DictReader(open(fp, encoding="utf-8"))}
    errs, warns = [], []
    if not os.path.exists(path):
        print("[gocheck] [실패] %s 없음 — `run.sh gocheck --template` 부터" % path); return 1
    for i in gold:
        r = rows.get(i)
        tag = "%s %s" % (i, ((cand.get(i) or {}).get("headline") or "")[:28])
        if not r:
            errs.append("행 없음: " + tag); continue
        terms = (r.get("주체검색어") or "").strip()
        if not terms:
            errs.append("주체검색어 없음: " + tag); continue
        fresh = search(items, terms)
        if (r.get("검색결과") or "").strip() != fresh.strip():
            errs.append("검색 결과가 표와 다름(검색을 안 돌렸거나 검색어를 바꿈) — `--search` 후 다시 읽을 것: " + tag)
        q = (r.get("리드인용") or "").strip()
        body = body_of(cache, cand.get(i))
        if q.startswith("본문없음:"):
            if len(q) < 12: errs.append("본문없음 사유가 짧음: " + tag)
            else: warns.append("본문 없이 판정: " + tag)
        elif len(norm(q)) < 15:
            errs.append("리드인용 15자 미만: " + tag)
        elif body and norm(q) not in norm(body):
            errs.append("리드인용이 본문에 없음(본문에서 그대로 복사할 것): " + tag)
        elif not body:
            warns.append("본문 캐시 없음 — 리드인용 대조 못 함: " + tag)
        flow = (r.get("흐름") or "").strip()
        judge = (r.get("판단") or "").strip()
        if not re.match(r"^(신규|\d\d-\d\d)$", flow):
            errs.append("흐름이 '신규' 또는 MM-DD 가 아님(%r): %s" % (flow, tag)); continue
        if flow == "신규" and fresh != "없음" and "다른 사건:" not in judge:
            errs.append("30일 발송본에 검색어가 걸렸는데 신규로 적고 '다른 사건:<이유>' 가 없음: " + tag)
        if flow != "신규":
            if "새사실:" not in judge:
                errs.append("흐름=%s 인데 판단에 '새사실:' 이 없음: %s" % (flow, tag))
            if flow not in fresh:
                warns.append("흐름 날짜 %s 가 검색결과에 없음 — 검색어를 넓힐 것: %s" % (flow, tag))
        fr = facts.get(i)
        if fr is not None and (fr.get("흐름") or "").strip() != flow:
            errs.append("facts '흐름'(%s) 과 확인표 흐름(%s) 불일치: %s" % (fr.get("흐름"), flow, tag))
    print("[gocheck] '고' %d건 / 표 %d행 / 최근 30일 발송 %d건 대조" % (len(gold), len(rows), len(items)))
    for w in warns: print("  [경고] " + w)
    for e in errs: print("  [실패] " + e)
    if errs:
        print("[gocheck] 실패 %d건 — 표를 실제로 확인해 채울 것. 문구만 채우는 길은 없다(검색은 코드가 다시 돌린다)." % len(errs))
        return 1
    print("[gocheck] 통과")
    return 0

if __name__ == "__main__":
    sys.exit(main())
