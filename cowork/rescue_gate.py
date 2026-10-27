# -*- coding: utf-8 -*-
"""링크·본문 미확보 강등 게이트 (2026-10-27 신설)

왜 만들었나
-----------
10-27 '중' 6건을 사용자가 전부 '고'로 올렸다. 그중 4건(MS 6만명 희망퇴직·도요타
휴머노이드·브로드컴 630억달러·LG 로봇)은 기사 가치가 아니라 **"검색으로 못 찾아서·
본문을 못 받아서"** 내렸다. 사용자: *"계속 이런식으로 중요한 기사를 놓치면 안되!!!"*
원인을 재 보니 네 건 모두 네이버 뉴스 API 키워드 검색 1회(`run.sh find`)로 국내
기사·본문이 나왔다. 판정 중 쓴 WebSearch 는 미국 기준이라 국내 기사가 안 나왔다.

무엇을 하나
-----------
verdict.csv 의 '중'·'저' 사유에 링크·본문 미확보·검색 실패·확인 불가·보류 류
낱말이 있으면, 같은 사유에 `find:<run.sh find 질의와 결과>` 가 있는지 본다.
없으면 걸림(종료코드 1). `find:` 를 적으려면 실제로 `run.sh find` 를 돌려야 한다.
  - 찾았으면 → 그 링크·본문으로 다시 판정한다(대개 '고' 복귀, 사유에 대체 링크).
  - 결과 0건이면 → `find:"<질의>" 0건` 을 적고 남긴다.
같은 날 후보끼리 중복('대표 N')으로 내린 행은 대상이 아니다(대표 건이 실린다).

    bash cowork/run.sh rescue          # verdict 가 자동 호출
"""
import csv, os, re, sys

MISS = re.compile(r"링크\s*미확보|원문\s*링크\s*미확보|본문\s*미확보|본문이 네비|본문 캐시 없음|"
                  r"웹검색.{0,6}실패|확인\s*(불가|실패)|2차 보류|보류:")
SAMEDAY = re.compile(r"중복 대표\s*[NL]?\d")
OK = re.compile(r"find:")


def main():
    day = sys.argv[1]
    root = os.getcwd()
    p = os.path.join(root, "out", "verdict-%s.csv" % day)
    if not os.path.exists(p):
        print("[rescue] verdict 없음: %s" % p)
        return 0
    bad = []
    for r in csv.DictReader(open(p, encoding="utf-8")):
        if r.get("grade") not in ("중", "저"):
            continue
        rs = r.get("reason") or ""
        if MISS.search(rs) and not SAMEDAY.search(rs) and not OK.search(rs):
            bad.append((r["idx"], r["grade"], rs[:110]))
    print("[rescue] 링크·본문 미확보로 내린 중·저 점검 — 걸림 %d건" % len(bad))
    for i, g, rs in bad:
        print("   [%s] %s  %s" % (i, g, rs))
    if bad:
        print("[rescue] 기사 가치가 아니라 '못 찾아서' 내린 건입니다. **WebSearch 말고**")
        print("         bash cowork/run.sh find \"<주체+숫자/핵심어>\" --body 2  로 국내 기사를 찾아")
        print("         다시 판정하고, 그래도 없으면 사유에 find:\"<질의>\" 0건 을 적으세요.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
