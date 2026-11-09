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

[강제 / 종료코드 1]  [2026-11-09 사용자 지시] 배치표 — 판정 순서를 건마다 적고 그대로 놓았는가
  out/placement-<날짜>.csv  (url,배치번호,근거,헤드라인)
  - 발송본의 모든 기사(원문 URL 기준)에 행이 있어야 한다. 없으면 실패.
  - 배치번호 = brief.py CATEGORY_RULES 의 '처음 예가 나온 번호'(0~10) 또는 '경계'.
    번호가 가리키는 카테고리와 실제로 놓은 섹션이 다르면 실패. 0(실적)은 싣지 않는다 → 실패.
  - 근거는 본문 사실로 10자 이상. 9번이면 '8번 아님:', 10번이면 '8번 아님:'·'9번 아님:' 이 근거에 있어야 한다
    (11-09 SK바이오팜 '신약개발 특화 AI 모델 구축'을 8번을 건너뛰고 10번 Enterprise AI 에 넣은 사고).
  - 쓰는 법: `bash cowork/run.sh cats --template` 로 빈 표를 만든 뒤, **발송본을 쓰기 전에**
    기사마다 0번부터 차례로 묻고 첫 '예'의 번호와 근거를 채운다. 그 표대로 섹션에 놓는다.

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


RULE_EARN = re.compile(r"(분기|[1-4]Q|FY\d|회계연도).{0,20}(매출|영업이익|순이익|실적)|실적 (발표|쇼크|부진|호조)|가이던스|earnings|quarterly results|매출.{0,12}% (성장|급증|증가)|\d+% 성장…|영업이익률", re.I)
RULE_FUND = re.compile(r"인수|매각|투자 ?유치|지분 ?투자|투자 참여|시리즈 ?[A-H]|기업가치|밸류|라운드|raises?|valuation|acquir|funding|series [a-h]|merger", re.I)
RULE_DCFIN = re.compile(r"(대출|보증|회사채|채권|조달|차입|loan|debt|bond|financ)", re.I)
RULE_DC = re.compile(r"데이터센터|AIDC|AI ?DC|data ?cent|GPU|MW|GW|AI 투자|AI 인프라", re.I)
RULE_EARN = re.compile(r"(분기|[1-4]Q|FY\d|회계연도).{0,20}(매출|영업이익|순이익|실적)|실적 (발표|쇼크|부진|호조)|가이던스|earnings|quarterly results|매출.{0,12}% (성장|급증|증가)|\d+% 성장…|영업이익률", re.I)
RULE_FUND = re.compile(r"인수|매각|투자 ?유치|지분 ?(투자|인수|매입)|투자 참여|시리즈 ?[A-H]\b|기업가치|밸류|몸값|IPO|상장|라운드|시드|베팅|품어|품는다|raises?\b|valuation|acquir|funding|series [a-h]\b|merger", re.I)
RULE_DCFIN = re.compile(r"대출|보증|회사채|채권|조달|차입|loan|debt|bond|financ", re.I)
RULE_DC = re.compile(r"데이터센터|AIDC|AI ?DC|data ?cent|GPU|MW|GW|AI 투자|AI 인프라", re.I)
RULE_NAT_MODEL = re.compile(r"독파모|독자 ?AI|독자 ?모델|소버린 ?(AI|모델)|프런티어 AI|프론티어 AI", re.I)
RULE_NAT_AGENT = re.compile(r"모두의 ?AI", re.I)
RULE_NAT_DC = re.compile(r"(정부|특별법|인허가|국가|지자체|시,|도,|유치).{0,30}(데이터센터|AIDC)|(데이터센터|AIDC).{0,30}(정부|특별법|인허가|유치)", re.I)
RULE_POLICY = re.compile(r"^(정부|중기부|과기정통부|과기부|산업부|방미통위|국회|의회|백악관|상무부|법무부|EU|미 정부|美 정부)|정부[,가는]|법안|입법|행정명령|규제 (강화|완화|도입)|예산|국가정책|과징금", re.I)
RULE_SAFETY = re.compile(r"해킹|해커|공격|위협|취약점|보안 사고|유출|악용|탈옥|딥페이크|안전|감시|오정렬|misalign|monitor|행동 원칙|모델 스펙|소송|제소", re.I)
RULE_INFRA = re.compile(r"데이터센터|AIDC|GPU|HBM|AI ?칩|반도체|전력|컴퓨팅|TPU|NPU|data ?cent", re.I)
RULE_ROBOT = re.compile(r"로봇|휴머노이드|자율주행|robot|humanoid", re.I)


def predict(head, body):
    """판정 순서(brief.py CATEGORY_RULES) 를 낱말로 흉내낸다. (권장 카테고리, 사유) 또는 None."""
    t = head + " " + body
    dcfin = RULE_DCFIN.search(t) and RULE_DC.search(t)
    if RULE_EARN.search(t) and not dcfin:
        return "싣지 않음", "0) 실적 기사"
    if RULE_FUND.search(head):
        return "AI 투자&시장", "2) 지분 이동(인수·매각·펀딩)"
    if RULE_NAT_MODEL.search(head):
        return "AI Model", "3) 국가 AI 사업(독파모·소버린)"
    if RULE_NAT_AGENT.search(head):
        return "AI Agent", "3) 국가 AI 사업(모두의 AI)"
    if RULE_NAT_DC.search(head):
        return "AI DC & Infra", "3) 국가 AI 사업(AIDC)"
    if RULE_POLICY.search(head):
        return "AI 보안&정책", "4) 정부 정책·예산·규제"
    if RULE_SAFETY.search(head):
        return "AI 보안&정책", "5) 위협·안전·소송"
    if dcfin or RULE_INFRA.search(head):
        return "AI DC & Infra", "6) 물리 인프라·DC 차입"
    if RULE_ROBOT.search(head):
        return "Physical AI", "7) 로봇·자율주행"
    return None


def check_rules(path):
    """발송본 각 기사를 판정 순서와 대조. [(섹션, 헤드라인, 권장, 사유)] — 8~10번(모델·에이전트·기업)은 낱말로 못 갈라 찍지 않는다."""
    text = open(path, encoding="utf-8").read()
    hits, sec, cur, blocks = [], None, None, []
    for line in text.splitlines():
        m = re.match(r"^\*\*■\s*(.+?)\*\*\s*$", line)
        if m:
            sec = m.group(1).strip()
            continue
        m = re.match(r"^\*\*\d+\\?\.\s*(.+?)\*\*", line)
        if m:
            cur = [sec, m.group(1), ""]
            blocks.append(cur)
            continue
        if cur is not None and "주요 내용" in line:
            cur[2] = line
    for sec, head, body in blocks:
        p = predict(head, body)
        if p and norm(p[0]) != norm(sec or ""):
            hits.append((sec, head, p[0], p[1]))
    return hits


# [2026-11-09 사용자 지시] 배치표 강제 — 번호 -> 놓을 수 있는 섹션
PLACEMENT_FROM = "2026-11-09"
RULE_CAT = {
    "0": set(),  # 실적은 싣지 않음
    "1": {"국책과제"},
    "2": {"AI 투자&시장"},
    "3": {"AI Model", "AI Agent", "AI DC & Infra"},
    "4": {"AI 보안&정책"},
    "5": {"AI 보안&정책"},
    "6": {"AI DC & Infra"},
    "7": {"Physical AI"},
    "8": {"AI Model"},
    "9": {"AI Agent"},
    "10": {"Enterprise AI"},
    "경계": {"AI 투자&시장", "AI DC & Infra", "AI Agent"},  # CATEGORY_RULES '경계 결정' 줄
}


def parse_brief_urls(path):
    """[(섹션명, 번호, 헤드라인, url), ...]"""
    out, cur = [], None
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        ms = re.match(r"^\*\*■\s*(.+?)\*\*\s*$", line)
        if ms:
            cur = ms.group(1).strip()
            continue
        mi = re.match(r"^\*\*(\d+)\\?\.\s*(.+?)\*\*\s*\(.*?\]\((https?://[^)]+)\)", line)
        if mi and cur is not None:
            out.append((cur, int(mi.group(1)), mi.group(2).strip(), mi.group(3).strip()))
    return out


def placement_path(day):
    return os.path.join(ROOT, "out", "placement-%s.csv" % day)


def write_placement_template(path, day):
    import csv
    pp = placement_path(day)
    if os.path.exists(pp):
        print("[cats] 배치표가 이미 있습니다(덮어쓰지 않음): %s" % pp)
        return
    with open(pp, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["url", "배치번호", "근거", "헤드라인"])
        for sec, _, head, url in parse_brief_urls(path):
            w.writerow([url, "", "", head])
    print("[cats] 빈 배치표 저장: %s" % pp)
    print("       기사마다 0번부터 차례로 묻고 첫 '예'의 번호·근거를 채운 뒤 그 표대로 섹션에 놓을 것.")


def check_placement(path, day):
    """오류 문자열 목록. PLACEMENT_FROM 이전 날짜는 검사하지 않는다."""
    import csv
    if day < PLACEMENT_FROM:
        return []
    pp = placement_path(day)
    if not os.path.exists(pp):
        return ["배치표 없음: %s — `run.sh cats --template` 로 만들고 기사마다 판정 순서 번호·근거를 채울 것" % os.path.basename(pp)]
    rows = {}
    for r in csv.DictReader(open(pp, encoding="utf-8")):
        rows[(r.get("url") or "").strip()] = r
    errs = []
    for sec, num, head, url in parse_brief_urls(path):
        r = rows.get(url)
        tag = "%s %d번 '%s'" % (sec, num, head[:30])
        if not r:
            errs.append("배치표에 행 없음: %s (url 기준 — 링크를 바꿨으면 표도 다시 채울 것)" % tag)
            continue
        n = (r.get("배치번호") or "").strip()
        why = (r.get("근거") or "").strip()
        if n not in RULE_CAT:
            errs.append("배치번호가 0~10·'경계' 가 아님(%r): %s" % (n, tag))
            continue
        if n == "0":
            errs.append("0번(실적)은 싣지 않습니다: %s" % tag)
            continue
        if sec not in RULE_CAT[n]:
            errs.append("배치번호 %s 는 %s 인데 %s 에 놓음: %s" % (n, "/".join(sorted(RULE_CAT[n])), sec, tag))
        if len(why) < 10:
            errs.append("근거가 비었거나 너무 짧음(10자 미만): %s" % tag)
        if n == "9" and "8번 아님" not in why:
            errs.append("9번인데 근거에 '8번 아님:<왜 모델 자체의 출시·성능·학습이 아닌가>' 가 없음: %s" % tag)
        if n == "10" and ("8번 아님" not in why or "9번 아님" not in why):
            errs.append("10번인데 근거에 '8번 아님:'·'9번 아님:' 이 없음(11-09 SK바이오팜 사고): %s" % tag)
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--stem", default="brief")
    ap.add_argument("--strict", action="store_true",
                    help="배치 불일치 경고도 종료코드 1 로 올린다")
    ap.add_argument("--template", action="store_true",
                    help="[2026-11-09] 빈 배치표 out/placement-<날짜>.csv 를 만든다(있으면 두고)")
    a = ap.parse_args()

    path = os.path.join(ROOT, "out", "%s-%s.md" % (a.stem, a.date))
    if not os.path.exists(path):
        print("[cats] [실패] %s 없음" % path)
        sys.exit(2)

    if a.template:
        write_placement_template(path, a.date)
        sys.exit(0)

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

    # (4-1) [2026-11-09 사용자 지시] 배치표 대조 — 강제
    errors.extend(check_placement(path, a.date))

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

    # (6) [2026-10-30 사용자 확정] 배치 원칙 대조 — 경고 전용(발송은 막지 않음).
    #     원칙 정본은 brief.py CATEGORY_RULES. 낱말로 '무슨 일인가'를 추정해 어긋난 것만 찍는다.
    rule_hits = check_rules(path)

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

    if rule_hits:
        print("  [배치규칙] 원칙(brief.py CATEGORY_RULES)과 어긋나 보이는 건 %d개 — 본문으로 확인해 옮기거나 사유를 알림에 적을 것:" % len(rule_hits))
        for n, head, want, why in rule_hits:
            print("         %s -> %s 권장 (%s) | %s" % (n, want, why, head[:44]))

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
