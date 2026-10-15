# -*- coding: utf-8 -*-
"""
websearch.py - link_resolution 경로 3 (네이버 웹문서 검색)

경로 1(로컬 인덱스)·경로 2(네이버 뉴스)로 링크를 못 얻는 경우를 받는다.
주로 네이버 뉴스 비제휴 매체다: 블록미디어, 재경일보, 디지털포커스,
kr.investing.com, kr.benzinga.com 등.

[왜 DuckDuckGo 가 아닌가]
  처음엔 DDG lite 로 site: 검색을 붙였는데, 약 10회 요청 만에 차단당해
  단순 질의조차 0건을 반환했다. 무인 운영에 쓸 수 없다.
  네이버 웹문서 검색(webkr)은 이미 발급받은 같은 키를 쓰고 한도가
  25,000콜/일이라 안정적이다. 실측에서 비제휴 매체 기사를 최상단으로 찾아냈다.

[검증] 구글뉴스가 알려준 헤드라인과 제목 유사도를 확인한다.
       요약문이 아니라 실제 헤드라인이 기준이므로 임계값을 높게 둘 수 있다.
"""
import difflib
import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import truststore
truststore.inject_into_ssl()

ENDPOINT = "https://openapi.naver.com/v1/search/webkr.json"
TIMEOUT = 15

# 구글뉴스 <source> 표시명 -> 도메인. 도메인이 확인되면 가산점을 준다.
# (틀린 추정을 하느니 비워두는 편이 낫다. 실측으로 확인된 것만 등록)
SOURCE_DOMAIN = {
    "중앙일보": "joongang.co.kr",
    "블록미디어": "blockmedia.co.kr",
    "재경일보": "jkn.co.kr",            # jkl 아님. 실측 확인
    "위키트리": "wikitree.co.kr",
    "헬로티": "hellot.net",
    "인공지능신문": "aitimes.kr",
    "IT조선": "it.chosun.com",
    "이데일리": "edaily.co.kr",
    "전자신문": "etnews.com",
    "디지털투데이": "digitaltoday.co.kr",
    "디지털데일리": "ddaily.co.kr",
    "Investing.com 한국어": "kr.investing.com",
    "Benzinga": "kr.benzinga.com",
}

# 원문이 아닌 수집/미러/포털
BLOCK = ("news.google.com", "v.daum.net", "n.news.naver.com", "msn.com",
         "biggo.com", "saveticker.com", "newsbreak.com", "blog.naver.com",
         "cafe.naver.com", "tistory.com", "brunch.co.kr")


def creds():
    cid = os.environ.get("NAVER_CLIENT_ID")
    sec = os.environ.get("NAVER_CLIENT_SECRET")
    if not cid or not sec:
        raise RuntimeError("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 없음")
    return cid, sec


def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def norm(s):
    s = re.sub(r"\[[^\]]{0,20}\]", "", s)
    s = re.sub(r"[\s​]+", "", s)
    s = re.sub(r"[^\w가-힣]", "", s)
    return s.lower()


# 네이버 웹문서 API 동시 호출 제한 + 재시도.
#
# [왜 - 2026-07-31] refine 링크해결을 병렬화했더니 순차 대비 링크가 사라졌다.
#   순차는 2회 실행 결과가 완전히 같은데(불일치 0건) 병렬만 잃었으므로 노이즈가
#   아니라 동시성 결함이다. 아래 except 가 429/타임아웃을 '검색 결과 없음'과
#   똑같이 [] 로 만들어 버리는 것이 원인이었다. naver_api 와 같은 처방을 쓴다.
_SEM = threading.Semaphore(3)
_RETRY_STATUS = (429, 500, 502, 503, 504)

# [2026-08-27] '검색 결과 없음' 과 '네트워크가 실패해서 못 봄' 을 구분한다.
#   종전에는 영구 실패도 [] 로 돌려줘 상위(resolve_links)에서 둘을 구분할 수
#   없었고, 그래서 일시적 실패한 건까지 link_tried=True 로 못박아 그날
#   영영 재시도하지 않았다. 실측 08-26: 336건 중 104건(31%) 실패인데
#   그중 몇 건이 회복 가능했는지 알 방법이 없었다.
#   스레드별 플래그로 올린다(호출부 시그니처를 안 건드리려는 것).
_TL = threading.local()


def clear_net_error():
    _TL.net = False


def net_error():
    return getattr(_TL, "net", False)


def _mark_net():
    _TL.net = True


def search(query, display=15):
    try:
        cid, sec = creds()
    except RuntimeError:
        return []                       # 키 없으면 조용히 빈 결과(상위에서 폴백)
    url = "%s?%s" % (ENDPOINT, urllib.parse.urlencode(
        {"query": query, "display": display}))
    req = urllib.request.Request(url, headers={
        "X-Naver-Client-Id": cid, "X-Naver-Client-Secret": sec})
    data = None
    for attempt in range(3):
        try:
            with _SEM:
                with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                    data = json.loads(r.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            if e.code in _RETRY_STATUS and attempt < 2:
                time.sleep(0.4 * (2 ** attempt))
                continue
            # 4xx 는 우리 질의 문제라 재시도해도 같다. 5xx·429 는 저쪽 사정.
            if e.code in _RETRY_STATUS or e.code >= 500:
                _mark_net()
            return []                   # 영구 실패는 기존대로 빈 결과
        except Exception:
            if attempt < 2:
                time.sleep(0.4 * (2 ** attempt))
                continue
            _mark_net()                 # 타임아웃·DNS·TLS·연결 끊김
            return []
    if data is None:
        _mark_net()
        return []
    return [{"title": strip_tags(it.get("title")), "link": it.get("link", "")}
            for it in data.get("items", [])]


def domain_of(source):
    s = (source or "").strip()
    if s in SOURCE_DOMAIN:
        return SOURCE_DOMAIN[s]
    if "." in s and " " not in s:          # source 가 이미 도메인
        return s.lower()
    return None


def resolve(headline, source=None, threshold=0.72):
    """(구글뉴스가 찾아준 헤드라인, 매체) -> 직링크."""
    dom = domain_of(source)
    n = norm(headline)

    # 질의가 너무 길면 결과가 0건이 된다. 주요어만 추린다.
    terms = re.findall(r"[가-힣]{2,}|[A-Za-z][A-Za-z0-9.\-]{1,}|\d+", headline)
    for q in (" ".join(terms[:10]), " ".join(terms[:6])):
        best, sc = None, 0.0
        for it in search(q):
            u = it["link"]
            if not u.startswith("http") or any(b in u for b in BLOCK):
                continue
            r = difflib.SequenceMatcher(None, n, norm(it["title"])).ratio()
            if dom and dom in u:
                r += 0.15                   # 매체 도메인 일치 가산
            if r > sc:
                sc, best = r, it
        if best and sc >= threshold:
            return {"url": best["link"], "via": "webkr",
                    "score": round(min(sc, 1.0), 3), "title": best["title"]}
    return None


if __name__ == "__main__":
    import sys
    print(json.dumps(
        resolve(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None),
        ensure_ascii=False, indent=1))
