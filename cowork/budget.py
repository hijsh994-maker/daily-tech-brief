# -*- coding: utf-8 -*-
"""발송본을 쓰기 **전에** 건당 본문 예산을 계산한다.  [2026-10-20 신설]

왜: 10-20 실측 — 20건을 '주요 80·시사점 50~80' 기준으로 먼저 쓰고 나서
    send dry 로 재니 5,008/4,080 자. 2건을 빼고 본문을 5번 압축해서야 1개 메시지에
    들어갔다(약 7분). 건수가 문제가 아니라 '예산을 모른 채 쓰고 나서 재는' 순서가
    문제였다. 상한 20건(09-10)·'넣을 만한 기사는 빼지 않고 분량을 줄인다'(09-28)는
    그대로 두고, 쓰기 전에 고정비를 실제 글자로 더해 건당 예산을 찍는다.

무엇을: send_telegram 과 **같은 자(tg_len = entities 파싱 후 UTF-16 길이)**로
    머리글·카테고리 헤더·구분줄·헤드라인·매체·'원문보기'·라벨까지 들어간 빈 뼈대
    브리프를 만들어 재고, LIMIT 에서 빼서 본문 예산을 나눈다.
    헤드라인은 후보 목록(prompt-<날짜>.txt)의 값을 그대로 쓴다(링크 미확보 N 도 포함).

쓰는 법:
    bash cowork/run.sh budget                 # verdict '고' 를 rank 순으로 상위 20건
    bash cowork/run.sh budget --top 18        # 상위 18건으로 가정
    bash cowork/run.sh budget --idx 123 125 42 3   # 발송 예정 idx 를 직접(순서 무관)
    bash cowork/run.sh budget --idx ... --headline N66="실제 쓸 제목"   # 웹검색으로 바꾼 제목
출력:
    고정비 / 본문 예산 / 건당 예산 / 권장 '주요 N자·시사점 M자' / 건당 헤드라인 길이표
    건당 예산이 110자 아래면 경고(그래도 20건 상한·09-28 결정은 코드가 바꾸지 않는다 —
    압축해서 쓰거나 rank 최하위를 뺄지는 사람이 정한다).
"""
import argparse, csv, datetime, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from send_telegram import LIMIT, md_to_html, tg_len  # noqa: E402

CATEGORY_ORDER = ["AI Model", "AI Agent", "AI DC & Infra", "Physical AI",
                  "Enterprise AI", "AI 보안&정책", "AI 투자&시장", "국책과제", "기타"]
MARGIN = 40          # retitle 로 제목이 길어지거나 '(i/N)' 꼬리표가 붙을 여유
SPLIT_MAIN = 0.55    # 본문 예산 중 '주요 내용' 몫 (80:65 ≈ 55:45)
WARN_PER_ITEM = 110  # 이 아래면 주요 60·시사점 50 도 못 쓴다


def load_candidates(date):
    p = "out/prompt-%s.txt" % date
    if not os.path.exists(p):
        raise SystemExit("[budget] 프롬프트가 없습니다: %s (brief 단계 뒤에 쓰세요)" % p)
    cands = {}
    for line in open(p, encoding="utf-8"):
        if not line.startswith("{"):
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        if "idx" in o:
            cands[str(o["idx"])] = {"headline": o.get("headline") or "", "outlet": o.get("outlet") or "?",
                                   "url": o.get("url") or "", "category": o.get("category") or "기타"}
        elif "nolink_idx" in o:
            cands["N%s" % o["nolink_idx"]] = {"headline": o.get("headline") or "",
                                              "outlet": o.get("outlet_guess") or "?",
                                              "url": "https://example.com/" + "x" * 40,
                                              "category": o.get("category") or "기타"}
    return cands


def go_by_rank(date, top):
    p = "out/verdict-%s.csv" % date
    if not os.path.exists(p):
        raise SystemExit("[budget] verdict 가 없습니다: %s (--idx 로 직접 주세요)" % p)
    rows = [r for r in csv.DictReader(open(p, encoding="utf-8")) if r.get("grade") == "고"]
    def key(r):
        try:
            return int(r.get("rank") or 0) or 10 ** 6
        except ValueError:
            return 10 ** 6
    rows.sort(key=key)
    return [r["idx"] for r in rows][:top]


def skeleton(date, items):
    d = datetime.date.fromisoformat(date)
    wd = "월화수목금토일"[d.weekday()]
    lines = ["**[%d/%d(%s), Daily Tech Brief]**" % (d.month, d.day, wd)]
    bycat = {}
    for it in items:
        bycat.setdefault(it["category"] if it["category"] in CATEGORY_ORDER else "기타", []).append(it)
    first = True
    for cat in CATEGORY_ORDER:
        if cat not in bycat:
            continue
        if not first:
            lines.append("ㅤ")
        first = False
        lines.append("**■ %s**" % cat)
        for n, it in enumerate(bycat[cat], 1):
            if n > 1:
                lines.append("ㅤ")
            lines.append("**%d\\. %s** (%s, [원문보기](%s))" % (n, it["headline"], it["outlet"], it["url"]))
            lines.append("• **주요 내용:** ")
            lines.append("• **시사점:** ")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--top", type=int, default=20, help="verdict '고' 를 rank 순으로 몇 건까지 (기본 20)")
    ap.add_argument("--idx", nargs="*", help="발송 예정 idx (주면 verdict 대신 이걸 쓴다)")
    ap.add_argument("--headline", nargs="*", default=[], help='제목 덮어쓰기: IDX="제목" (웹검색으로 바꾼 것)')
    ap.add_argument("--limit", type=int, default=LIMIT)
    a = ap.parse_args()

    cands = load_candidates(a.date)
    idxs = a.idx if a.idx else go_by_rank(a.date, a.top)
    over = {}
    for h in a.headline:
        k, _, v = h.partition("=")
        over[k.strip()] = v.strip().strip('"')
    items, missing = [], []
    for i in idxs:
        c = cands.get(str(i))
        if not c:
            missing.append(str(i)); continue
        c = dict(c)
        if str(i) in over:
            c["headline"] = over[str(i)]
        c["idx"] = str(i)
        items.append(c)
    if missing:
        print("[budget] 후보 목록에 없는 idx (건너뜀): %s" % ", ".join(missing))
    if not items:
        raise SystemExit("[budget] 계산할 항목이 없습니다")

    n = len(items)
    fixed = tg_len(md_to_html(skeleton(a.date, items)))
    body = a.limit - MARGIN - fixed
    per = body // n
    main_c = int(per * SPLIT_MAIN)
    insight_c = per - main_c
    ncat = len({it["category"] if it["category"] in CATEGORY_ORDER else "기타" for it in items})

    print("[budget] %s / 발송 예정 %d건 / 카테고리 %d개 (기계 분류 기준 — 손배치가 다르면 ±10자)" % (a.date, n, ncat))
    print("[budget] 상한 %d − 여유 %d − 고정비 %d(머리글·헤더·구분줄·헤드라인·매체·원문보기·라벨) = 본문 예산 %d자"
          % (a.limit, MARGIN, fixed, body))
    print("[budget] **건당 본문 %d자 -> 주요 내용 %d자 · 시사점 %d자 안에서 한 번에 쓰세요** (절차서 기준 주요 80·시사점 50~80 은 상한이지 목표가 아님)"
          % (per, main_c, insight_c))
    if per < WARN_PER_ITEM:
        print("[budget] [경고] 건당 %d자는 주요 60·시사점 50 도 안 됩니다. 09-28 결정(압축 우선, 그래도 안 들어가면 rank 최하위부터)을"
              " 사람이 적용하세요. 참고: 1건 빼면 건당 +%d자" % (per, (a.limit - MARGIN - fixed) // max(n - 1, 1) - per))
    print("[budget] 헤드라인 길이(길수록 그 건의 고정비가 큼 — 영문 긴 제목은 retitle 뒤 더 길어질 수 있음):")
    for it in items:
        hl = tg_len(md_to_html(it["headline"]))
        flag = "  <- 길다" if hl > 60 else ""
        print("   %-5s %3d자  %s%s" % (it["idx"], hl, it["headline"][:70], flag))
    print("[budget] 쓰고 나서 반드시 `run.sh send dry` 로 다시 잽니다. 이 명령은 예산 계산기이지 검사가 아닙니다.")


if __name__ == "__main__":
    main()
