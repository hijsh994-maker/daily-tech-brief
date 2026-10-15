#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""커뮤니티 원천 수집 — Hacker News · GitHub · Hugging Face.  [2026-09-23 신설]

[왜 만들었나]
  임원 피드백(09-23): "타입세이프AI(제브)·오픈클로처럼 새로운 반향을 일으키는
  이슈를 빠르게 캐치하고 싶다." 실측으로 되짚어 보니
    - 제브는 tier2(구글뉴스)에 09-21~23 사흘간 8건 들어왔지만 매체 RSS 48개
      (tier1)에는 0건, 오픈클로도 RSS 인덱스 2만1천 건에 0건이었다.
    - 국내 매체 첫 기사는 공개 후 D+7 이었다. 판정을 완벽히 했어도 D+7 이다.
  개발자 커뮤니티에서 먼저 터지는 유형은 매체를 기다리면 늦는다. HN·GitHub·HF 를
  tier0 급 입력 통로(src="community")로 붙인다. 셋 다 키 없이 된다.
  (config/sources.yaml 의 tier5_community 는 설계만 있고 FEED_SECTIONS 에 없어
   한 번도 돌지 않았다. 형식이 JSON 이라 collect.py 의 RSS 경로와 안 맞는다.)

[무엇을 집나]
  HN      : 최근 2일 · points >= --hn-min(100) · AI 어휘 포함 · 상위 --hn-top(20)
  GitHub  : 최근 --gh-days(21)일 생성 · stars >= 300 · AI 토픽 · 스타 속도 상위
  HF      : trendingScore 상위 --hf-top(6) 모델
  최근 7일 community-*.json 에 이미 나온 URL 은 다시 넣지 않는다(매일 같은
  레포·모델이 반복 진입하는 것을 막는다).

[출력]  out/community-<날짜>.json  { "date", "items": [refine 입력 형식] }
  refine.py 가 tier0 과 같이 읽어 컷에 고정한다. buzz.py 가 '커뮤니티 원천'
  가중치로도 쓴다.

[한계]  X(트위터)·Reddit 은 없다. Reddit 은 UA 제한이 있어 뒤로 미룬다.
"""
import argparse, datetime, glob, json, os, re, sys, time
import urllib.request, urllib.parse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "out")
UA = "dtb-community/1.0 (+daily-tech-brief)"

AI_RE = re.compile(
    r"\b(ai|llm|llms|gpt|claude|gemini|grok|mistral|llama|qwen|deepseek|"
    r"agent|agents|agentic|model|models|transformer|inference|rag|"
    r"copilot|codex|cursor|openai|anthropic|nvidia|cuda|gpu|robot|robotics|"
    r"humanoid|diffusion|embedding|vector|fine-?tun\w*|open-?source|"
    r"mcp|vibe|prompt|reasoning)\b", re.I)


def get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def seen_recent(day, days=7):
    d = datetime.date.fromisoformat(day)
    urls = set()
    for i in range(1, days + 1):
        p = os.path.join(OUT, "community-%s.json" % (d - datetime.timedelta(days=i)).isoformat())
        if os.path.exists(p):
            try:
                for it in json.load(open(p, encoding="utf-8")).get("items", []):
                    if it.get("url"):
                        urls.add(it["url"])
            except Exception:
                pass
    return urls


def hn(day, min_points, hours=48):
    d = datetime.date.fromisoformat(day)
    since = int(time.mktime((d - datetime.timedelta(days=2)).timetuple()))
    out = []
    # 프런트페이지 + 최근 고득점 스토리 두 갈래. 둘을 합쳐 AI 어휘로 거른다.
    urls = [
        "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=60",
        "https://hn.algolia.com/api/v1/search_by_date?tags=story&hitsPerPage=200"
        "&numericFilters=points>%d,created_at_i>%d" % (min_points, since),
    ]
    seen = set()
    for u in urls:
        try:
            hits = get(u).get("hits", [])
        except Exception as e:
            print("[community] HN 실패: %s" % str(e)[:80]); continue
        for h in hits:
            t = (h.get("title") or "").strip()
            pts = h.get("points") or 0
            if not t or pts < min_points or h.get("objectID") in seen:
                continue
            if not AI_RE.search(t):
                continue
            seen.add(h.get("objectID"))
            link = h.get("url") or "https://news.ycombinator.com/item?id=%s" % h.get("objectID")
            pd = (h.get("created_at") or "")[:10]
            out.append({"src": "community", "title": t, "outlet": "HN", "url": link,
                        "pub_date": pd or day, "cluster": 1, "confidence": "auto",
                        "queries": [], "groups": [],
                        "signal": "HN %dpt·%d댓글" % (pts, h.get("num_comments") or 0),
                        "weight": pts})
    out.sort(key=lambda x: -x["weight"])
    return out


def github(day, gh_days, min_stars, top):
    d = datetime.date.fromisoformat(day)
    since = (d - datetime.timedelta(days=gh_days)).isoformat()
    q = "created:>%s stars:>%d" % (since, min_stars)
    u = ("https://api.github.com/search/repositories?q=%s&sort=stars&order=desc&per_page=60"
         % urllib.parse.quote(q))
    try:
        items = get(u).get("items", [])
    except Exception as e:
        print("[community] GitHub 실패: %s" % str(e)[:80]); return []
    out = []
    for r in items:
        txt = " ".join([r.get("name") or "", r.get("description") or "",
                        " ".join(r.get("topics") or [])])
        if not AI_RE.search(txt):
            continue
        created = (r.get("created_at") or "")[:10]
        try:
            age = max(1, (d - datetime.date.fromisoformat(created)).days)
        except Exception:
            age = gh_days
        stars = r.get("stargazers_count") or 0
        vel = stars / age
        desc = (r.get("description") or "").strip()
        title = "%s — %s" % (r.get("full_name"), desc[:90]) if desc else r.get("full_name")
        out.append({"src": "community", "title": title, "outlet": "GitHub",
                    "url": r.get("html_url"), "pub_date": created or day, "cluster": 1,
                    "confidence": "auto", "queries": [], "groups": [],
                    "signal": "GitHub ★%d·%d일·%.0f/일" % (stars, age, vel),
                    "weight": vel})
    out.sort(key=lambda x: -x["weight"])
    return out[:top]


def huggingface(day, top):
    u = "https://huggingface.co/api/models?sort=trendingScore&direction=-1&limit=%d" % top
    try:
        rows = get(u)
    except Exception as e:
        print("[community] HF 실패: %s" % str(e)[:80]); return []
    out = []
    for m in rows if isinstance(rows, list) else []:
        mid = m.get("id") or m.get("modelId")
        if not mid:
            continue
        out.append({"src": "community", "title": "HF trending: %s" % mid, "outlet": "HuggingFace",
                    "url": "https://huggingface.co/%s" % mid,
                    "pub_date": (m.get("createdAt") or day)[:10], "cluster": 1,
                    "confidence": "auto", "queries": [], "groups": [],
                    "signal": "HF ♥%d·↓%d" % (m.get("likes") or 0, m.get("downloads") or 0),
                    "weight": m.get("likes") or 0})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--hn-min", type=int, default=100)
    ap.add_argument("--hn-top", type=int, default=20)
    ap.add_argument("--gh-days", type=int, default=21)
    ap.add_argument("--gh-min-stars", type=int, default=300)
    ap.add_argument("--gh-top", type=int, default=10)
    ap.add_argument("--hf-top", type=int, default=6)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    items = hn(a.date, a.hn_min)[:a.hn_top] + github(a.date, a.gh_days, a.gh_min_stars, a.gh_top) \
        + huggingface(a.date, a.hf_top)
    old = seen_recent(a.date)
    fresh = [it for it in items if it.get("url") not in old]
    dup = len(items) - len(fresh)
    from collections import Counter
    c = Counter(it["outlet"] for it in fresh)
    print("[community] %s  HN %d / GitHub %d / HF %d  (최근 7일 재등장 %d건 제외)"
          % (a.date, c.get("HN", 0), c.get("GitHub", 0), c.get("HuggingFace", 0), dup))
    for it in fresh[:40]:
        print("   %-11s %-22s %s" % (it["outlet"], it["signal"], it["title"][:70]))
    if a.dry:
        return 0
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, "community-%s.json" % a.date)
    json.dump({"date": a.date, "items": fresh}, open(p, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("[community] 저장: %s (%d건)" % (p, len(fresh)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
