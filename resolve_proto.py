# -*- coding: utf-8 -*-
"""
link_resolution 프로토타입.

목적: tier0 트렌드 로그(에이닷비즈뉴스레터)의 '제목 + 매체 + 날짜'만 있는
      항목에서 원문 직링크를 복원할 수 있는지, 성공률이 몇 %인지 측정한다.

경로 1(본 스크립트 범위): 매체 RSS 제목 매칭
경로 2 이하(네이버 API / 사이트검색)는 성공률 확인 후 결정.
"""
import re, sys, json, gzip, io, difflib, urllib.request, urllib.error
import xml.etree.ElementTree as ET

# 사내 프록시가 TLS 를 MITM 하므로 certifi 번들로는 검증 실패한다.
# OS(Windows) 인증서 저장소를 쓰도록 주입. (pip install truststore)
import truststore
truststore.inject_into_ssl()

UA = "Mozilla/5.0 (compatible; TIR-news-crawler/0.1)"

# 콘솔이 cp949 라 한글 출력이 깨진다. 파일로 남기고 콘솔은 보조로만 쓴다.
LOG = []
def say(s):
    LOG.append(s)
    try:
        print(s)
    except UnicodeEncodeError:
        print(s.encode("cp949", "replace").decode("cp949"))

# 2026-07-22 실측 검증 완료된 피드만 등록
FEEDS = {
    "전자신문":     ["https://rss.etnews.com/Section901.xml"],
    "서울경제":     ["https://www.sedaily.com/rss/newsall", "https://www.sedaily.com/rss/it"],
    "헤럴드경제":   ["https://biz.heraldcorp.com/rss/google/newsAll"],
    "한국경제":     ["https://www.hankyung.com/feed/it", "https://www.hankyung.com/feed/economy"],
    "아시아경제":   ["https://www.asiae.co.kr/rss/stock.htm"],
    "지디넷코리아": ["https://feeds.feedburner.com/zdkorea"],
    "이데일리":     ["https://rss.edaily.co.kr/edaily_news.xml"],
    "서울신문":     ["https://www.seoul.co.kr/xml/rss/rss_economy.xml"],
    "경향신문":     ["https://www.khan.co.kr/rss/rssdata/total_news.xml"],
    "동아일보":     ["https://rss.donga.com/total.xml"],
    "뉴시스":       ["https://newsis.com/RSS/health.xml"],
    "이투데이":     ["https://rss.etoday.co.kr/eto/etoday_news_all.xml"],
    "조선비즈":     ["https://biz.chosun.com/arc/outboundfeeds/rss/?outputType=xml"],
    "조선일보":     ["https://www.chosun.com/arc/outboundfeeds/rss/category/economy/?outputType=xml"],
    "매일경제":     ["https://www.mk.co.kr/rss/30000001/"],
    "AI타임스":     ["https://www.aitimes.com/rss/allArticle.xml"],
    "디지털투데이": ["https://www.digitaltoday.co.kr/rss/allArticle.xml"],
    "아이뉴스24":   ["https://www.inews24.com/rss/news_it.xml"],
    "머니투데이":   ["https://rss.mt.co.kr/mt_news.xml"],
}


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return raw


def parse_feed(url):
    """RSS/Atom -> [(title, link)]"""
    try:
        raw = fetch(url)
    except Exception as e:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        try:
            root = ET.fromstring(raw.decode("utf-8", "ignore").encode("utf-8"))
        except Exception:
            return []
    out = []
    for it in root.iter():
        tag = it.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        t = l = None
        for ch in it:
            c = ch.tag.split("}")[-1]
            if c == "title" and ch.text:
                t = ch.text.strip()
            elif c == "link":
                l = (ch.text or "").strip() or ch.attrib.get("href", "")
        if t and l:
            out.append((t, l))
    return out


def norm(s):
    """제목 정규화: 대괄호 말머리, 기호, 공백 제거"""
    s = re.sub(r"\[[^\]]{0,20}\]", "", s)
    s = re.sub(r"[\s​]+", "", s)
    s = re.sub(r"[^\w가-힣]", "", s)
    return s.lower()


def parse_adot(md):
    """에이닷비즈뉴스레터 섹션 -> [{title, outlet, date, related}]"""
    sec = md.split("## 텔레그램 - 에이닷비즈뉴스레터")
    if len(sec) < 2:
        return []
    lines = [l.rstrip() for l in sec[1].split("\n")]
    items, pending = [], None
    meta_re = re.compile(r"^ㄴ(\S+)\s+(\d{4}\.\d{2}\.\d{2})(?:\s*관련뉴스\s*(\d+)건)?")
    for l in lines:
        s = l.strip()
        m = meta_re.match(s)
        if m and pending:
            items.append({"title": pending, "outlet": m.group(1),
                          "date": m.group(2),
                          "related": int(m.group(3)) if m.group(3) else 0})
            pending = None
        elif s and not s.startswith(("ㄴ", "**", "#", "신규", "총 ", "7월")) and len(s) > 8:
            pending = s
        elif not s:
            pending = None
    return items


def main():
    md = open(sys.argv[1], encoding="utf-8").read()
    items = parse_adot(md)
    say("에이닷 항목 파싱: %d건" % len(items))

    outlets = sorted({i["outlet"] for i in items})
    say("등장 매체 %d개: %s" % (len(outlets), ", ".join(outlets)))

    have = [o for o in outlets if o in FEEDS]
    miss = [o for o in outlets if o not in FEEDS]
    say("  피드 보유 %d개 / 미보유 %d개: %s" % (len(have), len(miss), ", ".join(miss)))

    # 피드 인덱스 구축
    index = {}
    for o in have:
        arts = []
        for u in FEEDS[o]:
            arts += parse_feed(u)
        index[o] = [(norm(t), t, l) for t, l in arts]
        say("  [feed] %-12s %d건" % (o, len(arts)))

    ok = fail_nofeed = fail_nomatch = 0
    results = []
    for it in items:
        o = it["outlet"]
        if o not in index or not index[o]:
            fail_nofeed += 1
            results.append({**it, "status": "no_feed", "url": None})
            continue
        n = norm(it["title"])
        best, score = None, 0.0
        for nt, t, l in index[o]:
            r = difflib.SequenceMatcher(None, n, nt).ratio()
            if r > score:
                score, best = r, (t, l)
        if score >= 0.75:
            ok += 1
            results.append({**it, "status": "resolved", "score": round(score, 3),
                            "url": best[1], "matched": best[0]})
        else:
            fail_nomatch += 1
            results.append({**it, "status": "no_match", "score": round(score, 3),
                            "url": None, "closest": best[0] if best else None})

    tot = len(items)
    print("\n=== 결과 ===")
    say("  해결      %3d / %d  (%.1f%%)" % (ok, tot, ok / tot * 100))
    say("  피드없음  %3d          (%.1f%%)" % (fail_nofeed, fail_nofeed / tot * 100))
    say("  매칭실패  %3d          (%.1f%%)" % (fail_nomatch, fail_nomatch / tot * 100))
    json.dump(results, open("resolve_result.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    open("resolve_report.txt", "w", encoding="utf-8").write("\n".join(LOG))


if __name__ == "__main__":
    main()
