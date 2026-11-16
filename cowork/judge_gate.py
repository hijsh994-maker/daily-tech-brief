# -*- coding: utf-8 -*-
"""판정 과정 게이트 — 규칙을 '알면서' 건너뛴 길 세 개를 닫는다 (2026-11-16 사용자 지시)

왜 만들었나
-----------
11-16 사용자가 '중' 3건을 '고'감으로 지적했다. 셋 다 기준이 없어서가 아니라 **있는 규칙을 판정 때 안 지켜서** 났다.
  1) 'LLM 재킹 국내 발생'(306) — 본문 캐시가 없었는데 find 도 안 돌리고 제목만 보고 '사례 소개'로 내렸다.
     (판정기준 7항 '링크·본문 미확보는 강등 사유가 아니다'. 09-29 에도 LLM 재킹을 내렸다가 사용자가 되살렸다.)
     rescue 게이트는 사유에 '미확보'라고 **쓴** 건만 잡아서 빠져나갔다.
  2) '피클 대표 인터뷰'(11) — 11-13 카톡 연동 중단(규제·접근권)을 보냈다는 이유로 'G축 발송근거' 를 붙였다.
     오늘 기사는 경쟁 전략·MAU 20만(지표·전략) — 앵글이 다르면 다른 사건(판정기준 5항)인데 비교를 안 했다.
  3) '디지털 리얼티 日 100억달러'(129) — 독립 검토의 'CEO 인터뷰라 공식 발표 미확인'을 본문 확인 없이 '반영'해 내렸다.
     본문은 '미쓰비시와 10년간 100억달러 추가 투자, 193MW→700MW' 를 회사 계획으로 쓰고 있었다.
사용자: 사례집 추가는 의미가 적다 → 코드 게이트로 막는다.

무엇을 하나 (종료코드 1)
-----------------------
J1 본문 없이 내림: (가) 사람이 고른 통로(사람큐레이션·교차·경쟁사속보·SK그룹행사)의 '중' 이거나
   (나) 제목이 보안·사고 낱말(해킹·재킹·유출·공격·랜섬·취약·탈취·피싱·딥페이크·사이버·침해)인 '중'·'저' 가
   (반향·커뮤니티 통로는 영문 블로그가 많아 buzz·indep 가 따로 본다 — 첫 실측 11-16 에 J1 39건 중 22건이 그쪽 잡음)
   **본문 캐시가 없는데** 사유에 `find:` 도 `본문:` 도 없으면 걸림.
   닫힌 코드(중복·시황실적·AI무관·배제·운영공지·사설칼럼·자동:)로 내린 행은 대상 아님.
J2 앵글 비교 없는 신규성 감점: 사유에 `발송근거:` 가 있는 '중'·'저' 는 `앵글:<그때>→<오늘>` 을 적어야 한다.
   앵글 = 투자 | 제품 | 전략·조직 | 규제·사고 | 지표 | 인사. 두 앵글이 **다르면** 걸림(다른 사건 — '고' 로 재판정할 것).
J3 검토자 결론 복사: out/indep-review-<날짜>.csv 에서 처리가 '반영:' 이고 그 건이 지금 '중'·'저' 면(내린 반영),
   처리 칸에 `본문:<인용>` 이 있어야 하고 그 인용이 본문 캐시에 실제로 있어야 한다(본문이 없으면 `find:` 로 대신).

    bash cowork/run.sh judge          # verdict 가 자동 호출
"""
import csv, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bodypeek  # noqa: E402  (blind 메타·URL·본문 캐시 로더 재사용)

CLOSED = re.compile(r"^\s*(자동:|중복|시황실적|AI무관|배제|운영공지|사설칼럼|채용인력)")
WATCH_ROUTE = re.compile(r"사람큐레이션|교차|경쟁사속보|SK그룹행사")
SEC = re.compile(r"해킹|재킹|유출|공격|랜섬|취약|탈취|피싱|딥페이크|사이버|침해", re.I)
EVID = re.compile(r"find:|본문:")
ANGLES = ["투자", "제품", "전략·조직", "규제·사고", "지표", "인사"]
ANGLE_RE = re.compile(r"앵글:\s*(%s)\s*(?:→|->)\s*(%s)" % ("|".join(ANGLES), "|".join(ANGLES)))
QUOTE = re.compile(r"본문:\s*[\"“']?([^\"”'|/]{8,}?)[\"”']?(?:\s*(?:/|\||$))")


def norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def has_body(url, cache):
    if not url:
        return False
    b = cache.get(url)
    if b is None:
        b = next((v for k, v in cache.items() if k.split("?")[0] == url.split("?")[0]), None)
    if isinstance(b, dict):
        b = b.get("text") or b.get("body") or ""
    return bool(b) and len(b) >= 200


def body_text(url, cache):
    b = cache.get(url) or next((v for k, v in cache.items() if k.split("?")[0] == url.split("?")[0]), "")
    if isinstance(b, dict):
        b = b.get("text") or b.get("body") or ""
    return norm(b)


FROM = "2026-11-16"   # 이 날짜부터 강제(첫 실측일)


def main():
    day = sys.argv[1]
    if day < FROM:
        print("[judge] %s 이전 판정은 대상 아님" % FROM)
        return 0
    out = bodypeek.OUT
    vp = os.path.join(out, "verdict-%s.csv" % day)
    if not os.path.exists(vp):
        print("[judge] verdict 없음: %s" % vp)
        return 0
    meta, urls, cache = bodypeek.load(day)
    rows = list(csv.DictReader(open(vp, encoding="utf-8")))
    grade = {r["idx"].strip(): r["grade"].strip() for r in rows}
    errs = []

    # J1
    for r in rows:
        i, g, rs = r["idx"].strip(), r["grade"].strip(), r.get("reason") or ""
        if g not in ("중", "저") or CLOSED.search(rs) or EVID.search(rs):
            continue
        m = meta.get(i) or {}
        if "발송근거:" in rs:   # 기발송 근거로 내린 건은 J2 가 본다
            continue
        sec = SEC.search(m.get("head", ""))
        if not (sec or (g == "중" and WATCH_ROUTE.search(m.get("route", "")))):
            continue
        if has_body(urls.get(i, ""), cache):
            continue
        errs.append(("J1", i, "%s %s | 본문 캐시 없음인데 find:/본문: 없이 '%s' — %s"
                     % (m.get("route", "?"), m.get("head", "")[:50], g, rs[:60])))

    # J2
    for r in rows:
        i, g, rs = r["idx"].strip(), r["grade"].strip(), r.get("reason") or ""
        if g not in ("중", "저") or "발송근거:" not in rs:
            continue
        a = ANGLE_RE.search(rs)
        if not a:
            errs.append(("J2", i, "발송근거만 있고 `앵글:<그때>→<오늘>` 없음 — %s" % rs[:70]))
        elif a.group(1) != a.group(2):
            errs.append(("J2", i, "앵글이 다름(%s→%s) = 다른 사건. '고' 로 재판정하거나 같은 앵글임을 다시 확인 — %s"
                         % (a.group(1), a.group(2), (meta.get(i) or {}).get("head", "")[:50])))

    # J3
    ip = os.path.join(out, "indep-review-%s.csv" % day)
    if os.path.exists(ip):
        for x in csv.DictReader(open(ip, encoding="utf-8")):
            i = (x.get("idx") or "").strip()
            done = (x.get("처리") or "").strip()
            if not done.startswith("반영:") or grade.get(i) not in ("중", "저"):
                continue
            url = urls.get(i, "")
            if not has_body(url, cache):
                if "find:" not in done:
                    errs.append(("J3", i, "검토자 의견으로 내렸는데 본문이 없고 `find:` 도 없음"))
                continue
            q = QUOTE.search(done)
            if not q:
                errs.append(("J3", i, "검토자 의견으로 내렸는데 처리 칸에 `본문:<인용>` 없음 — 검토자 결론을 본문으로 확인할 것"))
            elif norm(q.group(1)) not in body_text(url, cache):
                errs.append(("J3", i, "처리 칸 `본문:` 인용이 본문에 없음: %s" % q.group(1)[:40]))

    print("[judge] 판정 과정 점검 — J1 본문없이내림 / J2 앵글비교 / J3 검토반영 근거 — 걸림 %d건" % len(errs))
    for code, i, msg in errs:
        print("  [실패 %s] %s  %s" % (code, i, msg))
    if errs:
        print("[judge] 문구를 덧붙여 통과시키지 말 것: J1 은 run.sh find --body 2 로 본문을 실제로 읽고,")
        print("        J2 는 그때 발송분과 오늘 본문의 앵글을 비교하고, J3 은 본문에서 검토자 주장을 확인한다.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
