#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
timeline 의 '·'(후보로만 오르고 미발송) 누적을 blind 목록에 표시한다. [2026-09-22 신설]

[왜 만들었나 — 실측 2026-09-22]
  사용자가 뺀 '현대차 아틸라스 2만5000대'는 timeline 에 이렇게 쌓여 있었다.
      ## 현대차 · 휴머노이드 · 공장  (3건, 08-26 → 09-01)
         · 08-26 "현대차, 휴머노이드 가장 잘 만들 기업..."
         · 08-31 현대차 美공장 로봇 1400대...2028년 휴머노이드 투입
         · 09-01 로봇이 조립하고 AI가 관리…현대차 美공장, 2028년 휴머노이드 투입
         >> 오늘 후보 1건 -> [187] 아틸라스 '훈련장'...2만5000대 배치 준비
  **세 줄 모두 '·' = 후보로만 오르고 한 번도 발송되지 않은 이야기**다.
  08-31·09-01 에 이미 '美공장 휴머노이드 투입'이 나왔고 오늘은 숫자만 바뀌었다.
  사용자 원문: *"여러 번 기사화된 내용이고 기사를 보면 중요한 새로운 내용이 없다."*
  지침에도 "여러 날 이어져 있으면 새 사실이 없으면 올리지 않는다"고 적혀 있는데,
  그날 timeline 을 **생성만 하고 읽지 않아** rank 9 로 실었다.

[무엇을 하나]
  timeline-<날짜>.md 를 파싱해 오늘 후보가 붙은 줄의 과거 이력을 세고,
  **과거 2건 이상이 전부 미발송**이면 blind 목록 해당 줄 끝에
  `[반복후보 N건·미발송 MM-DD~MM-DD]` 를 붙인다.
  hist 는 '발송된 것'만, repeat 은 '사용자가 뺀 것'만 본다. 이 표시는
  **"여러 번 후보였는데 한 번도 안 실린 주제"** 라는 세 번째 기억을 채운다.

[한계 — 솔직히 적어 둔다]
  timeline 클러스터링이 과거와 못 이으면 표시도 안 된다. 같은 날 사용자가 뺀
  '소뱅-현대차 RAI연구소'는 timeline 이 '[새] 대기업·인수·연구소'로 분류해
  과거 이력을 하나도 못 붙였다. 그런 건은 이 표시로 못 잡는다. 사례집이 맡는다.

[쓰는 법]
  python3 cowork/timeline_marks.py --date <날짜>            # blind 에 표시
  python3 cowork/timeline_marks.py --date <날짜> --check    # '고' 중 표시된 건 보고
"""
import argparse, csv, datetime, os, re, sys

OUT = "out"

def parse_timeline(path):
    """idx -> (past_total, past_unsent, days, first, last, story, truncated)"""
    marks = {}
    story = None; hdr = None; total = 0; unsent = 0; dates = []; pend = []
    def flush():
        d = sorted(set(dates))
        for i in pend:
            marks[i] = (total, unsent, len(d), d[0] if d else "", d[-1] if d else "",
                        story, hdr is not None and hdr > total)
    for raw in open(path, encoding="utf-8"):
        line = raw.rstrip("\n")
        if line.startswith("## "):
            flush()
            story = line[3:].strip(); total = 0; unsent = 0; dates = []; pend = []
            m = re.search(r'\((?:후보\s*)?(\d+)건', story)
            hdr = int(m.group(1)) if m and "후보" not in m.group(0) else None
            continue
        m = re.match(r'^\s*(\u00b7\s*)?(\d{2}-\d{2})\s+\S', line)
        if m and "오늘 후보" not in line:
            total += 1
            if m.group(1): unsent += 1
            dates.append(m.group(2))
            continue
        m = re.match(r'^\s*\[([NL]?\d+)\]', line)
        if m:
            pend.append(m.group(1))
    flush()
    return marks


def hot_lines(marks, min_past, min_days):
    """표시 대상: 과거 min_past건 이상 · 서로 다른 날 min_days일 이상 · 전부 미발송 · 목록 안 잘림.

    같은 날 교차보도 여러 건(예: 09-14 KT 모두의AI 4건)은 '여러 번 기사화'가 아니므로
    날짜 수로 걸러낸다. 헤더 건수보다 표시줄이 적은 메가클러스터는 '전부 미발송'을
    확인할 수 없으므로 제외한다(2026-09-22 오탐 실측 후 보정).
    """
    return {i: v for i, v in marks.items()
            if v[0] >= min_past and v[1] == v[0] and v[2] >= min_days and not v[6]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--min-past", type=int, default=2)
    ap.add_argument("--min-days", type=int, default=2)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    tl = os.path.join(OUT, "timeline-%s.md" % a.date)
    bl = os.path.join(OUT, "candidates-blind-%s.txt" % a.date)
    if not os.path.exists(tl):
        print("[marks] timeline 없음: %s — run.sh timeline 을 먼저 돌리세요" % tl); return 0
    marks = parse_timeline(tl)
    hot = hot_lines(marks, a.min_past, a.min_days)

    if a.check:
        vp = os.path.join(OUT, "verdict-%s.csv" % a.date)
        if not os.path.exists(vp):
            print("[marks] 판정 파일 없음"); return 0
        rows = [r for r in csv.DictReader(open(vp, encoding="utf-8")) if r.get("idx")]
        hits = [r for r in rows if r["grade"].strip() == "고" and r["idx"].strip() in hot]
        if not hits:
            print("[marks] '고' 중 반복후보·미발송 표시 없음"); return 0
        print("[marks] **'고' 중 '여러 번 후보였으나 한 번도 발송 안 된 이야기' %d건**" % len(hits))
        for r in hits:
            i = r["idx"].strip(); t, u, d, f, l, st, _ = hot[i]
            print("[marks]   [%s] 과거 %d건(%d일) 전부 미발송 (%s~%s) — %s" % (i, t, d, f, l, st))
        print("[marks]   발송 0건이므로 독자에게는 첫 게재입니다. 신규성으로 깎지 말고 알맹이로만 판단하세요.")
        return 0

    if not os.path.exists(bl):
        print("[marks] blind 없음: %s" % bl); return 0
    out = []; n = 0
    for raw in open(bl, encoding="utf-8"):
        line = re.sub(r"\s*\[(?:반복후보|후보만)[^\]]*\]", "", raw.rstrip("\n"))
        m = re.match(r'^([NL]?\d+)\s*\|', line)
        if m and m.group(1) in hot:
            t, u, d, f, l, st, _ = hot[m.group(1)]
            line += "  [후보만 %d회·발송 0건 %s~%s]" % (t, f, l)
            n += 1
        out.append(line)
    open(bl, "w", encoding="utf-8").write("\n".join(out) + "\n")
    print("[marks] 후보만·발송 0건 표시 %d건 -> %s" % (n, bl))
    if n:
        print("[marks]   뜻: 과거 여러 날 **후보로만** 올랐고 **독자에게는 한 번도 안 나갔다**.")
        print("[marks]   **신규성 감점 근거가 아니다**(2026-09-30 사용자 지시) - 독자 기준으로는 첫 게재다.")
        print("[marks]   내릴 때는 기사 자체의 알맹이(E축)·단계(C축)로만. 사용자가 뺀 적이 있으면 repeat 가 따로 잡는다.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
