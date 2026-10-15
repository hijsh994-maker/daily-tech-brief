#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
비교쌍 사례집 — 편집자의 수정을 규칙이 아니라 사례로 쌓는다.  [2026-09-14 신설]

[왜 만들었나]
  2026-09-14 사용자: "이렇게 규칙만 추가한다고 제대로 선별이 될지 여전히 믿음이
  안 간다. 사람은 이렇게 규칙으로 기사를 결정하는 게 아니거든."

  맞는 지적이다. 09-10 에도 같은 말을 들었다("규칙을 더 얹지 마라. 네 번 실패했다").
  그런데도 09-14 에 또 규칙 8개를 적었다. 규칙표가 길어질수록 판정이
  **'표 훑기' 모드**로 되돌아가고, 그게 오판의 원인이었다.

  대신 **편집자가 실제로 고친 것을 짝으로 남긴다.** '이건 뺐고 이건 넣었다,
  이유는 이것' 이 20~30쌍 쌓이면 규칙표보다 훨씬 정확한 판단 재료가 된다.
  사람이 신입에게 가르치는 방식이 원래 이쪽이다.

[형식]
  파이프 5칸. 헤더 없이 한 줄에 한 건.
    날짜 | 제외|추가 | 헤드라인 | 사용자가 말한 사유(원문에 가깝게) | 일반화 한 줄

[쓰는 법]
  bash cowork/run.sh case <<'RAW'
  2026-09-14 | 제외 | LG·엔비디아 합작 'AI 팩토리'… | 협력은 이전부터 있던 내용이고 새 내용은 GPU 확보뿐인데 GPU 확보는 중요하지 않다 | 단순 GPU·컴퓨팅 구매는 금액이 커도 중. F1 이 못 뒤집는다
  RAW

  `--list` 로 현재 사례집을 찍는다. 판정 직전에 이것을 읽는다.

[주의]
  **여기에 규칙을 쓰지 말 것.** 다섯째 칸의 '일반화'는 그 사례를 한 줄로
  요약한 것이지 새 규칙이 아니다. 사례 없이 일반화만 있는 줄은 넣지 않는다.
"""
import argparse
import datetime
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# [2026-09-16 수정] 사례집은 **마운트(사용자 폴더)** 것이 정본이다.
#   run.sh 는 `cd $W` 로 작업본에서 돌기 때문에 BASE 가 /tmp/dtb-* 가 되는데,
#   `run.sh sync` 는 docs/ 를 복사하지 않는다. 그래서 작업본의 사례집이 늘
#   **빈 파일**이었고, `case` 로 넣은 사례가 그날 세션 안에서만 살아 있었다.
#   실측 09-16: 마운트에 18건이 있는데 `case --list` 는 8건만 찍었다.
#   판정 직전에 읽으라고 만든 파일이 정작 판정할 때 비어 있었던 것이다.
#   run.sh 가 DTB_DOCS 로 마운트 경로를 넘긴다.
DOC = os.path.join(os.environ.get("DTB_DOCS") or os.path.join(BASE, "docs"),
                   "사례집.md")

HEADER = """# 편집자 수정 사례집 (비교쌍)

> **판정 직전에 규칙표 대신 이것을 읽는다.** (2026-09-14 신설)
>
> 사용자가 발송본을 고칠 때마다 '뺀 것 / 넣은 것 + 그가 말한 이유'를 그대로
> 쌓는다. 규칙표를 늘리면 판정이 '표 훑기'로 되돌아가기 때문에, 규칙 대신
> **사례**로 감각을 옮긴다. `bash cowork/run.sh case` 로 덧붙인다.
>
> 읽는 법 — 오늘 후보 하나를 놓고 "이 사례들 중 어느 쪽에 가까운가"를 묻는다.
> 조건이 맞는 규칙을 찾는 것이 아니다.

| 날짜 | 판정 | 헤드라인 | 편집자가 말한 이유 | 일반화 |
|---|---|---|---|---|
"""


def log(m):
    print("[case] %s" % m)


def ensure():
    os.makedirs(os.path.dirname(DOC), exist_ok=True)
    if not os.path.exists(DOC):
        open(DOC, "w", encoding="utf-8").write(HEADER)


def rows():
    if not os.path.exists(DOC):
        return []
    out = []
    for line in open(DOC, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line.startswith("| 2"):
            continue
        out.append([c.strip() for c in line.strip("|").split("|")])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="사례집이 비었거나 줄어들었으면 종료코드 1 (2026-09-16 신설)")
    a = ap.parse_args()
    ensure()

    if a.check:
        # [2026-09-16 신설] 사례집은 '판정 직전에 읽는 파일'이다. 그런데 09-16 에
        #   작업본 사례집이 **빈 파일**인 줄 모르고 하루치 판정을 끝냈다
        #   (run.sh sync 가 docs/ 를 복사하지 않아서). 읽으라고 만든 파일이
        #   비어 있으면 판정 자체가 무효이므로, 건수를 최고수위로 기록해 두고
        #   그보다 줄면 막는다. 파일은 늘기만 하므로 줄었다 = 잘못 읽고 있다.
        n = len(rows())
        hwm_path = os.path.join(os.path.dirname(DOC), ".사례집-최고수위")
        hwm = 0
        if os.path.exists(hwm_path):
            try:
                hwm = int(open(hwm_path, encoding="utf-8").read().strip() or 0)
            except Exception:
                hwm = 0
        if n == 0:
            log("[실패] 사례집이 비었습니다: %s" % DOC)
            log("        판정 직전에 읽어야 할 파일입니다. 경로·동기화를 확인하세요.")
            return 1
        if n < hwm:
            log("[실패] 사례집이 줄었습니다: %d건 < 기록된 최고 %d건 (%s)" % (n, hwm, DOC))
            log("        잘못된 사본을 보고 있을 수 있습니다. 마운트 원본을 확인하세요.")
            return 1
        if n > hwm:
            with open(hwm_path, "w", encoding="utf-8") as f:
                f.write(str(n))
        log("사례집 %d건 (최고수위 %d) — %s" % (n, max(n, hwm), DOC))
        return 0

    if a.list:
        sys.stdout.write(open(DOC, encoding="utf-8").read())
        return 0

    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    added = 0
    with open(DOC, "a", encoding="utf-8") as f:
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [c.strip() for c in line.split("|")]
            if len(parts) < 4:
                log("[건너뜀] 칸이 모자랍니다(4칸 이상 필요): %s" % line[:60])
                continue
            while len(parts) < 5:
                parts.append("")
            f.write("| %s |\n" % " | ".join(parts[:5]))
            added += 1
    n = len(rows())
    log("%d건 추가 / 사례집 총 %d건 -> %s" % (added, n, DOC))
    if n < 20:
        log("20건까지 %d건 남았습니다. 그때부터 규칙표보다 이쪽이 정확해집니다." % (20 - n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
