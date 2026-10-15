# -*- coding: utf-8 -*-
"""linkcheck.py - 붙어 있는 링크가 '그 기사'가 맞는지 검사한다.

## 왜 만들었나 (2026-08-26 사용자 지시)
tier0 네 소스가 모두 링크를 갖고 들어오게 됐다(슬랙 마크다운 링크 /
에이닷 / 삼성 슬랙 / NewsClips 슬랙). 링크 해결(`run.sh links`)에
쓰던 시간이 크게 줄어든 대신, **받은 링크가 맞는지 아무도 안 본다**는
구멍이 생겼다. 종전에는 우리가 직접 찾았으니 최소한 사람이 한 번 봤다.

8단계 검증은 '후보 목록 안에 있는 URL 인가'와 '중복인가'만 본다.
URL 이 살아 있는지, 그 URL 이 정말 그 헤드라인의 기사인지는 안 본다.
실측 08-25 bodycache 에 `digitaltoday.co.kr/en/list?view_type=sm` 이
후보 URL 로 들어와 있었다. 기사가 아니라 **섹션 목록 페이지**다.

## 무엇을 보나 - 세 가지 신호
1. **URL 모양** (네트워크 없이 즉시)
   목록·검색·태그·홈 같은 '기사가 아닌 주소'를 걸러낸다. 사람큐레이션은
   출처를 블로그 홈이나 문서 첫 장으로 적어 오는 일이 있다
   (실측 08-26: `skild.ai/blogs`, `dev.meta.ai/docs/overview/`).
2. **HTTP 응답** 404/410 은 죽은 링크다. 403 은 봇 차단이라 **정상 취급**한다
   (기존 samsung 매칭도 같은 규칙이다. 사람이 열면 열린다).
3. **제목 대조** 받아온 페이지의 <title>/og:title 과 헤드라인을 견준다.
   낱말 겹침 비율로 판정한다. 본문을 못 받는 사이트가 많으므로(실측 08-25:
   299건 중 183건이 빈 본문) **못 받았다고 실패로 세지 않는다.** '미확인'이다.

## 판정 (보수적으로)
    OK      : 모양 정상 + (200/403) + 제목 겹침 충분하거나 미확인
    의심    : 제목이 눈에 띄게 다르다. 사람이 봐야 한다
    실패    : 목록/홈 페이지이거나 404. 고쳐야 한다
'의심'은 막지 않고 찍기만 한다. 오탐으로 멀쩡한 기사를 빼는 쪽이 더 비싸다.

## '미확인' 은 한 덩어리가 아니다 (2026-08-27)
08-26 에 tier0 102건 중 90건, 브리프 30건 중 22건이 '미확인' 으로 떴는데
`실패 0` 이라 그대로 발송됐다. 링크가 멀쩡해서가 아니라 **검사를 못 해서**
였다. 그래서 미확인을 둘로 가른다.
  - 페이지를 받아 봤지만 판정 못 함 : 403 봇 차단, 제목 못 읽음 -> 정상
  - 받지도 못함(`unreached`)        : 시간초과·DNS·TLS·연결 끊김 -> 비정상
뒤엣것만 **재시도 대상**이자 **커버리지 계산에서 제외** 대상이다.
못 받은 비율이 --max-unreached(기본 20%)를 넘으면 종료코드 2 로 막는다.

## 어떻게 쓰나
    bash cowork/run.sh linkcheck          # out/brief-<날짜>.md 의 링크
    bash cowork/run.sh linkcheck tier0    # out/tier0-<날짜>.json 의 링크
결과는 out/linkcheck-<날짜>.json 에 남고, 두 번째 실행부터는 캐시를 쓴다.
단 **못 받은 건은 캐시하지 않는다** - 다시 실행하면 그것만 재조회한다.
종료코드: 0 정상 / 1 고쳐야 할 링크 있음 / 2 검사 커버리지 미달
"""
import argparse
import concurrent.futures as futures
import datetime
import gzip
import io
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(BASE, "out")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# [2026-08-27] TIMEOUT 8 / WORKERS 8 에서 TIMEOUT 15 / WORKERS 4 로 낮췄다.
#   08-26 실측: tier0 102건 중 90건, 브리프 30건 중 22건이 '받기 실패(URLError)'
#   로 떴다. 사이트가 막은 게 아니었다. 같은 URL 을 단건으로 받으면 1.5초에
#   200 이 온다. 원인은 **이 샌드박스에서 동시성을 올리면 건당 지연이
#   1.5초 -> 8초로 부푸는 것**이고, TIMEOUT 8 이 하필 그 절벽 위에 있었다.
#     워커 1  / TIMEOUT  8 : OK 16/16, 최장 2.7초
#     워커 8  / TIMEOUT  8 : OK 16/16, 최장 8.1초   <- 절벽 바로 위
#     워커 24 / TIMEOUT  8 : OK  8/48, 최장 8.0초, 실패 40건 전부 URLError
#     워커 8  / TIMEOUT 20 : OK 16/16, 최장 7.3초
#   urlopen 은 소켓 타임아웃도 URLError 로 감싸므로(<urlopen error timed out>)
#   타임아웃과 DNS·TLS 실패가 한 바구니에 담겨 원인이 안 보였다. 아래
#   fetch_title 에서 둘을 갈라 찍는다.
TIMEOUT = 15
WORKERS = 4
# 못 받은 건만 모아 다시 본다. 더 느리게, 더 오래 기다린다.
RETRY_TIMEOUT = 25
RETRY_WORKERS = 2

# 기사가 아닌 주소. 경로 끝이 이것들이면 개별 기사가 아니라 묶음 페이지다.
RE_NOT_ARTICLE = re.compile(
    r"(?:^/?$"                       # 도메인만
    r"|/(?:list|lists|search|tag|tags|category|categories|topics?|"
    r"blogs?|news|press|newsroom|index|home|main|archive|archives)/?$"
    r"|/docs/overview/?$"
    r")", re.I)
# 목록 페이지에서 자주 보이는 질의 파라미터
RE_LIST_QS = re.compile(r"(?:^|&)(?:view_type|page|pageNo|sc_area|sc_word)=", re.I)

RE_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
RE_OG = re.compile(
    r"<meta[^>]+property=[\"']og:title[\"'][^>]+content=[\"'](.*?)[\"']",
    re.I | re.S)
RE_TAG = re.compile(r"<[^>]+>")
RE_WS = re.compile(r"\s+")
# 매체명 꼬리표( - 전자신문, | 연합뉴스 ...)를 떼기 위한 구분자
RE_TAIL = re.compile(r"\s*[\|\-—–:·]\s*[^\|\-—–:·]{2,20}\s*$")

# [2026-09-07 신설] 페이지에서 발행일을 뽑는다.
#   왜 - 09-07 에 '스페이스X, 커서 600억달러 인수'(원문 6/16)가 미러 사이트
#   재게시일(9/6)로 후보에 잡혀 발행일 창을 통과했다. 제목만 대조하던
#   종전 검사로는 잡을 수 없었다. 이제 날짜도 대조한다.
RE_DATES = [
    re.compile(r'<meta[^>]+(?:property|name|itemprop)=["\']'
               r'(?:article:published_time|datePublished|pubdate|'
               r'sailthru\.date|parsely-pub-date)["\'][^>]+content=["\']'
               r'([^"\']+)["\']', re.I),
    re.compile(r'"datePublished"\s*:\s*"([^"]+)"', re.I),
    re.compile(r'<time[^>]+datetime=["\']([^"\']+)["\']', re.I),
]
# 본문에 찍힌 한국식 날짜 (2026. 9. 6. / 2026-09-06 / 2026.09.06)
RE_KDATE = re.compile(r"(20\d\d)[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})")

# 원매체 기사를 그대로 다시 싣는 미러/애그리게이터. 매체명과 도메인이
# 어긋나고 발행일이 재게시일로 찍혀 창 검사를 무력화한다.
MIRROR_HOSTS = (
    "mydailybyte.com", "webull.com", "stocktwits.com", "tradingview.com",
    "kr.tradingview.com", "finance.yahoo.com", "sg.finance.yahoo.com",
    "moomoo.com", "longbridge.com", "simplywall.st", "investing.com",
)

# [2026-09-09 신설] 회사 공식 블로그·뉴스룸.
#   여기는 **발행일 불일치를 '실패'로 보지 않는다.** 게시물을 갱신하면서
#   og:article:published_time 이 최초 게시일로 남거나, 반대로 목록 페이지의
#   다른 날짜를 물어오는 일이 있다. 미러 사이트와 달리 원문 1차 소스이므로
#   창 밖으로 잘라 낼 근거가 되지 못한다. '의심'으로 낮춰 눈으로 보게 한다.
#   실측 09-09: openai.com/index/introducing-chatgpt-images-2-5/ 를
#   '페이지 09-03 vs 후보 09-09, 6일'로 실패 처리해 링크를 비웠고, 그래서
#   '발행일 창 밖'으로 판정해 뺐다. **편집자는 바로 그 URL 을 실었다.**
OFFICIAL_HOSTS = (
    "openai.com", "anthropic.com", "blog.google", "ai.google", "deepmind.google",
    "about.fb.com", "ai.meta.com", "blogs.nvidia.com", "nvidia.com",
    "aws.amazon.com", "blogs.microsoft.com", "microsoft.com", "apple.com",
    "qualcomm.com", "cognition.com", "mistral.ai", "huggingface.co",
    "cisa.gov", "commerce.gov", "whitehouse.gov",
)


def is_official(host):
    h = (host or "").lower()
    if h.startswith("www."):
        h = h[4:]
    return any(h == d or h.endswith("." + d) for d in OFFICIAL_HOSTS)


def pick_pubdate(html):
    """페이지 HTML 에서 발행일(YYYY-MM-DD)을 뽑는다. 못 찾으면 None."""
    for rx in RE_DATES:
        m = rx.search(html or "")
        if not m:
            continue
        v = m.group(1).strip()
        d = re.search(r"(20\d\d)-(\d{2})-(\d{2})", v)
        if d:
            return "%s-%s-%s" % d.groups()
        d = RE_KDATE.search(v)
        if d:
            return "%s-%02d-%02d" % (d.group(1), int(d.group(2)), int(d.group(3)))
    # [주의] 본문 전체를 훑어 날짜를 줍는 폴백은 두지 않는다. 실측 09-07:
    #   v.daum.net 페이지 앞쪽에 무관한 날짜(2026-03-25)가 박혀 있어
    #   멀쩡한 기사 4건이 '발행일 불일치'로 잘못 걸렸다. 메타·JSON-LD·
    #   <time> 만 믿고, 없으면 **날짜 대조를 하지 않는다**(fail-open).
    return None


def date_gap(a, b):
    """두 YYYY-MM-DD 의 일수 차. 하나라도 없으면 None."""
    import datetime as _dt
    try:
        da = _dt.date(*map(int, a.split("-")))
        db = _dt.date(*map(int, b.split("-")))
    except Exception:
        return None
    return abs((da - db).days)


def log(m):
    print("[linkcheck] %s" % m)


def norm(s):
    """비교용 정규화 - 한글/영숫자만 남긴다."""
    s = (s or "").lower()
    s = re.sub(r"&[a-z]+;|&#\d+;", " ", s)
    return re.sub(r"[^0-9a-z가-힣]+", "", s)


def toks(s):
    """비교용 낱말 - 2자 이상 토큰."""
    s = (s or "").lower()
    s = re.sub(r"&[a-z]+;|&#\d+;", " ", s)
    parts = re.split(r"[^0-9a-z가-힣]+", s)
    return {p for p in parts if len(p) >= 2}


def shape_verdict(url):
    """네트워크 없이 URL 모양만 본다. 문제가 있으면 사유 문자열."""
    try:
        u = urllib.parse.urlsplit(url)
    except Exception:
        return "URL 파싱 실패"
    if u.scheme not in ("http", "https"):
        return "스킴 이상(%s)" % u.scheme
    if not u.netloc:
        return "호스트 없음"
    path = u.path or "/"
    if RE_NOT_ARTICLE.search(path):
        return "기사 아님(목록/홈 페이지)"
    if RE_LIST_QS.search(u.query or ""):
        return "기사 아님(목록 질의 파라미터)"
    return None


def fetch_title(url, timeout=None):
    """(status, title, pubdate, err) - 제목과 발행일을 뽑는다.

    err 는 '왜 못 받았나'를 사람 말로 돌려준다. 종전에는 예외 클래스 이름을
    그대로 찍어(URLError) 타임아웃과 DNS·TLS 실패가 구분되지 않았다.
    """
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Language": "ko,en;q=0.8",
        "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as r:
            raw = r.read(400000)
            if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                try:
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                except Exception:
                    pass
            enc = "utf-8"
            ct = r.headers.get("Content-Type") or ""
            m = re.search(r"charset=([\w-]+)", ct, re.I)
            if m:
                enc = m.group(1)
            html = raw.decode(enc, "replace")

            def pick(mo):
                if not mo:
                    return ""
                return RE_WS.sub(" ", RE_TAG.sub(" ", mo.group(1))).strip()

            # [2026-08-27] og:title 을 무조건 우선하지 않는다. 잘려 오는
            #   매체가 있다(실측: v.daum.net 의 og:title 이 "플랫폼 업계, "
            #   에서 끊겨 제목 대조가 0% 로 나와 멀쩡한 기사가 '의심' 이
            #   됐다). 둘 다 뽑아 **긴 쪽**을 쓴다.
            og, ti = pick(RE_OG.search(html)), pick(RE_TITLE.search(html))
            t = og if len(og) >= len(RE_TAIL.sub("", ti)) else ti
            return r.status, t, pick_pubdate(html), None
    except urllib.error.HTTPError as e:
        return e.code, "", None, None
    except Exception as e:
        return 0, "", None, why_failed(e)


def why_failed(e):
    """예외를 '왜 못 받았나'로 바꾼다. 타임아웃을 따로 갈라내는 것이 핵심."""
    if isinstance(e, socket.timeout):
        return "시간초과"
    reason = getattr(e, "reason", None)
    if isinstance(reason, socket.timeout):
        return "시간초과"
    if isinstance(reason, socket.gaierror):
        return "DNS 실패"
    if isinstance(reason, ssl.SSLError):
        return "TLS 오류"
    if isinstance(reason, ConnectionError):
        return "연결 끊김"
    txt = str(reason or e).lower()
    if "timed out" in txt or "timeout" in txt:
        return "시간초과"
    return type(e).__name__


def title_match(headline, page_title):
    """헤드라인과 페이지 제목의 겹침 비율(0~1). 판정 불가면 None."""
    if not page_title:
        return None
    pt = RE_TAIL.sub("", page_title)          # 매체명 꼬리표 제거
    a, b = toks(headline), toks(pt)
    if not a:
        return None
    inter = len(a & b)
    ratio = inter / float(len(a))
    # 낱말이 안 겹쳐도 통째로 들어가 있으면 같은 제목이다(띄어쓰기 차이).
    if ratio < 0.5:
        na, nb = norm(headline), norm(pt)
        if na and nb and (na in nb or nb in na):
            return 1.0
    return ratio


def check_one(item, timeout=None):
    url, headline = item["url"], item.get("headline") or ""
    bad = shape_verdict(url)
    if bad:
        return dict(item, verdict="실패", reason=bad, status=None, ratio=None,
                    page_title="", unreached=False)
    status, ptitle, pdate, err = fetch_title(url, timeout)
    if status in (404, 410):
        return dict(item, verdict="실패", reason="HTTP %d" % status,
                    status=status, ratio=None, page_title="", page_date=None,
                    unreached=False)

    # [2026-09-07] 발행일 대조. 제목보다 먼저 본다 - 제목이 같아도 원문
    #   날짜가 창 밖이면 실을 수 없기 때문이다(미러 사이트 재게시).
    want = item.get("pub_date")
    gap = date_gap(pdate, want) if (pdate and want) else None
    if gap is not None and gap >= 3:
        # [2026-09-09] 회사 공식 블로그·뉴스룸은 '실패'가 아니라 '의심'이다.
        #   위 OFFICIAL_HOSTS 주석의 09-09 실측 사고 참조.
        if is_official(urllib.parse.urlsplit(url).hostname):
            return dict(item, verdict="의심",
                        reason="공식 뉴스룸 발행일 차이(페이지 %s vs 후보 %s, %d일) "
                               "- 링크는 유효, 눈으로 확인" % (pdate, want, gap),
                        status=status, ratio=None, page_title=ptitle,
                        page_date=pdate, unreached=False)
        return dict(item, verdict="실패",
                    reason="발행일 불일치(페이지 %s vs 후보 %s, %d일)"
                           % (pdate, want, gap),
                    status=status, ratio=None, page_title=ptitle,
                    page_date=pdate, unreached=False)

    ratio = title_match(headline, ptitle)
    if ratio is None:
        # unreached: '검사를 못 했다'. 403 봇 차단이나 '제목 못 읽음' 과는
        # 다르다. 저 둘은 페이지를 받아 봤고 이쪽은 받지도 못했다.
        # 이 값이 뒤의 재시도 대상과 검사 커버리지 판정을 가른다.
        # [2026-09-07] 429 는 403 과 다르다. 봇 차단이 아니라 '지금은
        #   많다'는 뜻이라 재시도하면 읽힌다. 정상 취급하지 않는다.
        why, unreached = "제목 못 읽음", False
        if status == 403:
            why = "403 봇 차단(정상 취급)"
        elif status == 429:
            why, unreached = "429 요청제한(재시도 대상)", True
        elif status == 0:
            why, unreached = "받기 실패(%s)" % (err or "?"), True
        return dict(item, verdict="미확인", reason=why, status=status,
                    ratio=None, page_title=ptitle, page_date=pdate,
                    unreached=unreached)
    if ratio >= 0.45:
        v, why = "OK", "제목 일치 %.0f%%" % (ratio * 100)
    elif ratio >= 0.2:
        v, why = "의심", "제목 일부만 일치 %.0f%%" % (ratio * 100)
    else:
        v, why = "의심", "제목 다름 %.0f%%" % (ratio * 100)
    # 미러/애그리게이터 도메인은 제목이 맞아도 눈으로 보게 한다.
    host = (urllib.parse.urlsplit(url).netloc or "").lower().lstrip("www.")
    if v == "OK" and any(host.endswith(h) for h in MIRROR_HOSTS):
        v, why = "의심", "미러·재게시 도메인(%s) - 원매체 링크 권장" % host
    return dict(item, verdict=v, reason=why, status=status, ratio=ratio,
                page_title=ptitle, page_date=pdate, unreached=False)


RE_MD = re.compile(r"^\*\*(\d+)\\?\.\s*(.+?)\*\*.*?\]\((https?://[^)]+)\)",
                   re.M)


def brief_pubdates(day):
    """URL -> 후보 목록의 발행일. 발행일 대조에 쓴다. [2026-09-07 신설]

    브리프 마크다운에는 발행일이 없어서 종전에는 대조할 값 자체가 없었다.
    prompt 의 후보 JSONL 에서 끌어오고, portalize 로 네이버 정적링크로
    바뀐 건은 매핑을 뒤집어 원래 URL 로도 찾을 수 있게 한다.
    """
    m = {}
    pp = os.path.join(OUTDIR, "prompt-%s.txt" % day)
    if os.path.exists(pp):
        for ln in open(pp, encoding="utf-8"):
            if not ln.startswith('{"idx"'):
                continue
            try:
                o = json.loads(ln)
            except Exception:
                continue
            if o.get("url") and o.get("pub_date"):
                m[o["url"]] = o["pub_date"]
    zp = os.path.join(OUTDIR, "portalize-%s.json" % day)
    if os.path.exists(zp):
        try:
            mapping = json.load(open(zp, encoding="utf-8")).get("mapping", {})
        except Exception:
            mapping = {}
        for orig, portal in mapping.items():
            if orig in m and portal not in m:
                m[portal] = m[orig]
    return m


def from_brief(day):
    p = os.path.join(OUTDIR, "brief-%s.md" % day)
    if not os.path.exists(p):
        log("[실패] %s 없음" % p)
        return None
    text = open(p, encoding="utf-8").read()
    dates = brief_pubdates(day)
    out, seen = [], set()
    for m in RE_MD.finditer(text):
        headline, url = m.group(2).strip(), m.group(3).strip()
        # 헤드라인 뒤에 붙는 (매체, [원문보기]...) 를 떼어 낸다.
        headline = re.sub(r"\s*\((?:[^()]*)\)\s*$", "", headline)
        if url in seen:
            continue
        seen.add(url)
        out.append({"headline": headline, "url": url,
                    "pub_date": dates.get(url)})
    return out


def from_tier0(day):
    p = os.path.join(OUTDIR, "tier0-%s.json" % day)
    if not os.path.exists(p):
        log("[실패] %s 없음" % p)
        return None
    data = json.load(open(p, encoding="utf-8"))
    out, seen = [], set()
    for x in data.get("items", []):
        u = x.get("url")
        if not u or u in seen:
            continue
        seen.add(u)
        out.append({"headline": x.get("title") or "", "url": u,
                    "src": x.get("src"), "pub_date": x.get("pub_date")})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--source", default="brief", choices=("brief", "tier0"))
    ap.add_argument("--deadline", type=float, default=150.0)
    ap.add_argument("--no-cache", action="store_true")
    # [2026-08-27] '못 받은 것'이 몇 %를 넘으면 검사가 수행되지 않은 것으로
    #   보고 종료코드 1 을 준다. 종전에는 전부 '미확인' 으로 조용히 통과해서,
    #   08-26 에 브리프 30건 중 22건을 검사하지 못한 채 발송했다.
    ap.add_argument("--max-unreached", type=float, default=0.20,
                    help="못 받은 비율이 이 값을 넘으면 검사 미수행으로 본다")
    # [2026-08-26] tier0 검사에서 '실패'로 판정된 링크는 비워 둔다.
    #   404/410 이거나 기사가 아닌 주소는 그대로 두면 그 잘못된 URL 로
    #   브리프에 실린다. 비워 두면 뒤따르는 `run.sh links` 가 정상 경로로
    #   다시 찾는다. '의심'은 건드리지 않는다 - 제목 대조는 어림값이라
    #   멀쩡한 기사를 지우는 쪽이 더 비싸다.
    ap.add_argument("--fix", action="store_true",
                    help="tier0 에서 '실패' 판정 URL 을 비운다")
    a = ap.parse_args()

    day = datetime.date.fromisoformat(a.date).isoformat()
    items = from_brief(day) if a.source == "brief" else from_tier0(day)
    if items is None:
        return 1
    if not items:
        log("[경고] 검사할 링크가 0건입니다. 파서를 의심하세요")
        return 1

    cp = os.path.join(OUTDIR, "linkcheck-%s.json" % day)
    cache = {}
    if os.path.exists(cp) and not a.no_cache:
        try:
            cache = {r["url"]: r for r in json.load(open(cp, encoding="utf-8"))}
        except Exception:
            cache = {}
    # [2026-08-27] 못 받은 결과는 캐시에서 되살리지 않는다.
    #   종전에는 '받기 실패' 도 그대로 캐시돼, 다시 실행해도 같은 실패를
    #   재사용하느라 영영 검사되지 않았다. 캐시는 판정이 난 것만 쓴다.
    cache = {u: r for u, r in cache.items() if not r.get("unreached")}

    todo = [x for x in items if x["url"] not in cache]
    log("%s %d건 (캐시 %d건 재사용, 조회 %d건)"
        % (a.source, len(items), len(items) - len(todo), len(todo)))

    def run_pass(batch, workers, timeout, budget):
        """한 회차. 마감을 넘기면 남은 것을 취소하고 받은 것만 돌려준다."""
        out, t = [], time.time()
        with futures.ThreadPoolExecutor(max_workers=workers) as ex:
            fs = {ex.submit(check_one, x, timeout): x for x in batch}
            try:
                for f in futures.as_completed(fs, timeout=budget + 10):
                    try:
                        out.append(f.result())
                    except Exception as e:
                        out.append(dict(fs[f], verdict="미확인", status=None,
                                        ratio=None, page_title="",
                                        unreached=True,
                                        reason="검사 예외(%s)"
                                               % type(e).__name__))
                    if time.time() - t > budget:
                        for g in fs:
                            g.cancel()
                        break
            except futures.TimeoutError:
                for g in fs:
                    g.cancel()
        return out

    done, t0 = [], time.time()
    left = lambda: a.deadline - (time.time() - t0)
    if todo:
        done = run_pass(todo, WORKERS, TIMEOUT, a.deadline * 0.6)

        # 1) 마감에 걸려 아예 못 본 것부터 마저 본다. 종전에는 이걸
        #    '시간초과 미검사' 로 남겨 두고 남은 예산을 그냥 버렸다.
        seen = {r["url"] for r in done}
        untried = [x for x in todo if x["url"] not in seen]
        if untried and left() > 15:
            log("마감에 걸린 %d건 이어서 검사" % len(untried))
            done += run_pass(untried, WORKERS, TIMEOUT, left() * 0.5)

        # 2) 그다음 '받지도 못한' 것만 모아 다시 본다. 워커를 줄이고 더
        #    오래 기다린다. 08-26 실측상 이 실패는 사이트가 아니라
        #    동시성에서 왔으므로 느리게 다시 부르면 대부분 살아난다.
        again = [x for x in done if x.get("unreached")]
        if again:
            if left() > 15:
                log("못 받은 %d건 재시도 (워커 %d, 타임아웃 %d초)"
                    % (len(again), RETRY_WORKERS, RETRY_TIMEOUT))
                redo = run_pass(again, RETRY_WORKERS, RETRY_TIMEOUT, left() - 5)
                fixed = {r["url"]: r for r in redo}
                done = [fixed.get(r["url"], r) for r in done]
                n_ok = sum(1 for r in redo if not r.get("unreached"))
                log("재시도 결과: %d/%d 건 회수" % (n_ok, len(again)))
            else:
                log("[경고] 재시도할 시간이 없습니다(남은 %.0f초)" % left())

    res = [cache[x["url"]] for x in items if x["url"] in cache] + done
    by = {r["url"]: r for r in res}
    ordered = [by[x["url"]] for x in items if x["url"] in by]
    json.dump(ordered, open(cp, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    n = {"OK": 0, "의심": 0, "실패": 0, "미확인": 0}
    for r in ordered:
        n[r["verdict"]] = n.get(r["verdict"], 0) + 1
    for r in ordered:
        if r["verdict"] == "OK":
            continue
        log("  [%s] %s" % (r["verdict"], (r["headline"] or "")[:44]))
        log("        %s | %s" % (r["reason"], r["url"][:88]))
        if r.get("page_title") and r["verdict"] == "의심":
            log("        페이지 제목: %s" % r["page_title"][:74])
        if r.get("page_date") and r["verdict"] in ("의심", "실패"):
            log("        페이지 발행일: %s" % r["page_date"])
    skipped = len(items) - len(ordered)
    unreached = sum(1 for r in ordered if r.get("unreached"))
    log("결과 OK %d / 의심 %d / 실패 %d / 미확인 %d%s (%.0f초)"
        % (n["OK"], n["의심"], n["실패"], n["미확인"],
           (" / 시간초과 미검사 %d" % skipped) if skipped else "",
           time.time() - t0))
    if skipped:
        log("시간초과분이 남았습니다 - 다시 실행하면 캐시를 쓰고 나머지만 봅니다")

    # [2026-08-27] 검사 커버리지를 따로 찍는다. '미확인' 안에는 성격이
    #   다른 둘이 섞여 있다. 403 봇 차단은 페이지를 받아 본 것이고,
    #   '받기 실패' 는 받지도 못한 것이다. 뒤엣것만 커버리지에서 뺀다.
    checked = len(items) - unreached - skipped
    cover = checked / float(len(items)) if items else 0.0
    log("검사 커버리지 %d/%d (%.0f%%) | 못 받음 %d건"
        % (checked, len(items), cover * 100, unreached))
    if unreached:
        why = {}
        for r in ordered:
            if r.get("unreached"):
                why[r["reason"]] = why.get(r["reason"], 0) + 1
        log("  못 받은 사유: %s"
            % ", ".join("%s %d" % (k, v) for k, v in sorted(
                why.items(), key=lambda kv: -kv[1])))
    if a.fix and a.source == "tier0" and n["실패"]:
        bad = {r["url"] for r in ordered if r["verdict"] == "실패"}
        p = os.path.join(OUTDIR, "tier0-%s.json" % day)
        data = json.load(open(p, encoding="utf-8"))
        cleared = 0
        for x in data.get("items", []):
            if x.get("url") in bad:
                x["url"] = None
                x["url_via"] = None
                x["confidence"] = "unresolved"
                cleared += 1
        json.dump(data, open(p, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        log("실패 링크 %d건을 비웠습니다 - 뒤의 'run.sh links' 가 다시 찾습니다"
            % cleared)
        n["실패"] = 0          # 비웠으니 더 막지 않는다. 커버리지는 계속 본다.

    if n["실패"]:
        log("[실패] 고쳐야 할 링크가 %d건 있습니다" % n["실패"])
        return 1

    # [2026-09-07 신설] 발송본에서 '검증되지 않은 링크'는 통과시키지 않는다.
    #   403 봇 차단은 페이지를 받아 본 것이라 종전대로 정상 취급하지만,
    #   429·받기 실패·제목 못 읽음은 **제목도 발행일도 대조하지 못한 것**
    #   이다. 09-07 에 '스페이스X, 커서 600억달러 인수'가 429 로 미확인인
    #   채 발송됐고, 원문은 6월 16일 기사라 발행일 창 밖이었다.
    if a.source == "brief":
        unver = [r for r in res
                 if r["verdict"] == "미확인" and r.get("status") != 403]
        if unver:
            log("[실패] 검증 못 한 링크 %d건 - 발송본에서 빼거나 다른 "
                "링크로 바꾸세요" % len(unver))
            for r in unver[:10]:
                log("   %s | %s" % (r.get("reason"), r["url"][:80]))
            return 1

    # 못 받은 비율이 임계를 넘으면 '검사가 수행되지 않았다'로 본다.
    # 링크 검사는 브리프가 나가기 전 마지막 관문이라, 조용히 통과시키면
    # 안 된다(08-26 사고: 30건 중 22건 미검사 상태로 발송).
    if items and (unreached + skipped) / float(len(items)) > a.max_unreached:
        log("[실패] 검사 커버리지 %.0f%% - 임계 %.0f%% 미달입니다."
            % (cover * 100, (1 - a.max_unreached) * 100))
        log("        링크가 나쁜 게 아니라 **검사를 못 한 것**입니다.")
        log("        다시 실행하면 못 받은 건만 재조회합니다"
            "(판정 난 건은 캐시를 씁니다).")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
