# -*- coding: utf-8 -*-
"""발송본 문체 게이트 — 주요 내용·시사점은 명사형으로 끝난다 (2026-09-27 신설)

왜 만들었나
-----------
09-23·24·25 사흘치 발송본이 전부 '~했다/~한다' 서술형으로 나갔다.
사용자 지적: *"문체가 명사형으로 끝나지 않고 ~습니다 이렇게 끝나는데. 이건 내
지시와 다르고 이전에는 안그랬는데 왜 갑자기 이렇게 보내는지"*

원인은 넷이 겹쳤다.
  1. 문체 규칙은 brief.py 가 만드는 prompt-<날짜>.txt **65행** 에만 있었다.
     실행지침은 그 파일에서 [편집자 평가 기준](189행~)과 I 절만 열라고 했다.
     발송본 작성 절(65행 근처)은 아무도 읽으라고 하지 않았다.
  2. 발송본을 쓰기 전에 보라는 `run.sh config` 에 **문체 항목이 없었다.**
     카테고리·건수·길이·메시지 상한만 있었다. 그래서 길이는 맞추고 문체는 놓쳤다.
  3. `insight_check.py` 는 09-09 에 어미 검사(HOLLOW)를 **일부러 걷어냈다** —
     그때는 명사형을 공허한 술어로 오판해 사용자가 "원래대로 명사형으로" 라고
     정정한 사건이었다. 그 뒤로 문체를 보는 게이트가 하나도 없었다.
  4. 09-22 에 사람 검토가 사라졌다(자동 발송 복귀). 09-22 까지는 사용자가
     매일 발송본을 봤으므로 문체가 흔들려도 그날 잡혔다. 09-23 첫 무검토
     실행에서 서술형으로 바뀌었고, 잡을 사람이 없어 사흘을 갔다.

무엇을 하나
-----------
brief-<날짜>.md 의 '• **주요 내용:**' '• **시사점:**' 줄을 전부 읽어
문장 단위로 끝을 본다. 서술형 어미('~다.' '~습니다.' '~요.' '~죠.')로 끝나는
문장이 하나라도 있으면 **종료코드 1** 과 함께 그 줄을 찍는다.

명사형의 예(통과): '~공개' '~확대' '~국면' '~전망' '~함' '~됨' '~부상'
서술형의 예(실패): '~공개했다.' '~확대된다.' '~국면입니다.' '~전망이다.'

한 줄에 문장이 둘이면 둘 다 본다. 규칙의 정본은 brief.py 의
'문체는 명사형 종결' 항목이다. 여기서는 그 규칙을 검사만 한다.

쓰는 법
-------
    bash cowork/run.sh style            # 종료코드 0 통과 / 1 서술형 있음
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

FIELD = re.compile(r"^• \*\*(주요 내용|시사점):\*\*\s*(.*)$")
# 문장 끝의 서술형 어미. 마침표·따옴표·괄호가 뒤에 붙어도 잡는다.
# '다' 로 끝나는 명사(바다 등)는 이 브리프에서 문장 끝에 올 일이 없다.
DECL = re.compile(r"(습니다|입니다|니다|[가-힣]다|요|죠)[.!?]?[\"'’”)\]]*\s*$")
# 문장 나누기: 마침표 뒤 공백. 숫자 소수점(3.5)·URL 은 자르지 않는다.
SPLIT = re.compile(r"(?<=[가-힣A-Za-z)\]\"'’”])\.\s+")


def sentences(text):
    parts = [p.strip() for p in SPLIT.split(text) if p.strip()]
    return parts or [text.strip()]


def layout_errors(lines):
    """[2026-10-02] 양식 검사 — 'ㅤ'(U+3164) 구분줄 규칙.
    10-02 발송본이 카테고리 헤더 앞 'ㅤ'를 빠뜨린 채 나갔다(사용자 지적).
    규칙: 기사와 기사 사이, 그리고 두 번째 이후 '■ 카테고리' 앞에 'ㅤ' 한 줄.
    빈 줄은 쓰지 않는다(텔레그램이 삼킨다)."""
    errs = []
    seen_cat = False
    for i, ln in enumerate(lines):
        prev = lines[i - 1] if i else ""
        if ln.strip() == "" and i < len(lines) - 1:
            errs.append((i + 1, "빈 줄 금지 — 'ㅤ' 한 줄을 쓸 것"))
        if ln.startswith("**■"):
            if seen_cat and prev != "ㅤ":
                errs.append((i + 1, "카테고리 헤더 앞 'ㅤ' 누락: " + ln[:30]))
            seen_cat = True
        m = re.match(r"\*\*(\d+)\\\.", ln)
        if m and m.group(1) != "1" and prev != "ㅤ":
            errs.append((i + 1, "기사 사이 'ㅤ' 누락: " + ln[:30]))
        if m and m.group(1) == "1" and not prev.startswith("**■"):
            errs.append((i + 1, "1번 기사는 카테고리 헤더 바로 다음 줄이어야 함: " + ln[:30]))
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--stem", default="brief")
    a = ap.parse_args()
    path = os.path.join(ROOT, "out", "%s-%s.md" % (a.stem, a.date))
    if not os.path.exists(path):
        print("[style] 발송본 없음: %s" % path)
        return 2
    bad = []
    total = 0
    for ln in open(path, encoding="utf-8"):
        m = FIELD.match(ln.rstrip("\n"))
        if not m:
            continue
        total += 1
        for s in sentences(m.group(2)):
            if DECL.search(s):
                bad.append((m.group(1), s))
    print("[style] %s / 주요 내용·시사점 %d줄 / 서술형 종결 %d문장"
          % (os.path.basename(path), total, len(bad)))
    lay = layout_errors(open(path, encoding="utf-8").read().rstrip("\n").split("\n"))
    if lay:
        print("[style] **양식 위반 %d건** (ㅤ 구분줄 규칙, 2026-10-02 신설)" % len(lay))
        for n, msg in lay:
            print("   %d행: %s" % (n, msg))
        if not bad:
            return 1
    else:
        print("[style] 양식 통과 — ㅤ 구분줄 정상")
    if not bad:
        print("[style] 통과 — 전부 명사형 종결")
        return 0
    print("[style] **문체 위반** — 주요 내용·시사점은 명사형으로 끝나야 합니다"
          " (정본: brief.py '문체는 명사형 종결').")
    for kind, s in bad:
        tail = s[-40:] if len(s) > 40 else s
        print("   [%s] …%s" % (kind, tail))
    print("[style] 고치는 법: '~공개했다.' -> '~공개' / '~바뀐다.' -> '~전환' /")
    print("        '~해야 한다.' -> '~필요' / 두 문장이면 둘 다 명사형으로.")
    print("        마침표는 붙이지 않는다(09-22 까지의 발송본 참조).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
