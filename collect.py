# -*- coding: utf-8 -*-
"""
collect.py - 매체 RSS 상시 적재기

config/sources.yaml 의 tier1_kr_rss / tier3_global_rss / tier4_vendor_blog 를 읽어
전 피드를 긁고 index/articles.sqlite 에 누적한다.

왜 상시 적재인가:
  RSS 는 최신 N건만 반환하고 N이 매체별로 25~300건으로 편차가 크다.
  전자신문은 depth 30 = 1~2시간치에 불과해, 필요할 때 조회하는 방식으로는
  어제 기사를 영원히 찾을 수 없다. (2026-07-22 실측 해결률 6%)
  주기적으로 긁어 누적해두면 link_resolution 과 dedup 이 모두 해결된다.

사용:
  python collect.py            # 1회 수집
  python collect.py --stats    # 인덱스 현황만 출력
  python collect.py --resolve "제목" --outlet 매체명   # 링크 해결 테스트

Windows 작업 스케줄러에 45분 주기로 등록할 것.
"""
import argparse
import difflib
import gzip
import json
import os
import re
import html as html_mod
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

# 사내 프록시가 TLS 를 MITM 하므로 certifi 번들로는 검증 실패한다.
# OS(Windows) 인증서 저장소를 쓰도록 주입. 이게 없으면 전 요청이 죽는다.
import truststore
truststore.inject_into_ssl()

import yaml

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(BASE, "config", "sources.yaml")
INDEX_DIR = os.path.join(BASE, "index")
DB = os.path.join(INDEX_DIR, "articles.sqlite")
LOGDIR = os.path.join(BASE, "logs")

UA = "Mozilla/5.0 (compatible; TIR-news-crawler/0.1)"
TIMEOUT = 20
RETENTION_DAYS = 14

FEED_SECTIONS = ["tier1_kr_rss", "tier3_global_rss", "tier4_vendor_blog"]

# 추적 파라미터. URL 정규화 시 제거해 같은 기사가 중복 적재되는 것을 막는다.
TRACKING = re.compile(
    r"^(utm_|fbclid|gclid|igshid|spm|ref|referrer|__twitter|at_medium|at_campaign)", re.I
)


# 콘솔/리다이렉트 모두 UTF-8 로 고정. 이걸 안 하면 작업 스케줄러가 남기는
# 로그가 cp949 로 깨져서 사후 분석이 불가능해진다.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_LOGFILE = None


def log(msg):
    global _LOGFILE
    line = "[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg)
    print(line)
    if _LOGFILE is None:
        os.makedirs(LOGDIR, exist_ok=True)
        _LOGFILE = open(
            os.path.join(LOGDIR, "collect-%s.log" % datetime.now().strftime("%Y-%m-%d")),
            "a", encoding="utf-8")
    _LOGFILE.write(line + "\n")
    _LOGFILE.flush()
    return line


# ---------------------------------------------------------------- util
def canonical_url(u):
    """추적 파라미터 제거 + 프래그먼트 제거. dedup 키."""
    try:
        p = urllib.parse.urlsplit(u.strip())
    except ValueError:
        return u.strip()
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
         if not TRACKING.match(k)]
    return urllib.parse.urlunsplit(
        (p.scheme.lower(), p.netloc.lower(), p.path, urllib.parse.urlencode(q), "")
    )


def norm_title(s):
    """제목 정규화: 말머리 대괄호, 기호, 공백 제거. 매칭 키."""
    s = re.sub(r"\[[^\]]{0,20}\]", "", s)
    s = re.sub(r"[\s​]+", "", s)
    s = re.sub(r"[^\w가-힣]", "", s)
    return s.lower()


def parse_date(s):
    """RSS pubDate(RFC822) / Atom(ISO8601) 모두 처리. 실패 시 None."""
    if not s:
        return None
    s = s.strip()
    # [2026-09-08] **날짜만 있는 값은 시각을 붙이지 않는다.**
    #   아래 fromisoformat 가 '2026-09-08' 을 로컬(KST) 자정으로 읽고 UTC 로
    #   바꾸면 2026-09-07T15:00Z 가 되어 **달력 날짜가 하루 당겨진다.**
    #   refine 의 발간일 창은 published[:10] 을 KST 날짜와 비교하므로
    #   그대로 두면 하루씩 어긋난다(창이 2일이라 당장 유실은 없지만 꼬리가 잘림).
    #   디지털데일리처럼 목록에서 날짜만 뽑는 소스가 여기 걸린다.
    if re.fullmatch(r"20\d\d-\d\d-\d\d", s):
        try:
            return datetime.strptime(s, "%Y-%m-%d").replace(
                tzinfo=timezone.utc).isoformat()
        except Exception:
            pass
    try:
        return parsedate_to_datetime(s).astimezone(timezone.utc).isoformat()
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(
            timezone.utc).isoformat()
    except Exception:
        pass
    # [2026-08-27] 벤더 블로그 목록의 영문 날짜('August 25, 2026' / 'Aug 21,
    #   2026'). parse_html_list 가 뽑아 오는 형식이라 여기서 받아야 한다.
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(
                tzinfo=timezone.utc).isoformat()
        except Exception:
            continue
    return None


# ---------------------------------------------------------------- db
def connect():
    os.makedirs(INDEX_DIR, exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS articles(
        url         TEXT PRIMARY KEY,
        outlet      TEXT NOT NULL,
        title       TEXT NOT NULL,
        norm_title  TEXT NOT NULL,
        published   TEXT,
        first_seen  TEXT NOT NULL,
        feed        TEXT
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_outlet_norm ON articles(outlet, norm_title)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_first_seen ON articles(first_seen)")
    c.execute("""CREATE TABLE IF NOT EXISTS feed_state(
        feed          TEXT PRIMARY KEY,
        outlet        TEXT,
        etag          TEXT,
        last_modified TEXT,
        last_run      TEXT,
        last_count    INTEGER,
        last_new      INTEGER,
        last_error    TEXT
    )""")
    c.commit()
    return c


# ---------------------------------------------------------------- fetch
def fetch(url, etag=None, last_mod=None):
    """조건부 GET. 변경 없으면 (None, 304, etag, lm) 반환."""
    h = {"User-Agent": UA, "Accept-Encoding": "gzip"}
    if etag:
        h["If-None-Match"] = etag
    if last_mod:
        h["If-Modified-Since"] = last_mod
    req = urllib.request.Request(url, headers=h)
    # 일부 매체가 간헐적으로 연결을 끊는다(WinError 10054). 1회 재시도.
    last_exc = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                raw = r.read()
                if raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                return raw, r.status, r.headers.get("ETag"), r.headers.get("Last-Modified")
        except urllib.error.HTTPError as e:
            if e.code == 304:
                return None, 304, etag, last_mod
            raise
        except Exception as e:
            last_exc = e
            if attempt == 0:
                time.sleep(2)
    raise last_exc


RE_A = re.compile(r'<a\b[^>]*href="([^"#]+)"[^>]*>(.*?)</a>', re.I | re.S)
RE_TAGS = re.compile(r"<[^>]+>")
# /news/<슬러그> · /blog/<슬러그> · /research/<슬러그> 형태만 기사로 본다.
RE_ART_PATH = re.compile(
    r"^/(?:news|blog|blogs|research|discover|posts?|articles?)/[a-z0-9][a-z0-9\-_/]{3,}$",
    re.I)
RE_ENG_DATE = re.compile(
    r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+"
    r"\d{1,2},?\s+20\d\d)\b")
RE_ISO_DATE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")
RE_HEAD = re.compile(r"<h[1234]\b[^>]*>(.*?)</h[1234]>", re.I | re.S)
# datetime 속성은 대소문자가 제각각이다(Figure AI 는 dateTime=).
RE_TIME = re.compile(r"<time\b([^>]*)>(.*?)</time>", re.I | re.S)
RE_DTATTR = re.compile(r'datetime\s*=\s*"([^"]+)"', re.I)


def parse_html_list(raw, base_url, cap=25):
    """벤더 블로그의 '목록 페이지' HTML -> [(title, link, published_raw)]

    ## 왜 필요한가 (2026-08-27)
    config/sources.yaml 의 tier4_vendor_blog 에는 RSS 가 없는 항목이 섞여 있다
    (Anthropic·Mistral). parse_feed 는 XML 만 읽으므로 그 둘은 **오래전부터
    조용히 0건**이었다. 로그에 'OK 0건 중 신규 0' 으로만 찍혀 아무도 몰랐다.

    2026-08-26 정답지에 Figure AI 의 자사 블로그 글(figure.ai/news/...)이
    실렸는데 우리 후보에는 없었다. 사용자 지시: "주요 빅테크는 자사 기사를
    자사 블로그에 싣기 때문에 정답지는 그렇게 넣을 수 있다. tier1/tier2 소스에
    주요 빅테크 블로그를 넣어 중요한 내용을 포함시켜라."

    ## 날짜가 없으면 소용없다
    refine 은 **발간일 NULL 을 제외**한다(오래된 기사가 first_seen 으로
    되살아나는 것을 막으려는 것). 그래서 목록에서 날짜를 함께 뽑아야 한다.
    실측: Anthropic·Figure AI·xAI·Mistral 네 곳 모두 항목 옆에 영문 날짜를
    싣고 있다('August 25, 2026' / 'Aug 21, 2026').
    날짜를 못 찾은 항목은 **버린다.** 억지로 오늘 날짜를 넣으면 첫 수집 때
    과거 글이 통째로 '오늘 것'이 된다.
    """
    try:
        html = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    except Exception:
        return []
    parts = urllib.parse.urlsplit(base_url)
    root = "%s://%s" % (parts.scheme, parts.netloc)
    out, seen = [], set()
    for m in RE_A.finditer(html):
        href, inner = m.group(1), m.group(2)
        path = href if href.startswith("/") else (
            urllib.parse.urlsplit(href).path if href.startswith(root) else None)
        if not path or not RE_ART_PATH.match(path):
            continue
        url = root + path
        if url in seen:
            continue
        # 제목: 앵커 안의 첫 <h1|h2|h3>. 카드 markup 이 카테고리 태그와
        #   날짜를 같은 앵커에 넣기 때문에 통짜 텍스트를 쓰면 섞인다
        #   (실측: 'Introducing Claude Opus 5 Product Jul 24, 2026 Opus').
        hm = RE_HEAD.search(inner)
        title = RE_TAGS.sub(" ", hm.group(1) if hm else inner)
        title = re.sub(r"\s+", " ", html_mod.unescape(title)).strip()
        if not hm:
            # <h> 가 없으면 텍스트에서 날짜 조각을 걷어낸다.
            title = RE_ENG_DATE.sub(" ", title)
            title = re.sub(r"\s+", " ", title).strip()
        if len(title) < 12:
            continue
        # 날짜: 앵커 안의 <time>. datetime 속성이 있으면 그걸 쓴다
        #   (Figure AI 는 dateTime= 카멜케이스라 대소문자 무시 필요).
        dt = None
        tm = RE_TIME.search(inner)
        if tm:
            am = RE_DTATTR.search(tm.group(1) or "")
            dt = (am.group(1).strip() if am
                  else RE_TAGS.sub(" ", tm.group(2)).strip())
        if not dt:
            dm = RE_ENG_DATE.search(inner) or RE_ISO_DATE.search(inner)
            if dm:
                dt = dm.group(1)
        if not dt:                       # 앵커 밖 카드 영역까지만 본다
            win = html[m.end():m.end() + 300]
            dm = RE_ENG_DATE.search(win) or RE_ISO_DATE.search(win)
            dt = dm.group(1) if dm else None
        if not dt:
            continue
        seen.add(url)
        out.append((title, url, dt))
        if len(out) >= cap:
            break
    return out


RE_NEXTDATA = re.compile(
    r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.I | re.S)
RE_URL_TS = re.compile(r"/(\d{8})\d{6,}")   # /page/view/20260908145916... -> 20260908


def parse_nextdata(raw, base_url, cap=60):
    """Next.js SSR 페이지의 __NEXT_DATA__ JSON -> [(title, link, pubdate)]

    ## 왜 필요한가 (2026-09-08)
    뉴스1 은 RSS 가 없다(브리프 인용 13회로 미확보 매체 중 손실 최대).
    `/rss/allArticle.xml` 은 404 가 아니라 **HTTP 200 으로 홈 HTML 을 돌려줘서**
    parse_feed 가 조용히 0건을 냈다. 그 HTML 에는 <time> 도 <h1~4> 도 없어
    parse_html_list 도 못 읽는다. 대신 __NEXT_DATA__ 안에 title·url·pubdate 가
    그대로 들어 있다.

    ## 구조를 하드코딩하지 않는다
    props.pageProps 아래 배열 이름이 페이지마다 다르다
    (main / latest / briefing / people / insight). 키 이름 대신
    **'title·url·pubdate 를 다 가진 dict'** 를 재귀로 훑는다. 사이트가
    섹션을 늘려도 따라간다.
    **pubdate 가 없는 항목은 버린다** — refine 이 발간일 NULL 을 제외한다
    (parse_html_list 와 같은 이유). 실측: main[] 4건은 pubdate 가 없다.
    """
    try:
        html = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    except Exception:
        return []
    m = RE_NEXTDATA.search(html)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except Exception:
        return []
    parts = urllib.parse.urlsplit(base_url)
    root = "%s://%s" % (parts.scheme, parts.netloc)
    out, seen = [], set()

    def walk(o, depth=0):
        if depth > 8 or len(out) >= cap:
            return
        if isinstance(o, dict):
            t = o.get("title")
            u = o.get("url") or o.get("link")
            d = o.get("pubdate") or o.get("pubDate") or o.get("publishDate")
            if t and u and d and isinstance(t, str) and isinstance(u, str):
                url = u if u.startswith("http") else root + u
                if url not in seen and len(t.strip()) >= 8:
                    seen.add(url)
                    out.append((t.strip(), url, str(d).strip()))
                return
            for v in o.values():
                walk(v, depth + 1)
        elif isinstance(o, list):
            for v in o:
                walk(v, depth + 1)

    walk(data)
    return out


def parse_urldate(raw, base_url, article_re=None, cap=60):
    """목록 HTML -> [(title, link, pubdate)]. **발행일을 URL 에서 뽑는다.**

    ## 왜 필요한가 (2026-09-08)
    디지털데일리도 `/rss/allArticle.xml` 이 HTTP 200 으로 목록 HTML 을 돌려준다.
    그 HTML 에는 <time> 이 하나도 없어 parse_html_list 가 전량 버린다
    (날짜를 못 찾은 항목은 버리는 규칙). 그런데 기사 URL 이
    `/page/view/2026090814591613726` 처럼 **앞 8자리가 발행일**이다.
    한국 언론 CMS 에 흔한 형식이라 뉴스 ID 를 날짜로 쓴다.

    article_re 는 sources.yaml 에서 매체별로 준다. 없으면 아무 앵커나
    잡으므로 반드시 지정할 것.
    """
    try:
        html = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    except Exception:
        return []
    if not article_re:
        return []
    art = re.compile(article_re)
    parts = urllib.parse.urlsplit(base_url)
    root = "%s://%s" % (parts.scheme, parts.netloc)
    out, seen = [], set()
    for m in RE_A.finditer(html):
        href, inner = m.group(1), m.group(2)
        path = href if href.startswith("/") else (
            urllib.parse.urlsplit(href).path if href.startswith(root) else None)
        if not path or not art.match(path):
            continue
        url = root + path
        if url in seen:
            continue
        title = RE_TAGS.sub(" ", inner)
        title = re.sub(r"\s+", " ", html_mod.unescape(title)).strip()
        if len(title) < 12:
            continue
        dm = RE_URL_TS.search(path)
        if not dm:
            continue
        y = dm.group(1)
        seen.add(url)
        out.append((title, url, "%s-%s-%s" % (y[:4], y[4:6], y[6:8])))
        if len(out) >= cap:
            break
    return out


def parse_feed(raw):
    """RSS/Atom -> [(title, link, published_raw)]"""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        # 일부 매체가 인코딩 선언과 실제 바이트가 불일치. 관대하게 재시도.
        try:
            root = ET.fromstring(raw.decode("utf-8", "ignore"))
        except Exception:
            return []
    out = []
    for it in root.iter():
        if it.tag.split("}")[-1] not in ("item", "entry"):
            continue
        t = l = d = None
        for ch in it:
            c = ch.tag.split("}")[-1]
            if c == "title" and ch.text:
                t = ch.text.strip()
            elif c == "link":
                l = (ch.text or "").strip() or ch.attrib.get("href", "")
            elif c in ("pubDate", "published", "updated", "date"):
                d = (ch.text or "").strip()
        if t and l:
            out.append((t, l, d))
    return out


# ---------------------------------------------------------------- collect
def load_feeds():
    """(매체명, URL, opts) 목록. opts 는 parser·article_re 등 파서 힌트."""
    cfg = yaml.safe_load(open(CONFIG, encoding="utf-8"))
    feeds = []
    for sec in FEED_SECTIONS:
        for e in cfg.get(sec) or []:
            if not isinstance(e, dict) or e.get("status") != "ok":
                continue
            opts = {"parser": e.get("parser"), "article_re": e.get("article_re")}
            for u in [e.get("url")] + ([e["alt"]] if e.get("alt") else []):
                if u:
                    feeds.append((e["name"], u, opts))
    return feeds


def collect(conn):
    feeds = load_feeds()
    log("피드 %d개 수집 시작" % len(feeds))
    now = datetime.now(timezone.utc).isoformat()
    tot_new = tot_seen = n_ok = n_304 = n_err = 0

    for outlet, url, opts in feeds:
        row = conn.execute(
            "SELECT etag, last_modified FROM feed_state WHERE feed=?", (url,)
        ).fetchone()
        etag, lm = row if row else (None, None)
        try:
            raw, status, new_etag, new_lm = fetch(url, etag, lm)
        except Exception as e:
            n_err += 1
            conn.execute(
                "INSERT INTO feed_state(feed,outlet,last_run,last_error) VALUES(?,?,?,?) "
                "ON CONFLICT(feed) DO UPDATE SET last_run=?, last_error=?",
                (url, outlet, now, str(e)[:200], now, str(e)[:200]))
            log("  ERR  %-12s %s" % (outlet, str(e)[:60]))
            continue

        if status == 304:
            n_304 += 1
            conn.execute("UPDATE feed_state SET last_run=?, last_error=NULL WHERE feed=?",
                         (now, url))
            continue

        items = parse_feed(raw)
        if not items:
            # [2026-08-27] RSS 가 아니면(벤더 블로그 목록 HTML) 여기로 온다.
            #   Anthropic·Mistral 이 오랫동안 조용히 0건이던 것을 고친다.
            # [2026-09-08] 국내 매체는 마크업이 달라 parse_html_list 가 못 읽는다.
            #   sources.yaml 의 parser 키로 전용 파서를 지정한다.
            p = (opts or {}).get("parser")
            if p == "nextdata":
                items = parse_nextdata(raw, url)
            elif p == "urldate":
                items = parse_urldate(raw, url, (opts or {}).get("article_re"))
            else:
                items = parse_html_list(raw, url)
            if items:
                log("  HTML %-12s 목록에서 %d건 (%s)"
                    % (outlet, len(items), p or "html_list"))
        new = 0
        for title, link, pub in items:
            cu = canonical_url(link)
            if not cu.startswith("http"):
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO articles"
                "(url,outlet,title,norm_title,published,first_seen,feed) "
                "VALUES(?,?,?,?,?,?,?)",
                (cu, outlet, title, norm_title(title), parse_date(pub), now, url))
            new += cur.rowcount
        n_ok += 1
        tot_new += new
        tot_seen += len(items)
        conn.execute(
            "INSERT INTO feed_state(feed,outlet,etag,last_modified,last_run,last_count,"
            "last_new,last_error) VALUES(?,?,?,?,?,?,?,NULL) "
            "ON CONFLICT(feed) DO UPDATE SET etag=?, last_modified=?, last_run=?, "
            "last_count=?, last_new=?, last_error=NULL",
            (url, outlet, new_etag, new_lm, now, len(items), new,
             new_etag, new_lm, now, len(items), new))
        log("  OK   %-12s %3d건 중 신규 %3d" % (outlet, len(items), new))

    conn.commit()
    log("완료: 성공 %d / 미변경 %d / 실패 %d | 조회 %d건, 신규 %d건"
        % (n_ok, n_304, n_err, tot_seen, tot_new))
    return tot_new


def prune(conn):
    cut = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
    n = conn.execute("DELETE FROM articles WHERE first_seen < ?", (cut,)).rowcount
    conn.commit()
    if n:
        log("보존기간(%d일) 초과 %d건 삭제" % (RETENTION_DAYS, n))


# ---------------------------------------------------------------- query
def stats(conn):
    tot = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    log("인덱스 총 %d건" % tot)
    if not tot:
        return
    rng = conn.execute("SELECT MIN(first_seen), MAX(first_seen) FROM articles").fetchone()
    log("  최초적재 %s ~ %s" % (rng[0][:19], rng[1][:19]))
    log("  매체별:")
    for o, n in conn.execute(
            "SELECT outlet, COUNT(*) FROM articles GROUP BY outlet ORDER BY 2 DESC"):
        log("    %-14s %5d" % (o, n))
    err = conn.execute(
        "SELECT outlet, feed, last_error FROM feed_state WHERE last_error IS NOT NULL"
    ).fetchall()
    if err:
        log("  오류 피드 %d개:" % len(err))
        for o, f, e in err:
            log("    %-12s %s" % (o, e[:60]))


def resolve(conn, title, outlet=None, threshold=0.75):
    """제목(+매체)으로 인덱스에서 직링크를 찾는다. link_resolution 경로 1."""
    n = norm_title(title)
    if outlet:
        rows = conn.execute(
            "SELECT title, url, norm_title FROM articles WHERE outlet=?", (outlet,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT title, url, norm_title FROM articles").fetchall()
    best, score = None, 0.0
    for t, u, nt in rows:
        r = difflib.SequenceMatcher(None, n, nt).ratio()
        if r > score:
            score, best = r, (t, u)
    if best and score >= threshold:
        return {"status": "resolved", "score": round(score, 3),
                "title": best[0], "url": best[1]}
    return {"status": "no_match", "score": round(score, 3),
            "closest": best[0] if best else None, "pool": len(rows)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", action="store_true", help="인덱스 현황만 출력")
    ap.add_argument("--resolve", metavar="TITLE", help="제목으로 링크 해결 테스트")
    ap.add_argument("--outlet", help="--resolve 시 매체 지정")
    a = ap.parse_args()

    conn = connect()
    if a.stats:
        stats(conn)
    elif a.resolve:
        r = resolve(conn, a.resolve, a.outlet)
        for k, v in r.items():
            log("  %-8s %s" % (k, v))
    else:
        t0 = time.time()
        collect(conn)
        prune(conn)
        log("소요 %.1f초" % (time.time() - t0))
        stats(conn)
    conn.close()


if __name__ == "__main__":
    main()
