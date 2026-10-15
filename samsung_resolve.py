# -*- coding: utf-8 -*-
"""
samsung_resolve.py - 삼성 글로벌 AI/SW 섹션(요약문) -> 기사 링크 복원.

[왜 2단인가]
  네이버 단독은 25%(auto)에 그쳤다. 네이버 뉴스 검색은 제휴 매체만 색인해서
  글로벌 이슈 커버리지가 얕고, 질의 확장도 약하다.
  구글뉴스는 색인 범위가 훨씬 넓지만 <link>가 리다이렉트라 직링크를 못 준다.

  그래서 역할을 나눈다:
    1단계 구글뉴스 = 발견   (어떤 기사가 있는지 + 실제 헤드라인 + 매체 + 발행일)
    2단계 네이버    = 링크   (그 헤드라인으로 originallink 확보)
    2단계 대체 경로 = 로컬 인덱스 (collect.py 적재분)

  요약문 -> 헤드라인으로 한 번 변환되고 나면, 그 뒤는 이미 96% 검증된
  제목 매칭 경로를 그대로 탈 수 있다는 것이 핵심이다.

[날짜] 실행일 하루 전 ~ 실행일 로만 제한한다. 같은 사건을 다룬 과거 기사가
       끌려오는 것이 오탐의 주원인이었다.
"""
import argparse
import datetime
import difflib
import gzip
import html
import json
import os
import re
import sqlite3
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import truststore
truststore.inject_into_ssl()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import naver_api                      # noqa: E402
import websearch                      # noqa: E402
from collect import DB, norm_title    # noqa: E402

UA = "Mozilla/5.0 (compatible; TIR-news-crawler/0.1)"
GNEWS = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"

STOP = set("""
공개 출시 발표 추진 검토 보도 확대 강화 진행 예정 계획 전망 관련 위해 통해
기반 대상 대비 이후 이전 현재 최대 최소 등 및 또한 그리고 하지만 대한 포함
기업 회사 사업 서비스 기능 모델 시장 규모 수준 상황 경우 부분 내용 결과
지난해 올해 내년 상반기 하반기 분기 일환 해석 가능성 시사 논의 협의 체결
""".split())

# 변별력 없는 고빈도 토큰. 이것만 겹치면 같은 기사로 보지 않는다.
# tokens() 가 소문자로 내보내므로 여기도 소문자로 둔다(영문 대소문자 무관 매칭).
GENERIC = set("""
AI CEO IPO GPU CPU LLM MS IT API IP 오픈 모델 클라우드 보안 정부 미국 중국
달러 기술 투자 시장 서비스 플랫폼 데이터 반도체 인프라 파트너십 매출 주가
""".lower().split())

# 삼성 채널 표기 -> 한국 언론 표기
ALIAS = {"앤스로픽": "앤트로픽", "문샷 AI": "문샷", "미스트랄 AI": "미스트랄",
         "팔로알토 네트웍스": "팔로알토", "메타": "메타"}


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
    return gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw


# 조사를 떼지 않으면 '공격을' vs '공격', '샌드박스를' vs '샌드박스' 가
# 서로 다른 토큰이 되어 매칭도 검색도 실패한다.
JOSA = re.compile(r"(을|를|이|가|은|는|에서|에|으로|로|와|과|의|도|만|까지|부터)$")


def depart(t):
    """한글 토큰에서 조사 제거. 3자 미만은 건드리지 않는다."""
    if len(t) >= 3 and re.fullmatch(r"[가-힣]+", t):
        s = JOSA.sub("", t)
        if len(s) >= 2:
            return s
    return t


def tokens(s):
    # 영문은 소문자로 정규화한다. 안 그러면 'Valuation' 과 'valuation' 이 다른
    # 토큰이 되어 같은 사건의 영문 기사들이 클러스터로 쪼개진다(문샷 350억 =
    # 영문 7조각). 한글은 대소문자가 없어 영향 없다.
    t = re.findall(r"[가-힣]{2,}|[A-Za-z][A-Za-z0-9.\-]{1,}|\d+[a-zA-Z%]*", s)
    return [depart(x).lower() for x in t if x not in STOP and len(x) >= 2]


def distinctive(s):
    return {t for t in tokens(s) if t not in GENERIC}


def entity_of(b):
    h = b.split(",")[0].strip()
    return ALIAS.get(h, h)


def flat(s):
    return re.sub(r"[\s\-·]", "", s)


# 인용할 수 없는 포털/미러, 그리고 국내 브리프에 부적합한 비한국어 매체.
# 구글뉴스가 이런 소스를 최상단에 올리는 일이 잦다.
SKIP_SOURCE = ("v.daum.net", "n.news.naver.com", "msn.com", "vietnam.vn",
               "news.google.com", "finance.yahoo.com")


# 여러 주제를 묶은 '뉴스 모음' 기사. 개별 사건의 출처로 쓸 수 없다.
# 실측 오탐: '구글, 저렴한 모델과 새로운 Mythos 경쟁자를 추가하여 Gemini 라인업 확장'
#  -> 'AI뉴스 - GLM-5.2 열풍, 구글 심각한 위기, GPT-5.6 소식, Codex Record &
#      Replay, Mythos 사건, 이더리움 상황 등 Coin Stock (TUIvnvuGEe)'
# 모음 기사는 변별토큰이 많이 겹쳐서 점수가 높게 나오므로 별도로 걸러야 한다.
ROUNDUP = re.compile(r"^\s*(AI\s*뉴스|뉴스\s*모음|주간|오늘의|이주의)\s*[-–—:]")


def is_roundup(headline):
    h = headline or ""
    if ROUNDUP.search(h):
        return True
    if h.count(",") >= 4:            # 쉼표로 여러 토픽을 나열
        return True
    if re.search(r"\([A-Za-z0-9]{8,}\)\s*$", h):   # 말미의 난수형 식별자
        return True
    return False


def source_rank(src):
    """후보 정렬용 매체 선호도.

    [변경] 예전엔 해외 매체를 1점(국내 2점)으로 낮췄는데, 그러면 삼성 요약문의
    링크를 국내 매체로 대체할 때는 맞지만 tier2 원시 수집에서는 해가 된다.
    7/20 브리프 12건 중 7건이 영문 원문(TechCrunch·Reuters·Nikkei 등)이었고,
    한 사건을 국내·해외가 같이 보도하면 국내를 대표로 뽑으면 그만이다.
    그래서 해외 감점을 없애고, 인용 불가 포털/미러만 제외한다.
    """
    s = (src or "").lower()
    if any(b in s for b in SKIP_SOURCE):
        return -1                                   # 포털/미러만 제외
    return 1                                        # 국내·해외 동등


def domestic_pref(src):
    """국내 매체 선호도 0~2. 삼성/슬랙 요약문을 '국내 기사'로 대체할 때만 쓴다.
    (tier2 원시 수집 순위에는 쓰지 않는다 - 거기선 해외도 동등)"""
    s = (src or "").lower()
    if norm_source(src) in SOURCE_ALIAS.values():
        return 2
    if re.search(r"[가-힣]", src or "") or ".kr" in s:
        return 2
    return 1


def parse_samsung(md):
    """슬랙 로그의 삼성 섹션에서 **구 형식(■ 요약)** 불릿만 뽑는다.

    [2026-09-15] 삼성 소스가 슬랙 #dtb-tier0 로 바뀌면서 형식도 바뀌었다.
      구: `■ 요약문` (링크 없음) -> 구글뉴스로 링크를 되짚어야 했다(정확도 40~68%)
      신: `번호. 제목 (매체)` + `o 링크:` -> **링크가 100% 붙어 온다**

    신 형식은 이 함수가 아니라 **`run.sh samsung-slack`** 이 지메일 파서
    (`cowork/newsclips_mail.py`)로 처리한다. 형식이 같으므로 파서를 하나로 둔다.
    여기서 신 형식을 만나면 **조용히 빈 목록을 돌려준다** — 종전에는 0건이
    파싱되면서 '[포맷변경] 파서 점검 필요' 헛경고가 떴다(09-15 실측).
    과거 로그 재채점을 위해 구 형식 파싱은 그대로 남긴다.
    """
    sec = md.split("## 텔레그램 - 삼성 글로벌 AI/SW 뉴스")
    if len(sec) < 2:
        return []
    body = sec[1].split("\n## ")[0]
    if re.search(r"^\s*[o•∙*-]?\s*링크\s*[:：]", body, re.M):
        return []          # 신 형식. samsung-slack 이 맡는다.
    # 불릿 프리픽스 표기가 로그마다 다르다.
    #   7/22: '- ■ ...'  (마크다운 리스트 안의 ■)
    #   7/23: '■ ...'      (■ 만)
    # 둘 다 받아 ■ 뒤 본문만 취한다.
    out = []
    for l in body.split("\n"):
        s = l.strip()
        if s.startswith("- ■"):
            s = s[1:].strip()          # 리스트 마커 '-' 제거
        if s.startswith("■"):
            b = s[1:].strip()
            if b:
                out.append(b)
    return out


def queries(bullet):
    """요약문 -> 구글뉴스 질의. 주체 + 변별키워드.

    영문 제품명/숫자를 먼저 쓰고, 모자라면 조사를 뗀 한글 변별어를 채운다.
    조사를 안 떼면 '샌드박스를' 같은 형태로 검색돼 결과가 안 나온다.
    """
    head = entity_of(bullet)
    rest = bullet[len(bullet.split(",")[0]):]
    strong = [s for s in re.findall(r"[A-Za-z][A-Za-z0-9.\-]{2,}|\d+[가-힣A-Za-z%]*", rest)
              if s not in STOP][:4]
    if len(strong) < 3:
        ko = [depart(t) for t in re.findall(r"[가-힣]{2,}", rest)
              if t not in STOP and depart(t) not in GENERIC and len(depart(t)) >= 2]
        strong += ko[:4 - len(strong)]
    v = []
    if strong:
        v.append("%s %s" % (head, " ".join(strong[:3])))
        v.append("%s %s" % (head, " ".join(strong[:1])))
    v.append(head)
    seen, out = set(), []
    for q in v:
        q = re.sub(r"\s+", " ", q).strip()
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


GNEWS_EN = ("https://news.google.com/rss/search?q={q}"
            "&hl=en-US&gl=US&ceid=US:en")


def gnews(query, window="2d", locale="ko"):
    """구글뉴스 RSS 검색 -> [{headline, outlet, date}]  (link 는 쓰지 않음)

    locale='en' 은 영문 로케일로 검색한다.
    한국어 로케일만 쓰면 영문 원문 기사가 거의 안 잡힌다. 실측에서
    7/20 브리프 12건 중 7건(TechCrunch·CNBC·Reuters·KOB·Nikkei Asia 등)이
    영문 소스였는데 한국어 로케일 수집분에는 하나도 없었다.

    window=None 이면 'when:' 을 붙이지 않는다.
      쿼리에 'after:/before:' 를 직접 넣어 특정 날짜만 긁을 때 쓴다.
      when: 과 after:/before: 를 같이 주면 when: 이 이겨서 날짜 지정이
      무시된다(2026-08-03 실측: when:3d + after:07-30 before:08-01 ->
      07-31 결과 0건, when: 제거 시 정상).
    """
    base = GNEWS_EN if locale == "en" else GNEWS
    q = query if window is None else query + " when:" + window
    url = base.format(q=urllib.parse.quote_plus(q))
    try:
        root = ET.fromstring(fetch(url))
    except Exception:
        return []
    out = []
    for it in root.iter("item"):
        t = (it.findtext("title") or "").strip()
        src = it.find("source")
        pub = it.findtext("pubDate")
        # 구글뉴스 제목은 "헤드라인 - 매체명" 형태
        outlet = (src.text.strip() if src is not None and src.text else "")
        head = t[: -(len(outlet) + 3)] if outlet and t.endswith(" - " + outlet) else t
        d = None
        if pub:
            try:
                from email.utils import parsedate_to_datetime
                d = parsedate_to_datetime(pub).date()
            except Exception:
                pass
        out.append({"headline": html.unescape(head).strip(),
                    "outlet": outlet, "date": d})
    return out


# 구글뉴스 <source> 는 매체명일 때도 있고 도메인일 때도 있다.
# (실측: 'IT조선', '중앙일보' 처럼 이름 / 'edaily.co.kr', 'blog.google' 처럼 도메인)
# 우리 인덱스와 네이버 도메인표는 매체명 기준이라 정규화가 필요하다.
SOURCE_ALIAS = {
    "edaily.co.kr": "이데일리", "etnews.com": "전자신문", "sedaily.com": "서울경제",
    "hankyung.com": "한국경제", "mk.co.kr": "매일경제", "chosun.com": "조선일보",
    "biz.chosun.com": "조선비즈", "donga.com": "동아일보", "khan.co.kr": "경향신문",
    "seoul.co.kr": "서울신문", "asiae.co.kr": "아시아경제", "mt.co.kr": "머니투데이",
    "etoday.co.kr": "이투데이", "zdnet.co.kr": "지디넷코리아", "news1.kr": "뉴스1",
    "aitimes.com": "AI타임스", "inews24.com": "아이뉴스24", "newsis.com": "뉴시스",
    "digitaltoday.co.kr": "디지털투데이", "heraldcorp.com": "헤럴드경제",
}

# 네이버에 아예 색인되지 않는 매체. 링크 해결을 시도할 필요가 없다.
NON_NAVER = ("blog.google", "vietnam.vn", "benzinga", "investing.com",
             "v.daum.net", "openai.com", "anthropic.com")


def norm_source(s):
    s = (s or "").strip()
    key = s.lower()
    if key in SOURCE_ALIAS:
        return SOURCE_ALIAS[key]
    for d, name in SOURCE_ALIAS.items():          # 'www.edaily.co.kr' 형태 대응
        if d in key:
            return name
    return s


def resolve_link(headline, outlet, lo, hi, skip_naver=False):
    """헤드라인 -> 직링크.

    skip_naver=True 면 네이버 '뉴스' 경로(2·4)를 건너뛰고 로컬 인덱스와
    네이버 웹문서(3)만 본다. 영문 제목용이다.
      [근거 2026-08-04] 링크 해결 실패 9건 중 5건이 영문 제목이었고, 영문이
      성공한 유일한 1건도 경로 3(webkr)이었다. 영문 헤드라인으로 네이버
      '뉴스'를 때리는 건 구조적으로 헛일인데, 실패 1건에 5초씩 든다.
      풀의 32%가 영문이므로 그 시간을 국문 후보에 쓰는 편이 낫다.

    1) 로컬 인덱스 (매체명 정규화 후)
    2) 네이버 - 매체 지정
    3) 네이버 - 매체 무시. 구글이 찾아준 매체가 네이버 비제휴여도,
       같은 사건을 다룬 국내 기사가 있으면 그걸로 링크를 건다.
       (수작업으로 하던 방식과 동일. 중요한 건 '어떤 매체냐'가 아니라
        '그 사건을 정확히 다룬 기사냐'다.)
    """
    o = norm_source(outlet)
    lo_s, hi_s = lo.isoformat(), hi.isoformat()

    # 경로1: 로컬 인덱스. 발행일 창(전일~당일) 안의 기사만 후보로.
    # (과거 기사 오링크 방지 - 예: '정재헌 MWC'가 3월 기사로 연결되던 버그)
    try:
        conn = sqlite3.connect(DB)
        n = norm_title(headline)
        rows = conn.execute(
            "SELECT title, url, published FROM articles WHERE outlet=?", (o,)).fetchall()
        best, sc = None, 0.0
        for t, u, pub in rows:
            pd = (pub or "")[:10]
            if pd and not (lo_s <= pd <= hi_s):
                continue
            r = difflib.SequenceMatcher(None, n, norm_title(t)).ratio()
            if r > sc:
                sc, best = r, u
        conn.close()
        if best and sc >= 0.75:
            return {"url": best, "via": "index", "score": round(sc, 3), "outlet": o}
    except Exception:
        pass

    # 경로2: 네이버 웹문서 검색.
    #
    # [순서 변경 2026-08-04] 예전엔 뉴스 API 를 먼저 봤다. 실측해보니 웹문서가
    #   뉴스 API 의 상위집합이었다.
    #     - 뉴스로 풀린 3건은 웹문서도 '같은 URL' 을 돌려줬다(3/3 일치)
    #     - 웹문서로 풀린 5건은 뉴스로는 전부 실패했다
    #     - 속도도 웹문서가 빠르다(성공 1.05초 vs 1.42초). 뉴스가 실패하는
    #       경우 2.2~3.5초를 버리는데, 그 뒤에 웹문서가 결국 찾아낸다.
    #   그래서 웹문서를 먼저 본다. 재현율은 그대로고 실패 대기시간만 준다.
    #   (뉴스 API 는 뒤로 물러나 웹문서가 못 잡은 것만 받는다)
    w = websearch.resolve(headline, outlet)
    if w:
        return {"url": w["url"], "via": "webkr", "score": w["score"],
                "outlet": norm_source(outlet), "title": w["title"]}

    # 경로3: 네이버 뉴스 API. 발행일 창을 넘겨 창 밖 기사는 배제.
    if not skip_naver and not any(x in (outlet or "").lower() for x in NON_NAVER):
        r = naver_api.resolve(headline, o, date_lo=lo, date_hi=hi)
        if r.get("status") == "resolved":
            return {"url": r["url"], "via": "naver", "score": r["score"], "outlet": o}

    # 4) 매체 무시 + 발행일 제약. 국내에 같은 사건 기사가 있으면 그걸로 건다.
    if skip_naver:
        return None
    from email.utils import parsedate_to_datetime
    try:
        items = naver_api.search(headline, display=20)
    except Exception:
        return None
    dh = distinctive(headline)
    cand, cs = None, 0
    for it in items:
        if not (it["originallink"] or "").startswith("http"):
            continue
        try:
            d = parsedate_to_datetime(it["pubDate"]).date()
        except Exception:
            d = None
        if d and not (lo <= d <= hi):
            continue
        ov = len(dh & distinctive(it["title"]))
        if ov > cs:
            cs, cand = ov, it
    if cand and cs >= 2:
        return {"url": cand["originallink"], "via": "naver-any", "score": cs,
                "outlet": "?", "title": cand["title"]}
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--window", default="2d", help="구글뉴스 when: 파라미터")
    a = ap.parse_args()

    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", a.log)
    logdate = datetime.date(*map(int, m.groups())) if m else datetime.date.today()
    lo, hi = logdate - datetime.timedelta(days=1), logdate   # 전일 ~ 당일

    bullets = parse_samsung(open(a.log, encoding="utf-8").read())
    out = ["삼성 섹션 %d건 | 구글뉴스 발견 -> 링크해결" % len(bullets),
           "발행일 제한: %s ~ %s" % (lo, hi), ""]
    res = []

    for i, b in enumerate(bullets, 1):
        ent, best, seen_dates = entity_of(b), None, 0
        for q in queries(b):
            for c in gnews(q, a.window):
                if c["date"] and not (lo <= c["date"] <= hi):
                    seen_dates += 1
                    continue
                if source_rank(c["outlet"]) < 0:   # 포털/미러 제외
                    continue
                if is_roundup(c["headline"]):      # 뉴스 모음 기사 제외
                    continue
                rank = domestic_pref(c["outlet"])  # 삼성 링크는 국내 매체 선호
                d = distinctive(b) & distinctive(c["headline"])
                # 주체 토큰은 원본과 기호제거본을 모두 만들어 비교한다.
                #  - 원본만 쓰면 '미-중' 이 1글자로 쪼개져 토큰이 안 잡힌다.
                #  - 기호제거본만 쓰면 '인텔-포티넷' 이 '인텔포티넷' 한 덩어리가 되어
                #    '포티넷·인텔' 같은 어순 뒤바뀐 제목과 매칭되지 않는다.
                ent_toks = set(re.findall(r"[가-힣A-Za-z]{2,}", ent)) | \
                    set(re.findall(r"[가-힣A-Za-z]{2,}", flat(ent)))
                ent_ok = any(tok in flat(c["headline"]) for tok in ent_toks)
                sc = (len(d), ent_ok, rank)
                if best is None or sc > best[0]:
                    best = (sc, c, q, sorted(d))
            if best and best[0][0] >= 2 and best[0][1]:
                break

        nd = best[0][0] if best else 0
        eo = best[0][1] if best else False
        tier = "auto" if (nd >= 2 and eo) else ("review" if (nd >= 1 and eo) else "drop")

        link = None
        if tier in ("auto", "review") and best:
            link = resolve_link(best[1]["headline"], best[1]["outlet"], lo, hi)

        out.append("%2d. [%-6s] %s" % (i, tier, b[:60]))
        if best:
            out.append("      질의: %s" % best[2])
            out.append("      발견: %s  (%s, %s)"
                       % (best[1]["headline"][:58], best[1]["outlet"], best[1]["date"]))
            out.append("      변별토큰 %d개 %s | 주체 %s | 날짜탈락 %d"
                       % (nd, best[3][:6], eo, seen_dates))
            if link:
                out.append("      링크: %s" % link["url"][:76])
                out.append("            [%s] %s"
                           % (link["via"], link.get("title", link.get("outlet", ""))))
            else:
                out.append("      링크: 해결실패")
        else:
            out.append("      후보 없음 (날짜탈락 %d)" % seen_dates)
        out.append("")
        res.append({"bullet": b, "tier": tier, "distinct": nd,
                    "headline": best[1]["headline"] if best else None,
                    "outlet": best[1]["outlet"] if best else None,
                    "date": str(best[1]["date"]) if best and best[1]["date"] else None,
                    "url": link["url"] if link else None,
                    "link_via": link["via"] if link else None})

    import collections
    tc = collections.Counter(x["tier"] for x in res)
    linked = sum(1 for x in res if x["url"])
    out.insert(2, "  auto %d / review %d / drop %d | 링크확보 %d/%d (%.0f%%)"
               % (tc["auto"], tc["review"], tc["drop"], linked, len(bullets),
                  linked / len(bullets) * 100))
    open("samsung_google.txt", "w", encoding="utf-8").write("\n".join(out))
    json.dump(res, open("samsung_google.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n".join(out[:4]))


if __name__ == "__main__":
    main()
