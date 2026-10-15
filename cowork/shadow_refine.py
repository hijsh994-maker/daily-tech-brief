#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[2026-10-08] 소스 정리 섀도 실험 — 후보만 따로 만들고 실제 발송본과 비교한다.

실제 파이프라인(out/refined-*.json 등)은 건드리지 않는다. 같은 입력으로
refine.py 를 두 번(--no-links) 돌려 out/shadow/<날짜>/{base,cut}/ 에 쓴다.
  base = 전체 소스(대조군)   cut = RSS 제외 + 쿼리 9개 제외(config/shadow_exclude.yaml)
그다음 그날 실제 발송본·'고' 판정이 cut 후보에 남는지 대조해
out/shadow/log.jsonl 에 한 줄씩 남긴다.

  python3 cowork/shadow_refine.py --date 2026-10-08 [--base-dir <작업본>]
"""
import argparse, contextlib, csv, importlib.util, io, json, os, re, sys, yaml

def norm(u): return re.sub(r'^https?://(www\.|m\.)?', '', u or '').split('#')[0].split('?')[0].rstrip('/')
def hk(h): return re.sub(r'[\W_]', '', (h or '').lower())[:20]

def run_refine(base, day, mode, excl, outdir):
    spec = importlib.util.spec_from_file_location("refine_shadow_" + mode, os.path.join(base, "refine.py"))
    R = importlib.util.module_from_spec(spec); spec.loader.exec_module(R)
    real_out = R.OUTDIR
    os.makedirs(outdir, exist_ok=True)
    ld0, ldc, ldb, ld1, ld2 = R.load_tier0, R.load_community, R.load_buzz, R.load_tier1, R.load_tier2
    def wrap(f):
        def g(*a, **k):
            R.OUTDIR = real_out
            try: return f(*a, **k)
            finally: R.OUTDIR = outdir
        return g
    R.load_tier0, R.load_community, R.load_buzz = wrap(ld0), wrap(ldc), wrap(ldb)
    t2 = wrap(ld2)
    if mode == "cut":
        ex_src = set(excl.get("exclude_sources") or [])
        ex_q = set(excl.get("exclude_queries") or [])
        if "rss" in ex_src:
            R.load_tier1 = lambda *a, **k: []
        def t2f(day):
            items = t2(day); keep = []
            for it in items:
                qs = set(it.get("queries") or [])
                if qs and qs <= ex_q:      # 제외 쿼리에만 걸린 기사만 뺀다
                    continue
                keep.append(it)
            return keep
        R.load_tier2 = t2f
    else:
        R.load_tier2 = t2
    R.OUTDIR = outdir
    sys.argv = ["refine.py", "--date", day, "--min-score", "6", "--max-items", "300",
                "--max-rescue", "150", "--no-links"]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        R.main()
    m = re.search(r"입력\s+tier0 (\d+).*?tier1\(RSS\) (\d+) / tier2\(구글\) (\d+)", buf.getvalue())
    return {"input": [int(x) for x in m.groups()] if m else None}

def load_cands(d):
    items = []
    for f in ("refined", "ranked"):
        p = os.path.join(d, "%s.json" % f)
        if os.path.exists(p):
            j = json.load(open(p, encoding="utf-8"))
            items += j.get("items", [])
    idx_u, idx_h = {}, {}
    for it in items:
        for u in [it.get("url")] + (it.get("member_urls") or []):
            if u: idx_u.setdefault(norm(u), it)
        for t in [it.get("title") or it.get("headline")] + (it.get("members") or []):
            if t: idx_h.setdefault(hk(t), it)
    nref = len(json.load(open(os.path.join(d, "refined.json"), encoding="utf-8"))["items"])
    return idx_u, idx_h, nref

def present(it_base, cand):
    """base 클러스터가 cut 후보에도 있는가(대표/멤버 URL·제목 중 하나라도 겹치면)."""
    iu, ih, _ = cand
    for u in [it_base.get("url")] + (it_base.get("member_urls") or []):
        if u and norm(u) in iu: return iu[norm(u)]
    for t in [it_base.get("title")] + (it_base.get("members") or []):
        if t and hk(t) in ih: return ih[hk(t)]
    return None

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--date", required=True)
    ap.add_argument("--base-dir", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--log-dir", default=None, help="log.jsonl 위치(기본: <base>/out/shadow)")
    a = ap.parse_args(); day = a.date; base = a.base_dir
    excl = yaml.safe_load(open(os.path.join(base, "config", "shadow_exclude.yaml"), encoding="utf-8"))
    sd = os.path.join(base, "out", "shadow", day)
    info = {}
    for mode in ("base", "cut"):
        od = os.path.join(sd, mode)
        info[mode] = run_refine(base, day, mode, excl, od)
        for f in ("refined", "ranked", "preview"):
            ext = "txt" if f == "preview" else "json"
            src = os.path.join(od, "%s-%s.%s" % (f, day, ext))
            if os.path.exists(src): os.replace(src, os.path.join(od, "%s.%s" % (f, ext)))
    B = load_cands(os.path.join(sd, "base")); C = load_cands(os.path.join(sd, "cut"))
    O = os.path.join(base, "out")
    # 실제 발송본 / '고'
    sent = re.findall(r'^\*\*\d+\\\.\s*(.+?)\*\*\s*\((.+?),\s*\[원문보기\]\((https?://[^)]+)\)',
                      open(os.path.join(O, "brief-%s.md" % day), encoding="utf-8").read(), re.M)
    p = open(os.path.join(O, "prompt-%s.txt" % day), encoding="utf-8").read()
    pit = {str(json.loads(m.group(0))["idx"]): json.loads(m.group(0)) for m in re.finditer(r'^\{"idx".*$', p, re.M)}
    go = [pit[r["idx"]] for r in csv.DictReader(open(os.path.join(O, "verdict-%s.csv" % day), encoding="utf-8"))
          if r["grade"] == "고" and r["idx"] in pit]
    def check(head, url):
        bu, bh, _ = B
        b = bu.get(norm(url)) or bh.get(hk(head))
        if b is None:
            # base 에도 없으면(웹검색·수동 보정) cut 에서 직접 찾는다
            c = C[0].get(norm(url)) or C[1].get(hk(head))
            return "base밖", c is not None
        return "base", present(b, C) is not None
    res = {"sent": [], "go": []}
    for h, o, u in sent: res["sent"].append((h,) + check(h, u))
    for it in go: res["go"].append((it.get("headline"),) + check(it.get("headline"), it.get("url")))
    def summ(lst):
        inb = [x for x in lst if x[1] == "base"]
        kept = sum(1 for x in inb if x[2]); lost = [x[0] for x in inb if not x[2]]
        outside = [x[0] for x in lst if x[1] != "base"]
        return {"n": len(lst), "in_base": len(inb), "kept": kept, "lost": lost, "outside": outside}
    row = {"date": day, "end_date": excl.get("end_date"),
           "input_base": info["base"]["input"], "input_cut": info["cut"]["input"],
           "cands_base": B[2], "cands_cut": C[2],
           "sent": summ(res["sent"]), "go": summ(res["go"])}
    ld = a.log_dir or os.path.join(base, "out", "shadow")
    os.makedirs(ld, exist_ok=True)
    lp = os.path.join(ld, "log.jsonl")
    rows = [json.loads(l) for l in open(lp, encoding="utf-8")] if os.path.exists(lp) else []
    rows = [r for r in rows if r["date"] != day] + [row]
    rows.sort(key=lambda r: r["date"])
    with open(lp, "w", encoding="utf-8") as f:
        for r in rows: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    ib, ic = row["input_base"] or [0, 0, 0], row["input_cut"] or [0, 0, 0]
    print("[shadow] %s 입력 %d -> %d건 (RSS %d->%d, 구글 %d->%d) / 후보 %d -> %d건"
          % (day, sum(ib), sum(ic), ib[1], ic[1], ib[2], ic[2], B[2], C[2]))
    for k, nm in (("sent", "발송본"), ("go", "'고'")):
        s = row[k]
        print("[shadow]   %s %d건 중 대조군 후보에 있던 %d건 -> 섀도에 남음 %d건 / 사라짐 %d건 (대조군에도 없던 수동·웹검색 %d건 제외)"
              % (nm, s["n"], s["in_base"], s["kept"], len(s["lost"]), len(s["outside"])))
        for t in s["lost"]: print("[shadow]     - 사라짐: %s" % t[:60])

if __name__ == "__main__":
    main()
