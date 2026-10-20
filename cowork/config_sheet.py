# -*- coding: utf-8 -*-
"""오늘 쓸 설정값 한 장 (2026-09-15 신설)

왜 만들었나
-----------
2026-09-15 하루에 **같은 원인으로 세 번** 틀렸다. 전부 '설정값이 여러 곳에
있고 그중 낡은 것을 읽은' 경우다.

  1. 카테고리 집합 — 메모리(09-04, 폐기)를 읽어 없앤 섹션을 되살렸다
  2. 카테고리 배치 — 기계 분류값을 안 보고 제목 낱말로 놓았다
  3. **시사점 길이** — 운영지침 09-09절(40자)을 읽고 17건 중 14건을
     50자 밑으로 줄였다. 실제 정본은 09-10 스킬 개정의 **50~80자**다

사용자 지적: *"정답이 3군데에나 있는데 오류나는건 진짜 문제 있는거 아니냐"*
맞다. **사본이 셋이면 안전장치가 아니라 오답 후보가 셋인 것**이고, 어느 것이
현재인지 파일만 봐서는 알 수 없다. 사본을 줄이는 것이 답이지 더 조심하는
것이 답이 아니다.

그래서 이 스크립트는 **값을 들고 있지 않는다.** 전부 실행 코드에서 읽어 온다.
여기에 숫자를 적어 넣으면 사본이 하나 더 느는 것이므로 **절대 하드코딩하지 말 것.**

쓰는 법
-------
    bash cowork/run.sh config

발송본을 쓰기 **전에** 이것부터 본다. 판정 지침(타임라인·사례집·사업현황)과
달리 이것은 '서식과 분량' 한 장이다.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def read(path):
    try:
        return open(os.path.join(ROOT, path), encoding="utf-8").read()
    except Exception:
        return ""


def grab(src, pat, default="(못 읽음)"):
    m = re.search(pat, src, re.S | re.M)
    return m.group(1).strip() if m else default


def main():
    brief = read("brief.py")
    send = read("cowork/send_telegram.py")

    print("=" * 68)
    print(" 오늘 쓸 설정값 — 전부 실행 코드에서 읽어온 값입니다")
    print(" 이 표와 다른 숫자를 기억하고 있다면 **그 기억이 낡은 것입니다.**")
    print("=" * 68)

    # ── 카테고리 : brief.py CATEGORY_ORDER 가 유일 정본
    order = re.findall(r'"([^"]+)"',
                       grab(brief, r"^CATEGORY_ORDER\s*=\s*\[(.*?)\]"))
    print("\n[카테고리]  정본 = brief.py CATEGORY_ORDER   검사 = run.sh cats")
    for i, c in enumerate(order, 1):
        tail = "   <- 그날 해당 기사가 없으면 섹션째 뺀다" if c in ("국책과제", "기타") else ""
        print("   %d. %s%s" % (i, c, tail))
    print("   * 이 목록 밖의 섹션을 만들면 run.sh cats 가 발송을 막습니다.")
    print("   * 독파모·소버린 -> AI Model / 모두의 AI -> AI Agent (2026-09-09 통합)")
    print("   * 카테고리는 배치 기준일 뿐 **정원이 아닙니다.**")

    # ── 건수 : send_telegram.py 의 가드가 유일 정본
    lo = grab(send, r"목표\s*(\d+)~\d+건", "?")
    hi = grab(send, r"목표\s*\d+~(\d+)건", "?")
    limit = grab(send, r"^LIMIT\s*=\s*(\d+)")
    print("\n[건수]      정본 = cowork/send_telegram.py 건수 가드")
    print("   목표 %s~%s건 (%s건 초과 금지)" % (lo, hi, hi))
    print("   * '고'가 더 많아도 rank 상위에서 끊습니다.")
    print("   * '고'가 적은 날은 적은 대로 끝냅니다. '중'으로 채우지 마세요.")

    # ── 길이 : brief.py 프롬프트 문구가 유일 정본
    main_len = grab(brief, r"주요 내용:\s*(\d+자 안팎)", "?")
    main_max = grab(brief, r"주요 내용:\s*\d+자 안팎\*\*\s*\(최대 (\d+)", "?")
    imp_len = grab(brief, r"시사점:\s*(\d+~\d+자)", "?")
    imp_max = grab(brief, r"시사점:\s*\d+~\d+자\*\*\s*\(최대 (\d+)", "?")
    print("\n[본문 길이] 정본 = brief.py (2026-09-10 사용자 스킬 개정)")
    print("   주요 내용 : %s (최대 %s자)" % (main_len, main_max))
    print("   시사점    : %s (최대 %s자)" % (imp_len, imp_max))
    print("   * **'40자 안팎'은 2026-09-09 값이고 09-10 에 교체됐습니다.**")
    print("     09-15 에 그 낡은 값을 읽고 17건 중 14건을 50자 밑으로 줄였습니다.")
    print("   * 길이를 채우려 군더더기를 붙이라는 뜻은 아닙니다 -> run.sh insight")

    # ── 텔레그램 길이 : send_telegram.py LIMIT
    # ── 문체 : brief.py '문체는 명사형 종결' 항목이 정본. [2026-09-27 추가]
    #   09-23~25 사흘치가 서술형('~했다.')으로 나갔다. 이 표에 문체가 없어서
    #   길이는 맞추고 문체는 놓쳤다. 검사 = run.sh style
    style_line = grab(brief, r"(\*\*문체는 명사형 종결\.\*\*[^\n]*\n[^\n]*)", "")
    style_line = re.sub(r'^\s*L\.append\("|"\)\s*$', "", style_line, flags=re.M)
    style_line = re.sub(r'"\)\s*\n\s*L\.append\("', " ", style_line).strip()
    print("\n[문체]      정본 = brief.py 발송본 작성 절   검사 = run.sh style")
    print("   " + (style_line or "(brief.py 에서 못 읽음)"))
    print("   * 주요 내용·시사점 **둘 다** 명사형으로 끝냅니다. 마침표를 찍지 않습니다.")
    print("   * '~했다.' '~한다.' '~입니다.' 가 하나라도 있으면 run.sh style 이 종료코드 1.")
    print("   * 09-23~25 에 사흘 연속 서술형으로 나갔습니다. 09-22 발송본이 견본입니다.")

    print("\n[메시지]    정본 = cowork/send_telegram.py LIMIT")
    print("   상한 %s자 — **HTML 길이가 아니라 entities 파싱 후 텍스트 길이**" % limit)
    print("   * 반드시 메시지 1개. 2개면 기사를 빼지 말고 건당 본문을 먼저 압축합니다(09-28 사용자 결정). 그래도 넘칠 때만 rank 최하위를 뺍니다.")
    print("   * 발송 대상은 초안 채널. `run.sh send final` 은 쓰지 않습니다.")

    print("\n[본문 예산]  정본 = cowork/budget.py   명령 = run.sh budget")
    print("   발송본을 쓰기 **전에** `run.sh budget` 으로 건당 '주요 N자·시사점 M자'를 받는다.")
    print("   위 [본문 길이]는 상한이지 목표가 아니다 — 18~20건이면 건당 본문이 120~140자뿐이다.")
    print("   (10-20 실측: 예산 없이 20건을 쓰고 재니 5,008/4,080 -> 압축 5회)")
    print("\n[발송 전 순서]")
    print("   linkcheck -> cats -> insight -> style -> deals -> push -> send")
    print("   * cats 는 send 가 자동 호출하며 실패 시 발송을 막습니다.")

    print("\n" + "-" * 68)
    print(" 기억하지 말고 이 명령을 다시 부르세요:  bash cowork/run.sh config")
    print(" 메모리에 적힌 숫자는 '그때 왜 그렇게 정했나'의 기록이지")
    print(" 현재 설정값이 아닙니다. 값이 다르면 **코드가 이깁니다.**")
    print("-" * 68)

    probe = "".join([str(order or "(못 읽음)"), lo, hi, limit,
                     main_len, main_max, imp_len, imp_max])
    if "(못 읽음)" in probe or "?" in probe:
        print("\n[경고] 코드에서 못 읽은 항목이 있습니다. 정규식이 코드 변경을")
        print("       못 따라간 것이니 config_sheet.py 를 고치세요.")
        sys.exit(1)


if __name__ == "__main__":
    main()
