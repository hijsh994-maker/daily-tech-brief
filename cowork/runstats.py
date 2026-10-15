#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""하루 실행이 '일을 했는지'를 한 화면으로 찍는다. [2026-09-22 신설]

[왜] 사용자 지시로 09-22 부터 검토 없이 자동 발송한다. 그러면 판정을 제대로
  했는지 확인할 사람이 없다. 실제로 09-22 에 557건 전수 판정을 했다고 보고하고
  실제로는 키워드 스크립트로 417건(75%)에 같은 문장 두 개를 붙였다.
  사용자: *"왜 557건 전수판정을 하지 않은거야? 이건 여러차례 경고한건인데
  왜 고쳐지지 않는거지?"* / *"또 고로 판정한거 몇개만 하는거 아니지? 불안하다"*
  게이트는 이미 있지만 게이트 실패가 발송을 막지 않는 설정이라(사용자 선택)
  **숫자를 알림 맨 앞에 띄워 30초 안에 눈으로 확인할 수 있게 한다.**

[무엇을] 판정 건수·사유 다양성·'고' 본문 확인율·게이트 7종 결과를 한 덩어리로
  찍는다. 이 출력을 그대로 실행 알림과 보고 맨 위에 붙인다.

  bash cowork/run.sh stats            # 사람이 읽는 형태
  bash cowork/run.sh stats --oneline  # 알림 첫 줄용 한 줄
"""
import argparse, csv, collections, datetime, os, subprocess, sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "out")

GATES = [
    ("cats",    ["cowork/catcheck.py", "--stem", "brief"]),
    ("spread",  ["cowork/verdict_spread.py"]),
    ("newfact", ["cowork/newfact_check.py"]),
    ("repeat",  ["cowork/repeat_gate.py"]),
    ("agenda",  ["cowork/agenda_gate.py"]),
    ("peek",    ["cowork/bodypeek.py", "--check"]),
]


def verdict_stats(day):
    p = os.path.join(OUT, "verdict-%s.csv" % day)
    if not os.path.exists(p):
        return None
    rows = [r for r in csv.DictReader(open(p, encoding="utf-8")) if r.get("idx")]
    by = collections.defaultdict(list)
    for r in rows:
        by[r.get("grade", "").strip()].append((r.get("reason") or "").strip())
    out = {"total": len(rows)}
    for g in ("고", "중", "저"):
        rs = by.get(g, [])
        c = collections.Counter(rs)
        out[g] = {
            "n": len(rs),
            "uniq": len(c),
            "top": (c.most_common(1)[0][1] if c else 0),
        }
    return out


def peek_stats(day):
    """'고' 몇 건의 본문을 실제로 열었는가."""
    import json
    try:
        sys.path.insert(0, os.path.join(BASE, "cowork"))
        import bodypeek as BP
        gos = BP.go_set(day) or []
        meta, urls, cache = BP.load(day)
        have = [i for i in gos if cache.get(urls.get(i) or "")]
        seen = []
        pp = BP.peek_path(day)
        if os.path.exists(pp):
            seen = json.load(open(pp, encoding="utf-8")).get("peeked", [])
        return len(gos), len(have), len([i for i in have if i in seen])
    except (Exception, SystemExit):
        # bodypeek.load() 는 캐시가 없으면 SystemExit 을 던진다(2026-10-15 클라우드 리허설에서 확인).
        # 본문 캐시가 없어도 stats 한 줄은 나와야 한다 -> '고 본문확인 ?' 로 표시.
        return None


def run_gates(day):
    res = []
    for name, cmd in GATES:
        full = [sys.executable, os.path.join(BASE, cmd[0]), "--date", day] + cmd[1:]
        if name == "repeat":
            full += ["--mnt", BASE]
        try:
            p = subprocess.run(full, cwd=os.getcwd(), capture_output=True,
                               timeout=90)
            res.append((name, p.returncode == 0))
        except Exception:
            res.append((name, None))
    # 사례집 무결성은 마운트에서 돈다
    try:
        p = subprocess.run([sys.executable, os.path.join(BASE, "cowork/casebook.py"),
                            "--check"], cwd=BASE, capture_output=True, timeout=90)
        res.append(("case", p.returncode == 0))
    except Exception:
        res.append(("case", None))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--oneline", action="store_true")
    a = ap.parse_args()

    v = verdict_stats(a.date)
    if not v:
        print("[stats] 판정 파일이 없습니다: out/verdict-%s.csv" % a.date)
        return 1
    pk = peek_stats(a.date)
    gates = run_gates(a.date)
    bad = [n for n, ok in gates if ok is not True]

    mid, low, hi = v["중"], v["저"], v["고"]
    peek_txt = ("고 본문확인 %d/%d" % (pk[2], pk[1])) if pk else "고 본문확인 ?"
    one = ("판정 %d건(고 %d·중 %d·저 %d) / 사유 중 %d종·저 %d종 / %s / 게이트 %s"
           % (v["total"], hi["n"], mid["n"], low["n"], mid["uniq"], low["uniq"],
              peek_txt,
              ("전부 통과" if not bad else "실패 " + " ".join(bad))))
    if a.oneline:
        print(one)
        return 0

    print("=" * 66)
    print("[stats] %s 실행 검증" % a.date)
    print("=" * 66)
    print("  전수 판정      %d건  (고 %d / 중 %d / 저 %d)"
          % (v["total"], hi["n"], mid["n"], low["n"]))
    for g, d in (("중", mid), ("저", low)):
        if d["n"]:
            print("    '%s' 사유    %d종 / %d건   최다사유 %d건(%.0f%%)"
                  % (g, d["uniq"], d["n"], d["top"], d["top"] / d["n"] * 100))
    print("    ↑ 최다사유 비중이 높거나 종류가 적으면 대량 라벨링입니다(spread 게이트).")
    if pk:
        print("  '고' 본문확인  %d/%d건 열람   (본문 미확보 %d건은 게이트가 못 막음)"
              % (pk[2], pk[1], pk[0] - pk[1]))
    print("  게이트         %s"
          % ("  ".join("%s%s" % (n, "" if ok else "(실패)") for n, ok in gates)))
    print()
    print("[알림 첫 줄용]")
    print(one)
    return 0


if __name__ == "__main__":
    sys.exit(main())
