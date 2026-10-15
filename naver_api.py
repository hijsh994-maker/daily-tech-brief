# -*- coding: utf-8 -*-
"""
naver_api.py - link_resolution 경로 2 (네이버 뉴스 검색 API)

경로 1(로컬 인덱스)이 실패하는 경우를 받는다:
  - RSS 자체가 없는 매체: 뉴스1 / 파이낸셜뉴스 / 세계일보 / 쿠키뉴스 등
    (2026-07-22 실측 no_feed 36%)
  - 인덱스 가동 이전에 발행된 과거 기사

인증정보는 코드/설정파일에 넣지 말고 환경변수로 둔다:
    NAVER_CLIENT_ID
    NAVER_CLIENT_SECRET
발급: https://developers.naver.com/apps/#/register  (검색 API 선택)
무료 한도 25,000 콜/일 — 본 파이프라인 사용량(하루 수백) 대비 충분.

주의: 네이버 뉴스 검색은 news.naver.com 제휴 매체만 색인한다.
      비제휴 매체(해외지, 일부 전문지)는 여기서도 안 나온다 -> 경로 3으로.
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

ENDPOINT = "https://openapi.naver.com/v1/search/news.json"
TIMEOUT = 15

# 매체명 -> 원문 도메인. originallink 가 이 도메인이어야 같은 매체로 인정한다.
# (같은 제목의 기사를 다른 매체가 받아쓰는 경우가 흔해서 도메인 검증이 필요)
OUTLET_DOMAIN = {
    "뉴스1": "news1.kr",
    "파이낸셜뉴스": "fnnews.com",
    "세계일보": "segye.com",
    "쿠키뉴스": "kukinews.com",
    "디지털데일리": "ddaily.co.kr",
    "디지털타임스": "dt.co.kr",
    "IT조선": "it.chosun.com",
    "아주경제": "ajunews.com",
    "뉴스핌": "newspim.com",
    "한겨레": "hani.co.kr",
    "연합뉴스": "yna.co.kr",
    "서울신문": "seoul.co.kr",
    "전자신문": "etnews.com",
    "서울경제": "sedaily.com",
    "헤럴드경제": "heraldcorp.com",
    "한국경제": "hankyung.com",
    "아시아경제": "asiae.co.kr",
    "이투데이": "etoday.co.kr",
    "이데일리": "edaily.co.kr",
    "매일경제": "mk.co.kr",
    "조선비즈": "chosun.com",
    "동아일보": "donga.com",
    "경향신문": "khan.co.kr",
    "머니투데이": "mt.co.kr",
    "지디넷코리아": "zdnet.co.kr",
    "AI타임스": "aitimes.com",
    "아이뉴스24": "inews24.com",
    "뉴시스": "newsis.com",
    "디지털투데이": "digitaltoday.co.kr",
    # [2026-08-10 추가] 미확보 국문 76건의 71%가 미등록 매체였다.
    #   미등록이면 도메인 검증을 못 해 임계값이 0.55 -> 0.75 로 올라간다.
    #   특히 조선일보·중앙일보가 빠져 있었다(조선비즈만 등록돼 있었음).
    "조선일보": "chosun.com",
    "중앙일보": "joongang.co.kr",
    "국민일보": "kmib.co.kr",
    "한국일보": "hankookilbo.com",
    "파이낸셜투데이": "ftoday.co.kr",
    "블로터": "bloter.net",
    "비즈니스포스트": "businesspost.co.kr",
    "글로벌이코노믹": "g-enews.com",
    "더구루": "theguru.co.kr",
    "위키트리": "wikitree.co.kr",
    "로봇신문": "irobotnews.com",
    "에너지경제신문": "ekn.kr",
    "AI포스트": "aipostkorea.com",
    "테크42": "tech42.co.kr",
    "인공지능신문": "aitimes.kr",
}

# 원문 매체가 아니라 포털·수집 사이트. 도메인 검증을 걸면 안 된다.
#   (news.nate.com 은 어느 매체 기사든 다 실리므로 도메인이 무의미하고,
#    검증을 켜면 정답 후보가 전부 걸러져 유사도 0.000 이 나온다)
AGGREGATOR = {"네이트", "다음", "네이버", "구글뉴스", "Investing.com 한국어",
              "Investing.com", "flipboard", "Yahoo", "MSN"}


class NotConfigured(RuntimeError):
    pass


def creds():
    cid = os.environ.get("NAVER_CLIENT_ID")
    sec = os.environ.get("NAVER_CLIENT_SECRET")
    if not cid or not sec:
        raise NotConfigured(
            "NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수가 없습니다. "
            "PowerShell: [Environment]::SetEnvironmentVariable('NAVER_CLIENT_ID','...','User')")
    return cid, sec


def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def norm_title(s):
    s = s or ""
    # 유사도 비교 전에도 꼬리를 떼야 한다. 안 그러면 검색은 맞게 했는데
    # 비교 단계에서 '- 머니투데이' 만큼 점수가 깎여 임계값에 걸린다.
    s = re.sub(r"\s+[-–—]\s+[^\s\-–—]{2,20}$", "", s)
    s = re.sub(r"\[[^\]]{0,20}\]", "", s)
    s = re.sub(r"[\s​]+", "", s)
    s = re.sub(r"[^\w가-힣]", "", s)
    return s.lower()


# 네이버 검색 API 동시 호출 제한.
#
# [왜 필요한가 - 2026-07-31]
#   refine 의 링크 해결을 병렬화(8스레드)했더니 순차 대비 링크가 6~10건 사라졌다.
#   예외 로그는 0건이었다. 원인은 이 함수가 아니라 호출자들이 실패를 조용히
#   흡수하는 구조였다 - resolve() 는 예외를 {"status":"error"} 로 바꿔 반환하고
#   websearch.search() 는 [] 를 반환한다. 그래서 '순간적인 거절'과 '그런 기사
#   없음'이 구분되지 않고, 멀쩡한 링크가 영구히 버려졌다.
#
#   근본 처방은 두 가지다.
#     (1) 이 지점에서 동시 호출 수를 묶는다 (네이버 오픈API 는 초당 호출 제한이 있다)
#     (2) 일시적 실패는 재시도한다
#   호출자를 고치는 것보다 여기가 낫다. 모든 경로가 결국 이 함수를 지나기 때문이다.
_SEM = threading.Semaphore(3)
_RETRY_STATUS = (429, 500, 502, 503, 504)

# [2026-08-27] websearch.py 와 같은 목적. '네이버에 그 기사가 없다' 와
#   '네트워크가 실패해 못 봤다' 를 상위에서 구분하게 해준다. search() 는
#   예외를 올리지만 resolve() 가 {"status":"error"} 로 삼키고, 그걸 다시
#   samsung_resolve.resolve_link 이 None 으로 뭉개서 끝내 사라졌다.
_TL = threading.local()


def clear_net_error():
    _TL.net = False


def net_error():
    return getattr(_TL, "net", False)


def _mark_net():
    _TL.net = True


def search(query, display=20, sort="sim"):
    """네이버 뉴스 검색. -> [{title, originallink, link, pubDate}]

    동시 호출은 3개로 제한하고, 일시적 실패(429/5xx/네트워크)는 백오프 후
    2회까지 재시도한다. 영구 실패(400/401 등)는 그대로 예외로 올린다.
    """
    cid, sec = creds()
    url = "%s?%s" % (ENDPOINT, urllib.parse.urlencode(
        {"query": query, "display": display, "sort": sort}))
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
            if e.code in _RETRY_STATUS or e.code >= 500:
                _mark_net()
            raise
        except Exception:
            if attempt < 2:
                time.sleep(0.4 * (2 ** attempt))
                continue
            _mark_net()                 # 타임아웃·DNS·TLS·연결 끊김
            raise
    out = []
    for it in data.get("items", []):
        out.append({"title": strip_tags(it.get("title")),
                    "originallink": it.get("originallink") or "",
                    "link": it.get("link") or "",
                    "pubDate": it.get("pubDate")})
    return out


def sanitize_query(t):
    """검색용 질의 정제.

    스마트 따옴표(“”‘’)가 들어가면 네이버 검색이 0건을 반환한다.
    (실측: '“AI 거점 도약”... 포항에 ‘데이터센터’ 첫 삽' -> 0건,
           정제 후 -> 정확한 세계일보 기사 반환)
    """
    t = re.sub(r"[“”‘’\"'`]", " ", t)
    t = re.sub(r"[…]+|\.{2,}", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def strip_title_noise(title):
    """수집 과정에서 제목에 붙은 군더더기를 떼어낸다.

    [2026-08-10] 미확보 국문 76건 중 9건이 '제목이 제목이 아닌' 경우였다.
      - '…시드투자 유치 - 머니투데이'  : 구글뉴스가 붙이는 ' - 매체명' 꼬리
      - '지난달 21일 …열리고 있다. ⓒ박종진기자' : 사진 캡션이 제목으로 들어옴
    꼬리를 단 채로 검색하면 유사도가 깎여 임계값을 못 넘는다.
    """
    t = (title or "").strip()
    # 끝의 ' - 매체명' 꼬리 (매체명에 공백이 없고 20자 이내일 때만)
    t = re.sub(r"\s+[-–—]\s+[^\s\-–—]{2,20}$", "", t)
    # 사진 캡션: 촬영자 표기 이후를 버린다
    t = re.split(r"\s*ⓒ", t)[0].strip()
    return t or (title or "")


def query_variants(title):
    """전체 제목 -> 정제 -> 앞부분만. 순서대로 시도한다."""
    title = strip_title_noise(title)
    v = [title, sanitize_query(title)]
    head = sanitize_query(re.sub(r"[\[\(].*?[\]\)]", "", title.split("...")[0].split("…")[0]))
    if len(head) >= 8:
        v.append(head)
        # [2026-08-10] 꼬리 어절을 하나씩 떼며 좁힌 질의도 시도한다.
        #   지면 스크랩 제목은 온라인 제목과 표현이 달라, 어절 하나가
        #   검색 결과를 통째로 비운다. 실측: '네이버, AI 팩토리 운영사 설립
        #   시동'은 0건인데 '시동'을 떼면 정답이 1위로 나온다.
        #   질의를 넓혀도 채택은 유사도 임계값이 막으므로 오링크 위험은 없다.
        w = head.replace(",", " ").split()
        for cut in (1, 2):
            if len(w) - cut >= 3:
                v.append(" ".join(w[:len(w) - cut]))
    seen, out = set(), []
    for q in v:
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


def _pubdate(s):
    """네이버 pubDate(RFC822) -> date. 실패 시 None."""
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(s).date()
    except Exception:
        return None


def resolve(title, outlet=None, threshold=0.75, domain_threshold=0.55,
            date_lo=None, date_hi=None):
    """제목(+매체)으로 원문 직링크를 찾는다.

    originallink 를 쓴다. link 는 news.naver.com 주소라 브리프용으로 부적합.

    date_lo/date_hi 를 주면 그 발행일 창(전일~당일) 밖 기사는 후보에서 배제한다.
    (과거 기사 오링크 방지)

    임계값을 둘로 나눈다:
      - 발행사 도메인이 일치하면 0.55 로 완화.
        에이닷 뉴스레터가 제목을 자주 고쳐 쓰기 때문이다. 실측 예:
          로그  '나스닥 1.3%↑, 3거래일 만에 반등...반도체주 AI 랠리 주도[뉴욕마감]'
          실제  '나스닥 1.3% 상승…마이크론 12% 급등, 반도체주 AI 랠리 주도[뉴욕마감]'
        같은 매체 + 같은 시기 + 절반 이상 일치면 동일 기사로 본다.
      - 도메인 미확인이면 0.75 유지(오탐 방지).
    """
    # 포털·수집 사이트는 원문 도메인이 제각각이라 검증 대상이 아니다.
    want_domain = None
    if outlet and outlet not in AGGREGATOR:
        want_domain = OUTLET_DOMAIN.get(outlet)
    n = norm_title(title)
    # best_dom : 매체 도메인이 일치하는 최고 후보(임계 domain_threshold)
    # best_any : 도메인 무관 최고 후보(임계 threshold)
    # score/best 는 no_match 보고용 'closest' 추적에만 쓴다.
    best_dom = best_any = None
    sc_dom = sc_any = 0.0
    best, score, tried, cands = None, 0.0, [], 0

    for q in query_variants(title):
        tried.append(q)
        # [2026-08-21 FIX-A] 부제를 뗀 축약 질의로 찾은 기사는 **그 축약형으로도**
        #   유사도를 재고 큰 쪽을 쓴다. 종전에는 항상 원 제목 전체로만 쟀다.
        #   실측: 후보 '오픈AI, 청소년 전용 챗GPT 출시…나이 예측으로 미성년 자동
        #   분류' vs 실제 기사 '오픈AI, 청소년 전용 챗GPT 출시' -> 0.698 로
        #   임계 0.75 미달. 앞부분이 100% 일치하는데 부제 유무만으로 떨어졌다.
        #   축약형이 원 제목의 **앞부분이고 절반 이상 길이**일 때만 허용해
        #   '오픈AI 청소년' 같은 지나친 축약으로 오탐하는 것을 막는다.
        nq = norm_title(q)
        use_variant = (len(nq) >= 8 and len(nq) >= 0.5 * len(n)
                       and n.startswith(nq) and nq != n)
        try:
            items = search(q, display=20)
        except NotConfigured:
            return {"status": "not_configured"}
        except Exception as e:
            return {"status": "error", "detail": str(e)[:120]}
        cands += len(items)
        for it in items:
            link = it["originallink"] or ""
            if not link.startswith("http"):
                continue
            if date_lo and date_hi:
                pd = _pubdate(it.get("pubDate"))
                if pd and not (date_lo <= pd <= date_hi):
                    continue
            nt = norm_title(it["title"])
            r = difflib.SequenceMatcher(None, n, nt).ratio()
            if use_variant:
                r = max(r, difflib.SequenceMatcher(None, nq, nt).ratio())
            if r > score:
                score, best = r, it
            hit = bool(want_domain) and want_domain in link
            # [2026-08-21 FIX-B] 도메인 불일치 후보를 **버리지 않는다**.
            #   종전에는 outlet 힌트가 있으면 다른 매체 기사를 전부 continue 로
            #   날려, 그 매체가 네이버 뉴스 API 에 없으면 후보 0건이 됐다
            #   (실측: 지디넷 판이 없어 candidates=60 인데 전량 배제, score 0.0).
            #   주석의 원래 의도대로 '임계값을 둘로 나누는' 방식으로 바꾼다.
            #   **도메인 일치 후보를 항상 우선**하므로, 기존에 풀리던 건의
            #   결과는 바뀌지 않는다. 도메인 후보가 없을 때만 타 매체를 쓴다.
            if hit:
                if r > sc_dom:
                    sc_dom, best_dom = r, it
            elif r > sc_any:
                sc_any, best_any = r, it
        if best_dom is not None and sc_dom >= domain_threshold:
            return {"status": "resolved", "via": "naver", "score": round(sc_dom, 3),
                    "title": best_dom["title"], "url": best_dom["originallink"],
                    "query": q, "domain_verified": True}
        if best_any is not None and sc_any >= threshold:
            return {"status": "resolved", "via": "naver", "score": round(sc_any, 3),
                    "title": best_any["title"], "url": best_any["originallink"],
                    "query": q, "domain_verified": False}

    return {"status": "no_match", "via": "naver", "score": round(score, 3),
            "closest": best["title"] if best else None,
            "candidates": cands, "queries": tried,
            "domain_filtered": bool(want_domain)}


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python naver_api.py \"제목\" [매체명]")
        sys.exit(0)
    r = resolve(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    print(json.dumps(r, ensure_ascii=False, indent=1))
