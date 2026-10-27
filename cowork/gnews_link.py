# -*- coding: utf-8 -*-
"""구글뉴스 기사 주소 해독 — 영문 기사 링크 확보 경로 4 (2026-10-27 신설)

[왜] 10-27 사용자: *"이런 중요한 기사가 자꾸 빠지는게 마음이 안드네. 오늘 수정한 버전에서도
  링크를 못찾아서 빠지게 되는건지 검증해봐"* (Linux Foundation 'Agentic AI Foundation 4개 신규 프로젝트')
  실측: 이 발표는 10-23(N120)·10-27(N87) 두 번 후보에 올랐지만 둘 다 링크 미확보로 '중'.
  원인 — 구글뉴스 수집(samsung_resolve.gnews)은 RSS 의 <link>(news.google.com/rss/articles/…
  리다이렉트 주소)를 버리고 제목만 쓴다. 그 뒤 링크 해결은 **네이버 API(뉴스·웹문서)** 만 쓰는데
  해외 영문 기사는 네이버에 거의 없다. 10-27 refined: 영문 후보 163건 중 97건(60%) 링크 없음
  (국문은 337건 중 45건, 13%). 영문 기사는 구조적으로 링크를 못 얻고 있었다.
  10-27 오늘 고친 run.sh find 도 네이버라 영문은 못 찾는다(검증: 0건).

[무엇을] 제목으로 구글뉴스 RSS 를 다시 검색해 제목이 같은 항목을 고르고, 그 항목의
  news.google.com/rss/articles/<id> 주소를 구글뉴스 batchexecute(garturlreq)로 원문 주소로 바꾼다.
  (기사 페이지의 data-n-a-sg·data-n-a-ts 서명값이 필요해 항목당 HTTP 3회, 2~4초.)
  제목 유사도 0.75 이상 + 발행일 창 안 + 원문 도메인이 수집·미러 사이트가 아닐 때만 채택한다.

    from gnews_link import resolve
    resolve("AGNTCon + MCPCon 2026: Agentic AI Foundation Adds Four Projects ...", "infoq.com")
"""
import difflib
import html
import json
import re
import threading
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
TIMEOUT = 15
_SEM = threading.Semaphore(3)          # 구글 쪽 차단을 피하려 동시 3건까지
BLOCK = ("news.google.com", "msn.com", "biggo.com", "newsbreak.com", "flipboard.com",
         "ground.news", "inkl.com")
RSS = {"en": "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en",
       "ko": "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"}


def _get(url, data=None, ctype=None):
    h = dict(UA)
    if ctype:
        h["Content-Type"] = ctype
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read(900000).decode("utf-8", "ignore")


def _norm(t):
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", (t or "")).lower()


def decode(article_url):
    """news.google.com/rss/articles/<id> -> 원문 주소. 실패하면 None."""
    m = re.search(r"/articles/([^?/]+)", article_url or "")
    if not m:
        return None
    aid = m.group(1)
    page = _get("https://news.google.com/rss/articles/" + aid)
    sg = re.search(r'data-n-a-sg="([^"]+)"', page)
    ts = re.search(r'data-n-a-ts="([^"]+)"', page)
    if not (sg and ts):
        return None
    inner = ["garturlreq", [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None,
                             None, None, None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
             aid, int(ts.group(1)), sg.group(1)]
    body = "f.req=" + urllib.parse.quote(json.dumps([[["Fbv4je", json.dumps(inner)]]]))
    r = _get("https://news.google.com/_/DotsSplashUi/data/batchexecute", data=body.encode(),
             ctype="application/x-www-form-urlencoded;charset=UTF-8")
    m = re.search(r'garturlres\\",\\"(https?://[^\\"]+)', r)
    return m.group(1) if m else None


def variants(title):
    """질의 변형. 실측 10-27: '+'·':' 가 든 제목을 따옴표로 통째 검색하면 0건이었다."""
    clean = re.sub(r"[\[\]\(\)\"“”‘’'+:|…·,–—\-]+", " ", title)
    clean = re.sub(r"\s+", " ", clean).strip()
    words = clean.split()
    out = [clean, " ".join(words[:8]), " ".join(words[-8:])]
    seen, res = set(), []
    for q in out:
        if len(q) >= 8 and q not in seen:
            seen.add(q)
            res.append(q)
    return res


def search(title, locale, q=None):
    x = _get(RSS[locale].format(q=urllib.parse.quote_plus(q or title)))
    out = []
    for item in re.findall(r"<item>(.*?)</item>", x, re.S):
        t = html.unescape(re.search(r"<title>(.*?)</title>", item, re.S).group(1))
        src = re.search(r"<source[^>]*>(.*?)</source>", item, re.S)
        src = html.unescape(src.group(1)) if src else ""
        if src and t.endswith(" - " + src):
            t = t[:-(len(src) + 3)]
        link = html.unescape(re.search(r"<link>(.*?)</link>", item, re.S).group(1))
        pd = re.search(r"<pubDate>(.*?)</pubDate>", item, re.S)
        try:
            d = parsedate_to_datetime(pd.group(1)).date() if pd else None
        except Exception:
            d = None
        out.append(dict(title=t.strip(), outlet=src, link=link, date=d))
    return out


def resolve(title, outlet="", lo=None, hi=None, threshold=0.75):
    """제목(+매체)으로 원문 주소를 찾는다. {'url','via','score'} 또는 None."""
    title = (title or "").strip()
    if len(title) < 8:
        return None
    han = len(re.findall(r"[가-힣]", title)) / max(1, len(title))
    loc = "ko" if han >= 0.15 else "en"
    nt = _norm(title)
    with _SEM:
        best, score = None, 0.0
        for q in variants(title):
            try:
                cands = search(title, loc, q)
            except Exception:
                continue
            for c in cands:
                _r = _score(c, nt, outlet, lo, hi)
                if _r > score:
                    best, score = c, _r
            if score >= threshold:
                break
        if not best or score < threshold:
            return None
        try:
            url = decode(best["link"])
        except Exception:
            return None
    if not url or any(b in url for b in BLOCK):
        return None
    return {"url": url, "via": "gnews-decode", "score": round(score, 3), "outlet": best["outlet"]}


def _score(c, nt, outlet, lo, hi):
    import datetime as _dt
    if lo and hi and c["date"] and not (lo - _dt.timedelta(days=1) <= c["date"] <= hi):
        return 0.0
    r = difflib.SequenceMatcher(None, nt, _norm(c["title"])).ratio()
    if outlet and c["outlet"] and _norm(outlet)[:6] and _norm(outlet)[:6] in _norm(c["outlet"]):
        r += 0.05
    return r


if __name__ == "__main__":
    import sys
    print(resolve(" ".join(sys.argv[1:])))
