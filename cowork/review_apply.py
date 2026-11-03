#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""검토 반영 한 번에 — 제외 기록 + 사례집 + 누락 점검.  [2026-09-21 신설]

[왜 만들었나]
  사용자가 발송본을 고치면 해야 할 일이 둘이었다.
    (1) config/rejected.jsonl 에 제외 이력을 남긴다  (반복 게이트의 입력)
    (2) docs/사례집.md 에 비교쌍을 남긴다            (판정 직전에 읽는 정본)
  09-18 에 **둘 다 안 했고**, 09-21 에 같은 기사 3건이 다시 올라왔다.
  단계가 둘이면 하나를 빠뜨린다. 그래서 명령 하나로 묶는다.

사용법
  bash cowork/run.sh review <<'RAW'
  제외 | 헤드라인 | url | 사용자 사유 | 일반화
  추가 | 헤드라인 | url | 사용자 사유 | 일반화
  배치 | 헤드라인 | url | 사용자 사유 | 일반화   ← [2026-11-03] 카테고리 배치 정정(사례집에만)
  RAW

  · '제외' 는 rejected.jsonl 과 사례집 **양쪽**에 들어간다.
  · '추가' 는 사례집에만 들어간다(추가는 막을 이유가 없다).
  · 사용자 사유를 지어내지 말 것. 못 받았으면 '(사유 미제시)' 라고 적는다.
"""
import argparse, json, os, subprocess, sys

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--mnt", required=True)
    a = ap.parse_args()
    rows = []
    for line in sys.stdin:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        p = [x.strip() for x in line.split("|")]
        if len(p) < 2:
            continue
        kind = p[0]
        if kind not in ("제외", "추가", "배치"):
            print("[review] 첫 칸은 '제외'·'추가'·'배치' 중 하나여야 합니다: %s" % line[:40]); return 1
        rows.append({"kind": kind, "head": p[1],
                     "url": p[2] if len(p) > 2 else "",
                     "reason": p[3] if len(p) > 3 else "(사유 미제시)",
                     "gen": p[4] if len(p) > 4 else ""})
    if not rows:
        print("[review] 입력 없음"); return 2

    # (1) 제외 이력
    outs = [r for r in rows if r["kind"] == "제외"]
    if outs:
        payload = "\n".join("%s | %s | %s" % (r["head"], r["url"], r["reason"]) for r in outs)
        subprocess.run([sys.executable, os.path.join(a.mnt, "cowork", "repeat_gate.py"),
                        "--log", "--date", a.date, "--mnt", a.mnt],
                       input=payload, text=True, check=True)

    # (2) 사례집
    payload = "\n".join("%s | %s | %s | %s | %s" % (a.date, r["kind"], r["head"], r["reason"], r["gen"])
                        for r in rows)
    env = dict(os.environ, DTB_DOCS=os.path.join(a.mnt, "docs"))
    subprocess.run([sys.executable, os.path.join(a.mnt, "cowork", "casebook.py"), "--date", a.date],
                   input=payload, text=True, env=env, check=True)

    nb = sum(1 for r in rows if r["kind"] == "배치")
    print("[review] 제외 %d건 / 추가 %d건 / 배치 %d건 반영 완료" % (len(outs), len(rows) - len(outs) - nb, nb))
    return 0

if __name__ == "__main__":
    sys.exit(main())
