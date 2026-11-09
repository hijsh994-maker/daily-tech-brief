#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
판정 뭉침 게이트.  [2026-09-22 신설]

[왜 만들었나]
  지침에 "'중' 의 사유도 한 종류로 뭉개지 말 것" 이 있고 verdict 에 검사도
  있었지만, 그 검사는 **'사유가 1종뿐인가'** 만 봤다. 그래서 2026-09-22 에
  에이전트가 사유를 8종 만들고 그중 하나에 91% 를 몰아넣자 **경고 없이 통과**했다.
  557건을 손으로 판정하는 대신 키워드 정규식 스크립트로 CSV 를 생성했는데,
  구조 검사(전건 존재·'/' 포함·새사실·rank 연속)는 전부 통과했다.
  종류 수가 아니라 **집중도**를 재야 한다.

[임계치 근거 — 과거 실측]
  중 최다사유 비중:  09-14 0% · 09-15 0% · 09-17 9% · 09-16 32%   <- 실제 판정한 날
                     09-18 59% · 09-21 66% · 09-22 91% · 09-02/03/07/10 100%  <- 뭉갠 날
  저 최다코드 비중:  09-14 4% · 09-09 22% · 09-21 36% · 09-15 38%  <- 정상
                     09-08 59% · 09-22 70% · 09-10 72%             <- 뭉갬
  -> 중 35% / 저 60% 에 선을 그으면 위 두 무리가 정확히 갈린다.

[무엇을 보나]
  1) '중' 안에서 같은 사유 문자열의 최대 비중    (>=35% 실패)
  2) '중' 고유 사유 종류 / '중' 건수              (<10% 실패)
  3) '저' 안에서 같은 닫힌 코드의 최대 비중      (>=60% 실패)
  '저' 는 닫힌 코드를 쓰게 돼 있어 어느 정도 반복이 정상이므로 선을 높게 뒀다.

[종료코드] 0 통과 / 1 뭉침 감지
"""
import argparse, csv, collections, datetime, os, sys

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--max-mid-share", type=float, default=35.0)
    ap.add_argument("--min-mid-variety", type=float, default=10.0)
    ap.add_argument("--max-low-share", type=float, default=60.0)
    a = ap.parse_args()

    path = os.path.join("out", "verdict-%s.csv" % a.date)
    if not os.path.exists(path):
        print("[spread] 판정 파일 없음: %s" % path); return 0
    rows = [r for r in list(csv.reader(open(path, encoding="utf-8")))[1:] if len(r) >= 3]
    # [2026-11-09] run.sh prejudge 가 채운 '자동:' 사유(스포츠·시황·실적)는 사람이 쓴 판정이 아니므로 집계에서 뺀다.
    auto_n = sum(1 for r in rows if r[2].strip().startswith("자동:"))
    rows = [r for r in rows if not r[2].strip().startswith("자동:")]
    if auto_n:
        print("[spread] 자동 '저'(prejudge) %d건은 집계 제외" % auto_n)
    if not rows:
        print("[spread] 판정 0건"); return 0

    fail = []
    def bucket(g):
        return [r[2].strip() for r in rows if r[1] == g]

    mid = bucket("중")
    if mid:
        c = collections.Counter(mid); top, n = c.most_common(1)[0]
        share = n * 100.0 / len(mid)
        variety = len(c) * 100.0 / len(mid)
        print("[spread] 중 %d건 / 고유 사유 %d종 / 최다사유 %d건(%.0f%%)"
              % (len(mid), len(c), n, share))
        if share >= a.max_mid_share:
            fail.append("'중' 최다사유가 %.0f%% (상한 %.0f%%) — \"%s\"" % (share, a.max_mid_share, top[:40]))
        if variety < a.min_mid_variety:
            fail.append("'중' 사유 종류가 %.0f%% (하한 %.0f%%) — %d건에 %d종뿐"
                        % (variety, a.min_mid_variety, len(mid), len(c)))

    low = bucket("저")
    if low:
        c = collections.Counter(low); top, n = c.most_common(1)[0]
        share = n * 100.0 / len(low)
        print("[spread] 저 %d건 / 고유 코드 %d종 / 최다코드 %d건(%.0f%%)"
              % (len(low), len(c), n, share))
        if share >= a.max_low_share:
            fail.append("'저' 최다코드가 %.0f%% (상한 %.0f%%) — \"%s\"" % (share, a.max_low_share, top[:40]))

    if fail:
        print("[spread] **판정이 뭉쳤습니다 — 전수 판정을 하지 않은 신호입니다.**")
        for f in fail:
            print("[spread]   - %s" % f)
        print("[spread] 후보를 50~80건씩 끊어 실제로 읽고 사유를 개별로 적으세요.")
        print("[spread] 키워드 정규식·딕셔너리로 CSV 를 생성하는 것은 전수 판정이 아닙니다.")
        print("[spread] 보고에 '전수 판정'이라고 쓰려면 이 게이트를 통과해야 합니다.")
        return 1
    print("[spread] 판정 분포 정상")
    return 0

if __name__ == "__main__":
    sys.exit(main())
