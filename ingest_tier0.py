# -*- coding: utf-8 -*-
"""
ingest_tier0.py - tier0 트렌드 로그 3개 섹션을 하나로 수집·정규화한다.

입력: hijsh994-maker/slack-trend-backdata 의 logs/YYYY-MM-DD.md
출력: out/tier0-YYYY-MM-DD.json   (refine.py 의 입력)

세 섹션은 성격이 달라서 링크 해결 경로가 각각 다르다:

  ## 메시지 (슬랙 #3_트렌드)      약 17건
      실제 헤드라인 + 링크. 단 링크가 news.google.com / news.nate.com 이라
      원문이 아니다. 제목으로 직링크를 재해결한다.

  ## 텔레그램 - 삼성 글로벌 AI/SW   약 16건
      기사 제목이 아니라 작성자 요약문. 제목 매칭이 원리적으로 불가.
      구글뉴스로 실제 헤드라인을 먼저 찾아낸 뒤 링크를 해결한다.
      (실측 발견 94%, 링크 94%)

  ## 텔레그램 - 에이닷비즈뉴스레터  약 50건
      실제 헤드라인 + 매체 + 날짜 + '관련뉴스 N건'(클러스터 크기).
      제목 매칭이 잘 듣는다. (실측 96%)

사용:
  python ingest_tier0.py                    # 오늘자 로그를 GitHub 에서 받아 처리
  python ingest_tier0.py --date 2026-07-22
  python ingest_tier0.py --file ./log-2026-07-22.md
  python ingest_tier0.py --sections adot     # 특정 섹션만
"""
import argparse
import datetime
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

import truststore
truststore.inject_into_ssl()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import naver_api                                        # noqa: E402
import websearch                                        # noqa: E402
import samsung_resolve as SR                            # noqa: E402
from collect import DB, canonical_url, norm_title       # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.join(BASE, "out")
RAW = ("https://raw.githubusercontent.com/hijsh994-maker/"
       "slack-trend-backdata/main/logs/%s.md")

# 섹션 헤더 별칭. 로그 포맷이 여러 번 바뀌어(7/24 대개편) 헤더 문구가 달라진다.
# 파서와 진단이 같은 목록을 공유해, 헤더가 바뀌어도 '헤더 없음(상류 실패)'과
# '헤더는 있는데 0건(포맷 변경)'을 구분해 낼 수 있게 한다.
SECTION_HEADERS = {
    "slack":   ["## 메시지", "## 슬랙 #3_트렌드", "## 슬랙 #3트렌드"],
    "samsung": ["## 텔레그램 - 삼성 글로벌 AI/SW 뉴스"],
    "adot":    ["## 텔레그램 - 에이닷비즈뉴스레터"],
}


def find_section(md, key):
    """key 섹션의 본문(다음 '## ' 헤더 직전까지)을 반환. 헤더 없으면 None."""
    for h in SECTION_HEADERS[key]:
        if h in md:
            return md.split(h, 1)[1].split("\n## ")[0]
    return None


# 섹션별 정상 건수 범위. 벗어나면 경고한다.
# 텔레그램 두 섹션은 로컬 예약작업이 만들기 때문에 Claude 앱이 꺼져 있으면
# 통째로 결번된다. 17건짜리 브리프가 조용히 나가는 것이 최악이라 반드시 감지한다.
# 하한을 두되 헤더 존재 여부로 원인을 분기한다(main 참조).
EXPECTED = {"slack": (10, 30), "samsung": (8, 25), "adot": (30, 70)}


def log(m):
    print("[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), m))


# ------------------------------------------------------------------ 파서
# 슬랙 제목에 종종 ' - 매체명' 접미가 붙는다(7/24 포맷).
#   "AI 투자 불신에 M7 시총 1200조 증발… - 뉴스1"
# 매체명은 마지막 ' - ' 뒤 짧은 토막(공백 없음)일 때만 떼어 outlet 힌트로 쓴다.
# (본문에 ' - '가 있어도 뒤가 길거나 공백이 있으면 제목의 일부로 둔다)
_SLACK_OUTLET = re.compile(r"^(.*\S)\s+-\s+([가-힣A-Za-z0-9·.]{2,12})$")


def _split_slack_outlet(title):
    m = _SLACK_OUTLET.match(title)
    if m and " " not in m.group(2):
        return m.group(1).strip(), m.group(2).strip()
    return title, None


# [2026-08-21 수정] 제목에 대괄호가 들어간 기사를 통째로 흘리던 버그.
#   종전 정규식 r"\[([^\]]+)\]\((https?://[^)]+)\)" 은 제목에 ']' 를 허용하지
#   않아, 한국 기사에 흔한 아래 세 형태가 **한 건도 안 잡혔다**.
#     [AI 유통 계층의 부상 [정원훈의 AI 트렌드]](url)   <- 꼬리 대괄호
#     [[AI 고속도로] 구글, 마벨까지 품었다](url)         <- 머리 대괄호
#     [특징주] 구글, 스피릿항공 데이터 인수](url)        <- 짝 안 맞는 ']'
#   (슬랙이 긴 제목을 자르면서 여는 '[' 만 날아가 셋째 형태가 생긴다)
#   실측: 최근 14일 #3_트렌드 164건 중 11건(6.7%)이 이 이유로 사라졌다.
#   8일이 영향을 받았고 하루 최대 2건이었다. 손실분은 tier0 사람큐레이션이라
#   채택률이 평균의 2.6배인 구간이다.
#   고친 뒤 164/164(100%).
#   [설계] 대괄호 한 겹 중첩과 짝 안 맞는 ']' 를 허용하되,
#     - ']' 는 바로 뒤에 '(' 가 오지 않을 때만 제목의 일부로 본다.
#       (그래야 진짜 링크 끝 ']( ' 를 삼키지 않는다)
#     - 제목에 개행을 허용하지 않는다. 한 줄에 한 링크이므로 이렇게 두면
#       앞 줄 제목이 뒷 줄 링크를 훔쳐가는 사고가 원천 차단된다.
_SLACK_MD_LINK = re.compile(
    r"\[((?:[^\[\]\n]|\[[^\[\]\n]*\]|\](?!\())*)\]\((https?://[^)\s]+)\)")


def parse_slack(md):
    body = find_section(md, "slack")
    if body is None:
        return []
    out = []
    for t, u in _SLACK_MD_LINK.findall(body):
        title, outlet = _split_slack_outlet(t.strip())
        out.append({"title": title, "orig_link": u, "outlet": outlet})
    return out


def parse_adot(md):
    sec = md.split("## 텔레그램 - 에이닷비즈뉴스레터")
    if len(sec) < 2:
        return []
    lines = [l.rstrip() for l in sec[1].split("\n")]
    meta = re.compile(r"^ㄴ(\S+)\s+(\d{4}\.\d{2}\.\d{2})(?:\s*관련뉴스\s*(\d+)건)?")
    topic_hdr = re.compile(r"^[가-힣A-Za-z0-9]{3,12}$")
    # 날짜에 따라 제목 뒤에 원문 URL 이 붙어 오는 경우가 있다.
    # (7/20 로그에는 있고 7/22 로그에는 없었다) 있으면 링크 해결을 건너뛴다.
    url_re = re.compile(r"\s*\((https?://[^)\s]+)\)\s*$")
    items, pending, purl, topic = [], None, None, None
    for l in lines:
        s = l.strip()
        m = meta.match(s)
        if m and pending:
            items.append({"title": pending, "outlet": m.group(1),
                          "pub_date": m.group(2).replace(".", "-"),
                          "cluster": 1 + (int(m.group(3)) if m.group(3) else 0),
                          "topic": topic, "url": purl})
            pending, purl = None, None
        elif s.startswith("신규 ") or s.startswith("**["):
            continue
        elif topic_hdr.match(s) and not pending:
            topic = s                      # 'AI모델동향' 같은 토픽 헤더
        elif s and not s.startswith(("ㄴ", "#", "총 ")) and len(s) > 8:
            um = url_re.search(s)
            purl = um.group(1) if um else None
            pending = url_re.sub("", s).strip()
        elif not s:
            pending, purl = None, None
    return items


# [2026-08-22] curated(개인테스트 Daily AI Tech Trends) 파서 삭제.
#   08-06 에 수집을 껐고 앞으로도 안 들어온다는 사용자 확인에 따라
#   _BOOM/_CUR_ITEM/_CUR_CAT/parse_curated/do_curated 를 전부 지웠다.
#   되살릴 일이 생기면 git 이력에서 2026-08-21 판을 참고할 것.


# ------------------------------------------------------------------ 링크 해결
def resolve_by_title(title, outlet, lo, hi):
    """실제 헤드라인 -> 직링크. 인덱스 -> 네이버뉴스 -> 웹문서 순.

    발행일 창(lo~hi = 전일~당일) 밖 기사는 후보에서 배제한다.
    (예: 'SKT 정재헌 MWC' 가 3월 원문으로 연결되던 버그)
    """
    import difflib
    lo_s, hi_s = lo.isoformat(), hi.isoformat()
    if outlet:
        try:
            conn = sqlite3.connect(DB)
            n = norm_title(title)
            best, sc = None, 0.0
            for t, u, pub in conn.execute(
                    "SELECT title, url, published FROM articles WHERE outlet=?",
                    (outlet,)):
                pd = (pub or "")[:10]
                if pd and not (lo_s <= pd <= hi_s):
                    continue
                r = difflib.SequenceMatcher(None, n, norm_title(t)).ratio()
                if r > sc:
                    sc, best = r, u
            conn.close()
            if best and sc >= 0.75:
                return {"url": best, "via": "index", "score": round(sc, 3)}
        except Exception:
            pass

    r = naver_api.resolve(title, outlet, date_lo=lo, date_hi=hi)
    if r.get("status") == "resolved":
        return {"url": r["url"], "via": "naver", "score": r["score"]}

    # 웹문서 검색은 발행일을 알 수 없어 창 검증 불가. 제목 매칭 강도로만 판단.
    w = websearch.resolve(title, outlet)
    if w:
        return {"url": w["url"], "via": "webkr", "score": w["score"]}
    return None


# ------------------------------------------------------------------ 섹션 처리
def discover(text, lo, hi, window="2d"):
    """요약문/번역제목 -> 구글뉴스로 '실제 한국어 헤드라인'을 찾는다.

    삼성 섹션과 슬랙의 기계번역 제목이 같은 문제를 갖는다:
    그 문장 그대로는 어느 매체에도 존재하지 않아 제목 매칭이 통하지 않는다.
    키워드로 구글뉴스를 뒤져 실재하는 헤드라인으로 한 번 바꾼 뒤 링크를 건다.
    """
    ent, best = SR.entity_of(text), None
    for q in SR.queries(text):
        for c in SR.gnews(q, window):
            if c["date"] and not (lo <= c["date"] <= hi):
                continue
            if SR.source_rank(c["outlet"]) < 0 or SR.is_roundup(c["headline"]):
                continue
            rank = SR.domestic_pref(c["outlet"])   # 요약문 링크는 국내 매체 선호
            d = SR.distinctive(text) & SR.distinctive(c["headline"])
            ent_toks = set(re.findall(r"[가-힣A-Za-z]{2,}", ent)) | \
                set(re.findall(r"[가-힣A-Za-z]{2,}", SR.flat(ent)))
            ent_ok = any(t in SR.flat(c["headline"]) for t in ent_toks)
            sc = (len(d), ent_ok, rank)
            if best is None or sc > best[0]:
                best = (sc, c, sorted(d))
        if best and best[0][0] >= 2 and best[0][1]:
            break
    if not best:
        return None, "drop"
    nd, eo = best[0][0], best[0][1]
    conf = "auto" if (nd >= 2 and eo) else ("review" if (nd >= 1 and eo) else "drop")
    return best[1], conf


def do_slack(items, lo, hi, window="2d"):
    out = []
    for it in items:
        # 원 링크는 포털/리다이렉트라 그대로 쓰지 않는다. 반드시 재해결.
        # 제목에서 떼어낸 매체 힌트가 있으면 인덱스 매칭에 쓴다(정확도↑).
        r = resolve_by_title(it["title"], it.get("outlet"), lo, hi)
        rec = {"src": "slack", "title": it["title"], "outlet": it.get("outlet"),
               "pub_date": None, "cluster": 1, "topic": None,
               "url": r["url"] if r else None,
               "url_via": r["via"] if r else None,
               "confidence": "auto" if r else "unresolved",
               "portal_link": it["orig_link"]}

        # 제목 매칭 실패 = 구글뉴스가 영문 기사를 기계번역한 제목일 가능성이 높다.
        # ("OpenAI는 허깅 페이스가 ... 해킹되었다고 말했습니다." 같은 문장)
        # 이런 문장은 국내 어느 매체에도 없으므로 삼성 섹션과 같은 경로를 태운다.
        #
        # 단 이 경로는 신뢰도가 낮다. 실측에서
        #   '구글, 저렴한 모델과 새로운 Mythos 경쟁자를 추가하여 Gemini 라인업 확장'
        # 이 'AI뉴스 - GLM-5.2 열풍, 구글 심각한 위기...' 라는 뉴스 모음 기사로
        # 잘못 연결됐다. 그래서 두 겹으로 막는다:
        #   (1) discover 가 auto 로 판정한 것만 받는다 (review 는 버림)
        #   (2) 통과해도 confidence 는 review 로 낮춰 사람이 반드시 확인하게 한다
        if not r:
            c, conf = discover(it["title"], lo, hi, window)
            if c and conf == "auto":
                link = SR.resolve_link(c["headline"], c["outlet"], lo, hi)
                if link:
                    rec.update({"title": c["headline"], "outlet": c["outlet"],
                                "pub_date": str(c["date"]) if c["date"] else None,
                                "url": link["url"], "url_via": link["via"],
                                "confidence": "review",
                                "translated_title": it["title"]})

        # [2026-08-03] 마지막 폴백: 원 링크가 '구글뉴스 RSS 가 아닌 실제 기사 URL'
        # 이면 그냥 그걸 쓴다.
        #   실측(08-03): 슬랙 12건 중 링크 확보 1건(8%). 미해결 11건 가운데 3건은
        #   원 링크가 news.nate.com 직링크였는데, 위 '포털 링크는 재해결' 규칙이
        #   무조건 버리고 있었다. 나테는 source_rank=1 인 정상 매체라 버릴 이유가
        #   없다. 그날 사람이 실제 발송한 기사 1건(구글 이미지 생성 철회)이
        #   여기서 사라졌다.
        # 구글뉴스 RSS(news.google.com/rss/articles/...)는 제외한다. 그건 기사 URL
        # 이 아니라 리다이렉터이고, 신형 포맷이라 base64 디코드도 되지 않는다
        # (실측 0/8). 그 8건은 프롬프트의 [링크 미확보 후보]로 넘겨 WebSearch 로 푼다.
        if not rec["url"] and it.get("orig_link"):
            u = it["orig_link"]
            host = urllib.parse.urlparse(u).netloc.lower()
            if (u.startswith("http") and "news.google.com" not in host
                    and SR.source_rank(host) >= 0):
                rec.update({"url": u, "url_via": "orig_link",
                            "confidence": "orig_link"})
        out.append(rec)
    return out


def do_adot(items, lo, hi):
    out = []
    for it in items:
        # 로그에 원문 URL 이 이미 있으면 링크 해결을 건너뛴다 (가장 정확)
        if it.get("url"):
            out.append({"src": "adot", "title": it["title"], "outlet": it["outlet"],
                        "pub_date": it["pub_date"], "cluster": it["cluster"],
                        "topic": it["topic"], "url": it["url"],
                        "url_via": "log", "confidence": "auto"})
            continue
        r = resolve_by_title(it["title"], it["outlet"], lo, hi)
        out.append({"src": "adot", "title": it["title"], "outlet": it["outlet"],
                    "pub_date": it["pub_date"], "cluster": it["cluster"],
                    "topic": it["topic"],
                    "url": r["url"] if r else None,
                    "url_via": r["via"] if r else None,
                    "confidence": "auto" if r else "unresolved"})
    return out




def do_samsung(bullets, lo, hi, window="2d"):
    """요약문 -> 구글뉴스로 헤드라인 발견 -> 링크 해결."""
    out = []
    for b in bullets:
        c, conf = discover(b, lo, hi, window)
        link = SR.resolve_link(c["headline"], c["outlet"], lo, hi) \
            if (c and conf != "drop") else None
        out.append({"src": "samsung", "summary": b,
                    "title": c["headline"] if c else None,
                    "outlet": c["outlet"] if c else None,
                    "pub_date": str(c["date"]) if c and c["date"] else None,
                    "cluster": 1, "topic": None,
                    "url": link["url"] if link else None,
                    "url_via": link["via"] if link else None,
                    "confidence": conf if link else
                                  ("unresolved" if conf != "drop" else "drop")})
    return out


# ------------------------------------------------------------------ 교차 중복
def cross_dedup(items):
    """섹션 간 중복 표시.

    같은 기사가 두 섹션 이상에 등장하면 그만큼 중요한 사건이라는 신호다.
    지우지 않고 seen_in 으로 표시만 해서 refine.py 의 임팩트 계산에 넘긴다.
    """
    # 1) URL 완전일치
    by_url = {}
    for it in items:
        if it.get("url"):
            by_url.setdefault(canonical_url(it["url"]), []).append(it)

    # 2) 변별토큰 겹침. 섹션마다 제목 표기가 달라(번역/요약/원문) 완전일치로는
    #    거의 잡히지 않는다. 실제로 exact 방식은 중복 0건이었다.
    groups = [g for g in by_url.values() if len(g) > 1]
    toks = [(it, SR.distinctive(it.get("title") or it.get("summary") or ""))
            for it in items]
    for i in range(len(toks)):
        for j in range(i + 1, len(toks)):
            a, ta = toks[i]
            b, tb = toks[j]
            if a["src"] == b["src"] or not ta or not tb:
                continue
            inter = ta & tb
            if len(inter) >= 3 or (len(inter) >= 2 and
                                   len(inter) / min(len(ta), len(tb)) >= 0.5):
                groups.append([a, b])

    for g in groups:
        srcs = sorted({x["src"] for x in g})
        if len(srcs) < 2:
            continue
        for x in g:
            x["seen_in"] = sorted(set(x.get("seen_in", [x["src"]])) | set(srcs))
    for it in items:
        it.setdefault("seen_in", [it["src"]])
    return items


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (기본: 오늘)")
    ap.add_argument("--file", help="로컬 로그 파일 경로")
    ap.add_argument("--sections", default="slack,samsung,adot")
    ap.add_argument("--window", default="2d", help="구글뉴스 when: 파라미터")
    a = ap.parse_args()

    day = (datetime.date.fromisoformat(a.date) if a.date else datetime.date.today())
    lo, hi = day - datetime.timedelta(days=1), day
    want = {s.strip() for s in a.sections.split(",")}

    if a.file:
        md = open(a.file, encoding="utf-8").read()
        log("로컬 파일: %s (%d자)" % (a.file, len(md)))
    else:
        url = RAW % day.isoformat()
        log("GitHub 로그 수신: %s" % url)
        try:
            md = urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "TIR"}),
                timeout=20).read().decode("utf-8")
        except Exception as e:
            log("수신 실패: %s" % e)
            log("로컬 예약작업(09:21)이 아직 안 돌았거나 Claude 앱이 꺼져 있었을 수 있음")
            sys.exit(1)
        log("수신 완료 (%d자)" % len(md))

    slack_raw = parse_slack(md)
    sams_raw = SR.parse_samsung(md)
    adot_raw = parse_adot(md)

    counts = {"slack": len(slack_raw), "samsung": len(sams_raw),
              "adot": len(adot_raw)}
    # 헤더 존재 여부를 따로 본다. 0건일 때 원인이
    #   (a) 헤더 자체가 없음   -> 상류 수집 실패(텔레그램 off / 예약작업 미실행)
    #   (b) 헤더는 있는데 0건 -> 로그 포맷 변경(파서 점검 필요)
    # 로 갈린다. 예전엔 둘을 뭉뚱그려 7/23 삼성 포맷 변경을 'Telethon 만료'로
    # 오진했다. 이제 메시지를 분기해 다음 포맷 변경이 즉시 드러나게 한다.
    present = {k: find_section(md, k) is not None for k in SECTION_HEADERS}
    warnings = []
    for k, (mn, mx) in EXPECTED.items():
        n = counts[k]
        if n == 0:
            if k == "samsung" and present[k]:
                # [2026-09-15] 삼성 소스가 슬랙으로 바뀌면서 형식이 바뀌었다.
                #   신 형식(번호+`o 링크:`)은 tier0 이 아니라 `run.sh samsung-slack`
                #   이 지메일 파서로 처리한다. 여기서 0건인 것이 **정상**이다.
                warnings.append("[정상] 'samsung' 0건 - 신 형식은 "
                                "`run.sh samsung-slack` 이 맡습니다(09-15 전환). "
                                "tier0 뒤에 그 명령을 잊지 마세요")
            elif present[k]:
                warnings.append("[포맷변경] '%s' 헤더는 있으나 0건 파싱됨. "
                                "로그 포맷이 바뀌었을 수 있음 - 파서 점검 필요" % k)
            else:
                warnings.append("[결번] '%s' 섹션 헤더 없음. 상류 미수집 "
                                "(텔레그램 off / 예약작업 미실행 / 포맷 개편)" % k)
        elif not (mn <= n <= mx):
            warnings.append("[주의] '%s' %d건. 정상 범위 %d~%d 를 벗어남 "
                            "(포맷 변경 가능성)" % (k, n, mn, mx))
    log("파싱: 슬랙 %d / 삼성 %d / 에이닷 %d" % (
        counts["slack"], counts["samsung"], counts["adot"]))
    for w in warnings:
        log(w)

    items, t0 = [], time.time()
    if "adot" in want:
        log("에이닷 링크 해결 중...")
        items += do_adot(adot_raw, lo, hi)
    if "slack" in want:
        log("슬랙 링크 해결 중...")
        items += do_slack(slack_raw, lo, hi, a.window)
    if "samsung" in want:
        log("삼성 발견+링크 해결 중...")
        items += do_samsung(sams_raw, lo, hi, a.window)

    items = cross_dedup(items)

    linked = sum(1 for i in items if i["url"])
    dup = sum(1 for i in items if len(i["seen_in"]) > 1)
    log("완료 %d건 | 링크 %d (%.0f%%) | 섹션중복 %d | %.0f초"
        % (len(items), linked, linked / max(len(items), 1) * 100, dup, time.time() - t0))

    by_src = {}
    for i in items:
        d = by_src.setdefault(i["src"], [0, 0])
        d[0] += 1
        d[1] += 1 if i["url"] else 0
    for s, (n, l) in by_src.items():
        log("  %-8s %2d건 중 링크 %2d (%.0f%%)" % (s, n, l, l / n * 100))

    os.makedirs(OUTDIR, exist_ok=True)
    path = os.path.join(OUTDIR, "tier0-%s.json" % day.isoformat())
    json.dump({"date": day.isoformat(),
               "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
               "counts": counts, "warnings": warnings, "items": items},
              open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log("저장: %s" % path)


if __name__ == "__main__":
    main()
