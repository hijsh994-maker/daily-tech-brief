# -*- coding: utf-8 -*-
"""발송본 카테고리 검사 (2026-09-15 신설)

왜 만들었나
-----------
2026-09-15 에 발송본 카테고리가 두 가지로 틀렸다.

  (1) **집합·순서** — 2026-09-09 에 폐기된 '독파모 & 모두의 AI' 섹션을
      되살리고 순서도 임의로 짰다. 정답은 brief.py 의 CATEGORY_ORDER 이고
      프롬프트 1259행에도 그대로 찍혀 있었는데, 1997행짜리 프롬프트의
      뒷부분 서식 규칙을 안 읽고 **메모리(09-04, 이미 폐기)** 를 정본으로 썼다.
  (2) **개별 배치** — 애플 iOS 27(시리 앱 실행·결제 대행) 기사를 제목의
      '인텔리전스' 한 낱말만 보고 AI Model 에 넣었다. 본문은 에이전트 기사다.

둘 다 send 까지 아무 검사에 안 걸렸다. **기계가 정본 목록을 들고 있는데
발송본에 그것을 강제하는 단계가 없던 것**이 구조적 원인이다. 이 스크립트가
그 구멍을 막는다.

무엇을 하나
-----------
[강제 / 종료코드 1]  brief.py CATEGORY_ORDER 대조
  - 목록 밖 섹션명       -> 실패
  - 순서 어긋남          -> 실패
  - 같은 섹션 중복 출현  -> 실패
  - 섹션 안 번호가 1부터 연속이 아님 -> 실패

[경고 / 종료코드에 영향 없음]  기계 분류 대조
  후보 JSONL 의 category 와 내 배치를 비교해 불일치를 찍는다.
  **기계 분류가 정답이라는 뜻이 아니다.** 실측 09-15: 기계는 트럼프·젠슨 황
  통화를 헤드라인의 '로봇' 때문에 Physical AI 로, 네카오 AI 광고를
  AI 보안&정책 으로 넣었다. 둘 다 틀렸다. 이 경고의 쓰임은 하나다 —
  **"기계와 다르게 놓았다면 본문을 근거로 그렇게 한 것이 맞는가"를
  한 번 더 묻게 하는 것.** 특히 기계가 '기타'로 준 건은 손배치가 필수이므로
  따로 표시한다(09-15 애플 건이 정확히 이 경우였다).

쓰는 법
-------
    bash cowork/run.sh cats            # 검사만
    bash cowork/run.sh cats --strict   # 경고도 종료코드 1 로

`run.sh send` 가 발송 직전에 자동으로 부른다. 종료코드 1 이면 발송을 멈춘다.
"""
import argparse
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


def load_order():
    """정본은 brief.py 의 CATEGORY_ORDER 하나다. 여기에 복붙하지 말 것."""
    src = open(os.path.join(ROOT, "brief.py"), encoding="utf-8").read()
    m = re.search(r"^CATEGORY_ORDER\s*=\s*\[(.*?)\]", src, re.S | re.M)
    if not m:
        print("[cats] [실패] brief.py 에서 CATEGORY_ORDER 를 못 찾았습니다")
        sys.exit(2)
    return re.findall(r'"([^"]+)"', m.group(1))


def parse_brief(path):
    """[(섹션명, [(번호, 헤드라인), ...]), ...] 를 출현 순서대로."""
    secs, cur = [], None
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        ms = re.match(r"^\*\*■\s*(.+?)\*\*\s*$", line)
        if ms:
            cur = (ms.group(1).strip(), [])
            secs.append(cur)
            continue
        mi = re.match(r"^\*\*(\d+)\\?\.\s*(.+?)\*\*\s*\(", line)
        if mi and cur is not None:
            cur[1].append((int(mi.group(1)), mi.group(2).strip()))
    return secs


def load_machine_cats(day):
    """후보 JSONL 의 idx -> (category, headline)."""
    path = os.path.join(ROOT, "out", "prompt-%s.txt" % day)
    out = {}
    if not os.path.exists(path):
        return out
    for m in re.finditer(r'^\{"idx".*$', open(path, encoding="utf-8").read(), re.M):
        try:
            d = json.loads(m.group(0))
        except Exception:
            continue
        out[str(d.get("idx"))] = (d.get("category") or "?", d.get("headline") or "")
    return out


def norm(s):
    return re.sub(r"[\s&·]+", "", s or "").lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--stem", default="brief")
    ap.add_argument("--strict", action="store_true",
                    help="배치 불일치 경고도 종료코드 1 로 올린다")
    a = ap.parse_args()

    path = os.path.join(ROOT, "out", "%s-%s.md" % (a.stem, a.date))
    if not os.path.exists(path):
        print("[cats] [실패] %s 없음" % path)
        sys.exit(2)

    order = load_order()
    secs = parse_brief(path)
    if not secs:
        print("[cats] [실패] '**■ 섹션명**' 을 한 줄도 못 찾았습니다")
        sys.exit(2)

    errors, warns = [], []
    names = [s[0] for s in secs]

    # (1) 목록 밖 섹션 - 표기 흔들림(공백·&)은 정규화해서 비교하고 알려준다
    canon = {norm(c): c for c in order}
    for n in names:
        if n in order:
            continue
        if norm(n) in canon:
            warns.append("섹션명 표기 다름: '%s' -> 정본 '%s'" % (n, canon[norm(n)]))
        else:
            errors.append("정본에 없는 카테고리: '%s'  (정본: %s)"
                          % (n, " > ".join(order)))

    # (2) 중복
    for n in sorted(set(names)):
        if names.count(n) > 1:
            errors.append("같은 카테고리가 %d번 나옵니다: '%s'" % (names.count(n), n))

    # (3) 순서
    known = [n for n in names if n in order or norm(n) in canon]
    idxs = [order.index(canon.get(norm(n), n)) for n in known]
    if idxs != sorted(idxs):
        errors.append("순서가 정본과 다릅니다\n         현재: %s\n         정본: %s"
                      % (" > ".join(known),
                         " > ".join(c for c in order
                                    if c in [canon.get(norm(n), n) for n in known])))

    # (4) 섹션 안 번호가 1부터 연속인가
    for n, items in secs:
        nums = [i for i, _ in items]
        if nums != list(range(1, len(nums) + 1)):
            errors.append("'%s' 섹션 번호가 1부터 연속이 아닙니다: %s" % (n, nums))

    # (5) 기계 분류 대조 - 경고 전용
    machine = load_machine_cats(a.date)
    bykey = {}
    for idx, (cat, head) in machine.items():
        bykey[norm(head)[:24]] = (idx, cat)
    mism, etc = [], []
    for n, items in secs:
        for _, head in items:
            hit = bykey.get(norm(head)[:24])
            if not hit:
                continue
            idx, mcat = hit
            if mcat == "기타":
                etc.append((idx, n, head))
            elif mcat and mcat != n:
                mism.append((idx, mcat, n, head))

    total = sum(len(i) for _, i in secs)
    print("[cats] %s / 섹션 %d개 / 기사 %d건" % (os.path.basename(path), len(secs), total))
    print("[cats] 순서: %s" % " > ".join(names))

    for w in warns:
        print("  [경고] %s" % w)
    for e in errors:
        print("  [실패] %s" % e)

    if etc:
        print("  [확인] 기계가 '기타'로 준 건 %d개 - 손배치가 맞는지 **본문**으로 확인:" % len(etc))
        for idx, n, head in etc:
            print("         [%s] -> %s | %s" % (idx, n, head[:46]))
    if mism:
        print("  [확인] 기계 분류와 다르게 놓은 건 %d개 (기계가 정답이라는 뜻은 아님):" % len(mism))
        for idx, mcat, n, head in mism:
            print("         [%s] 기계 %s -> 내 배치 %s | %s" % (idx, mcat, n, head[:40]))
    if etc or mism:
        print("  [확인] 물을 것: **제목이 아니라 본문 리드가 그 카테고리를 가리키는가.**")
        print("         2026-09-15 애플 iOS 27 건은 기계가 '기타'였고, 제목의")
        print("         '인텔리전스'만 보고 AI Model 에 넣었다가 정정받았다.")

    if errors:
        print("[cats] 실패 %d건 - **고치기 전에는 발송하지 마세요.**" % len(errors))
        sys.exit(1)
    if a.strict and (mism or etc or warns):
        print("[cats] --strict: 확인 항목이 남아 종료코드 1")
        sys.exit(1)
    print("[cats] 카테고리 집합·순서·번호 정상")
    sys.exit(0)


if __name__ == "__main__":
    main()
