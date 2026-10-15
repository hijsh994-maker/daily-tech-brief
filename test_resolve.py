# -*- coding: utf-8 -*-
"""
test_resolve.py - tier0 로그 항목에 대한 link_resolution 성공률 측정.

collect.py 가 쌓은 index/articles.sqlite 를 조회한다.
매일 실행해 기록하면 인덱스가 깊어질수록 해결률이 오르는지 추적할 수 있다.

사용: python test_resolve.py <trend_log.md>
출력: resolve_bench.txt (누적 append), resolve_bench_detail.json
"""
import json
import os
import re
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect import DB, resolve  # noqa: E402
import naver_api  # noqa: E402


def parse_adot(md):
    sec = md.split("## 텔레그램 - 에이닷비즈뉴스레터")
    if len(sec) < 2:
        return []
    lines = [l.rstrip() for l in sec[1].split("\n")]
    items, pending = [], None
    meta = re.compile(r"^ㄴ(\S+)\s+(\d{4}\.\d{2}\.\d{2})(?:\s*관련뉴스\s*(\d+)건)?")
    for l in lines:
        s = l.strip()
        m = meta.match(s)
        if m and pending:
            items.append({"title": pending, "outlet": m.group(1), "date": m.group(2),
                          "related": int(m.group(3)) if m.group(3) else 0})
            pending = None
        elif s and not s.startswith(("ㄴ", "**", "#", "신규", "총 ", "7월")) and len(s) > 8:
            pending = s
        elif not s:
            pending = None
    return items


def main():
    md = open(sys.argv[1], encoding="utf-8").read()
    items = parse_adot(md)
    conn = sqlite3.connect(DB)

    pool = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    have = {r[0] for r in conn.execute("SELECT DISTINCT outlet FROM articles")}

    # 경로 1(로컬 인덱스) -> 실패 시 경로 2(네이버 API)
    out, res = [], []
    ok_idx = ok_nav = unresolved = 0
    nav_state = None
    for it in items:
        r = {"status": "no_feed"}
        if it["outlet"] in have:
            r = resolve(conn, it["title"], it["outlet"])
        if r["status"] == "resolved":
            ok_idx += 1
            res.append({**it, **r, "via": "index"})
            continue
        n = naver_api.resolve(it["title"], it["outlet"])
        nav_state = n["status"] if n["status"] in ("not_configured", "error") else nav_state
        if n["status"] == "resolved":
            ok_nav += 1
            res.append({**it, **n})
        else:
            unresolved += 1
            res.append({**it, "status": "unresolved",
                        "path1": r.get("status"), "path2": n.get("status")})

    tot = len(items)
    ok = ok_idx + ok_nav
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    out.append("=" * 60)
    out.append("%s  인덱스 %d건" % (ts, pool))
    if nav_state:
        out.append("  [경로2 네이버 API 미작동: %s]" % nav_state)
    out.append("  대상 %d건 | 해결 %d (%.1f%%) = 인덱스 %d + 네이버 %d | 미해결 %d (%.1f%%)"
               % (tot, ok, ok / tot * 100, ok_idx, ok_nav,
                  unresolved, unresolved / tot * 100))

    # 날짜별로 쪼개야 인덱스 깊이 효과가 보인다
    by = {}
    for r in res:
        d = r["date"]
        by.setdefault(d, [0, 0])
        by[d][1] += 1
        if r.get("status") == "resolved":
            by[d][0] += 1
    out.append("  기사일자별 해결률:")
    for d in sorted(by):
        o, t = by[d]
        out.append("    %s  %2d/%2d  (%.0f%%)" % (d, o, t, o / t * 100))

    with open("resolve_bench.txt", "a", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    json.dump(res, open("resolve_bench_detail.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n".join(out).encode("cp949", "replace").decode("cp949"))


if __name__ == "__main__":
    main()
