#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[2026-09-09 신설] 시사점 품질 검사.

## 왜 만들었나
임원 피드백: "시사점에 가끔 의미 없는 게 있는 것 같다. 좀 더 인사이트가
담겨 있으면 좋겠다."

## [2026-09-09 재정정] 어미로 판정하지 않는다
초판은 '~가속' '~본격화' '~확산' '~부상' 으로 끝나면 걸리게 했는데
**틀린 접근이었다.** 사용자 정정: "시사점 지침은 원래대로 명사형으로 종결해줘.
시사점을 단순히 글자수를 늘리는 게 중요한 게 아니고 경영층에게 의미 있는
인사이트를 주는 데 노력하라는 의미였어."

명사형 종결은 이 브리프의 확립된 문체다(`~가속화` `~부상` 모두 정상).
**어미가 문제가 아니라 판단이 없는 것이 문제다.** 그래서 검사도
어미가 아니라 **'주요 내용에 없는 새 정보를 담았는가'** 로 바꿨다.

## 무엇을 잡나
  - **동어반복**  시사점 어절의 절반 이상이 주요 내용에 이미 있음
      주요내용 '엔비디아가 리벨리온에 인수 포함 기술제휴·투자를 제안'
      시사점   '국내 AI 반도체 자립 전략의 분기점'   <- 기사를 다시 말한 것뿐
  - **새 정보 없음**  헤드라인·주요 내용에 없는 실질 어절이 2개 미만.
      읽어도 새로 아는 것이 없다는 신호다.
  - **극단적으로 짧음**  15자 미만. 40자를 채우라는 뜻이 **아니다** —
      짧아도 판단이 있으면 통과다.

기계가 '좋은 시사점'을 판정할 수는 없다. 대신 **실패 신호를 찾아
사람에게 보여주는 것**까지만 한다. 판단은 여전히 사람이 한다.

## 사용법
    bash cowork/run.sh insight            # 오늘 발송본 검사
종료코드는 항상 0(경고 전용). 발송을 막지 않는다.
"""
import argparse
import datetime
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(BASE, "out")

# [2026-09-09] HOLLOW(어미 목록) 검사는 폐기했다. 명사형 종결은 정상 문체다.
# 조사·군더더기와 **어미 자체**를 걷어내고 남는 실질 어절만 비교한다.
#   '가속화' '부상' 같은 종결어는 여기서 제외해야 '새 정보'를 제대로 센다.
STOP = set("이 그 저 및 등 의 를 을 은 는 가 이다 함 됨 로 으로 에 에서 와 과 "
           "가속 가속화 본격화 확산 부상 확대 심화 강화 재조명 전환 이동 "
           "필요 전망 예상 관측 가능성".split())


def toks(s):
    s = re.sub(r"[^\w가-힣 ]", " ", s)
    return {w for w in s.split() if len(w) > 1 and w not in STOP}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    a = ap.parse_args()
    p = os.path.join(OUTDIR, "brief-%s.md" % a.date)
    if not os.path.exists(p):
        print("[insight] %s 없음" % p)
        return 0
    txt = open(p, encoding="utf-8").read()

    items, cur = [], {}
    for line in txt.split("\n"):
        m = re.match(r"^\*\*(\d+)\\?\. (.+?)\*\* \(", line)
        if m:
            cur = {"title": m.group(2)}
            continue
        m = re.match(r"^[📌▪•] \*\*주요 내용:\*\* (.+)$", line)
        if m and cur:
            cur["body"] = m.group(1).strip()
            continue
        m = re.match(r"^[📌▪•] \*\*시사점:\*\* (.+)$", line)
        if m and cur:
            cur["imp"] = re.sub(r"\s*\(관련 조직:.*?\)\s*$", "", m.group(1)).strip()
            items.append(cur)
            cur = {}

    if not items:
        print("[insight] 항목을 못 읽었습니다. 서식을 확인하세요.")
        return 0

    flags = []
    for it in items:
        body, imp = it.get("body", ""), it.get("imp", "")
        why = []
        ti = toks(imp)
        tb = toks(body) | toks(it.get("title", ""))
        # 1) 동어반복 - 시사점 실질 어절의 절반 이상이 주요 내용·헤드라인에 이미 있음
        if ti and len(ti & tb) / len(ti) >= 0.5:
            why.append("동어반복(주요내용·헤드라인과 %d%% 겹침)"
                       % (100 * len(ti & tb) // len(ti)))
        # 2) 새 정보 없음 - 기사에 없는 실질 어절이 2개 미만
        elif len(ti - tb) < 2:
            why.append("새 정보 없음(기사 밖 어절 %d개)" % len(ti - tb))
        # 3) [2026-09-09 3차 개정] **한 겹짜리 관찰**을 잡는다.
        #   종전 임계값은 15자였는데, 09-09 발송본 15건이 평균 27자로 전부
        #   'A가 B로 이동' 형태의 관찰만 있었는데도 한 건도 안 걸렸다.
        #   사용자 지적: '왜 여전히 시사점이 이렇게 짧아?'
        #   시사점은 [관찰]+[함의] 두 겹으로 쓰고, 두 겹이면 자연히 60자를 넘는다.
        #   **길이 자체가 목적이 아니라 겹수의 대리 지표다.** 50자 미만이면
        #   함의가 빠졌을 가능성이 높으니 눈으로 보라는 뜻이다.
        #   [2026-09-10 스킬 개정] 임계값 50 -> 40 (시사점 하한이 60 -> 50 으로
        #   내려간 데 맞춤). 스킬 8절 검증 항목의 '40자 미만' 과 같은 값이다.
        if len(imp) < 40:
            why.append("한 겹짜리 관찰 의심(%d자) - 함의를 덧붙일 것" % len(imp))
        # 4) 반대로 셋째 절까지 붙은 경우. 두 겹에서 끊는다.
        elif len(imp) > 90:
            why.append("너무 김(%d자) - 셋째 절을 덜어낼 것" % len(imp))
        if why:
            flags.append((it["title"], imp, why))

    print("[insight] 기사 %d건 / 시사점 점검 대상 %d건" % (len(items), len(flags)))
    for t, imp, why in flags:
        print("  [확인] %s" % t[:52])
        print("         시사점: %s" % imp)
        print("         사유: %s" % " · ".join(why))
    if flags:
        print("[insight] 위 항목은 아래 넷 중 하나를 답하도록 고쳐 쓰세요:")
        print("          a.무엇이 바뀌나  b.무엇을 시사하나")
        print("          c.우리에게 무엇인가  d.무엇을 지켜봐야 하나")
        print("[insight] **글자 수를 늘리라는 뜻이 아닙니다.** 명사형 종결"
              "('~가속화' '~부상')은 그대로 쓰되,")
        print("          헤드라인을 읽은 경영층이 **새로 얻는 것**을 한 겹 더 넣으세요.")
    else:
        print("[insight] 걸린 항목 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
