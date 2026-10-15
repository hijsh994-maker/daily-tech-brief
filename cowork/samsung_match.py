# -*- coding: utf-8 -*-
"""
samsung_match.py - 삼성 섹션 링크 매칭을 LLM 이 맡는 3단계 파이프라인.

## 현행(기계) 방식이 왜 실패하는가
삼성 텔레그램은 원문 링크 없이 '■ 요약 한 문장'만 온다. ingest_tier0.discover()
가 구글뉴스로 실제 헤드라인을 역추적하는데, 두 군데가 동시에 약하다.

  (1) 질의 생성 - samsung_resolve.queries()
      '주체(첫 쉼표 앞) + 정규식으로 뽑은 토큰 3개'로 질의를 만든다. 무엇이
      사건의 핵심인지 모르기 때문에 엉뚱한 토큰이 뽑힌다.
        요약 "AWS, 애플-구글 출신 AI 인프라 전문가 오리 헌슈타트 영입.
              AgentCore 및 SageMaker 총괄"
        질의 "AWS AgentCore SageMaker"   <- '오리 헌슈타트 영입'이 빠졌다
      이러면 구글은 그냥 AWS 일반 기사를 준다.

  (2) 수락 판정 - discover() 의 conf
      '변별토큰 2개 이상 겹치고 주체가 제목에 있으면' auto 로 확정한다.
      위 예에서 후보 '아마존, 분기 매출 2000억달러 첫 돌파…AWS 매출 성장률'은
      {AWS, AI, 매출} 정도가 겹쳐 조건을 통과해 버린다. 토큰 겹침으로는
      '같은 회사의 다른 사건'을 절대 구분할 수 없다.

  실측(2026-07-31 기준, 원문 대조):
      07-30  링크 5건 중 정확 2건 (40%)
      07-31  링크 8건 중 정확 5건 (63%)

## 이 스크립트의 구조
검색·적용은 결정론적으로 스크립트가 하고, 판단이 필요한 두 지점만 LLM 이 맡는다.
LLM 이 링크를 '생성'하지 않는다 - 검색 결과 중에서 '고르기만' 한다(환각 차단).

  1. --dump    : 요약문 목록을 낸다            -> LLM 이 질의를 만든다
  2. --search  : LLM 질의로 검색, 후보를 모은다 -> LLM 이 정답을 고른다
  3. --apply   : 고른 것을 tier0 json 에 쓴다

사용:
  python3 cowork/samsung_match.py --date 2026-07-31 --dump
  python3 cowork/samsung_match.py --date 2026-07-31 --search queries.json
  python3 cowork/samsung_match.py --date 2026-07-31 --apply picks.json

queries.json  {"1": ["AWS 오리 헌슈타트 영입", "AWS 인프라 임원 영입"], "2": [...]}
picks.json    {"1": 3, "2": 0, "5": null}   # 후보 번호(1-based), null = 해당 없음
"""
import argparse
import datetime
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import truststore  # noqa: E402
truststore.inject_into_ssl()

import samsung_resolve as SR   # noqa: E402
import naver_api               # noqa: E402

OUTDIR = os.path.join(BASE, "out")


def tier0_path(day):
    return os.path.join(OUTDIR, "tier0-%s.json" % day)


def samsung_items(day):
    p = tier0_path(day)
    if not os.path.exists(p):
        print("[오류] %s 없음. tier0 를 먼저 실행" % p)
        sys.exit(1)
    data = json.load(open(p, encoding="utf-8"))
    return p, data, [x for x in data["items"] if x.get("src") == "samsung"]


def cmd_dump(day):
    _, _, items = samsung_items(day)
    print("[dump] %s 삼성 %d건. 각 요약의 '사건'을 잡아내는 한국어 검색어를"
          " 1~3개씩 만드세요." % (day, len(items)))
    print("       고유명사(인명·제품명·금액)를 넣고, 회사명만으로 된 질의는 피하세요.")
    out = {}
    for i, x in enumerate(items, 1):
        out[str(i)] = x.get("summary", "")
        print()
        print(" [%d] %s" % (i, x.get("summary", "")))
        cur = x.get("title")
        if cur:
            print("      (기계가 붙인 현재 매칭: %s)" % cur[:60])
    print()
    print(json.dumps(out, ensure_ascii=False))


def _search_one(args):
    """질의 하나로 구글뉴스+네이버를 훑어 후보를 모은다."""
    q, lo, hi = args
    cands = []
    # 구글뉴스는 리다이렉트 링크만 줘서 URL 로는 못 쓴다(서버에서 안 풀린다).
    # '실재하는 헤드라인'을 얻는 용도로만 쓰고, URL 은 선택 후 확정한다.
    #
    # 로케일은 질의의 언어로 정한다. 한글이 없으면 영문 로케일로 검색해
    # 국내 미보도 사안(외국인 임원 영입, 해외 상장 추진 등)을 잡는다.
    loc = "ko" if re.search(r"[가-힣]", q) else "en"
    try:
        for c in SR.gnews(q, "3d", locale=loc):
            if c["date"] and not (lo <= c["date"] <= hi):
                continue
            if SR.source_rank(c["outlet"]) < 0 or SR.is_roundup(c["headline"]):
                continue
            cands.append({"headline": c["headline"], "outlet": c["outlet"],
                          "date": str(c["date"]) if c["date"] else None,
                          "url": None, "via": "gnews-" + loc, "q": q})
    except Exception:
        pass
    if loc == "en":
        return cands          # 영문 질의는 네이버에서 볼 게 없다
    try:
        from email.utils import parsedate_to_datetime
        for it in naver_api.search(q, display=10):
            u = it.get("originallink") or ""
            if not u.startswith("http"):
                continue
            try:
                d = parsedate_to_datetime(it["pubDate"]).date()
            except Exception:
                d = None
            if d and not (lo <= d <= hi):
                continue
            import re as _re
            t = _re.sub(r"<[^>]+>", "", it.get("title") or "")
            t = t.replace("&quot;", '"').replace("&amp;", "&")
            cands.append({"headline": t, "outlet": SR.norm_source(u),
                          "date": str(d) if d else None,
                          "url": u, "via": "naver", "q": q})
    except Exception:
        pass
    return cands


def cmd_search(day, qpath):
    _, _, items = samsung_items(day)
    queries = json.load(open(qpath, encoding="utf-8"))
    d = datetime.date.fromisoformat(day)
    lo, hi = d - datetime.timedelta(days=2), d

    jobs, owner = [], []
    for k, qs in queries.items():
        for q in (qs if isinstance(qs, list) else [qs]):
            jobs.append((q, lo, hi))
            owner.append(k)

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(_search_one, jobs))

    per = {}
    for k, cands in zip(owner, results):
        per.setdefault(k, [])
        for c in cands:
            # 중복 제거. gnews 후보는 url 이 없으므로 헤드라인으로 판정한다.
            key = c["url"] or SR.norm_title(c["headline"]) if hasattr(SR, "norm_title") \
                else (c["url"] or c["headline"])
            if not any((e["url"] or e["headline"]) == (c["url"] or c["headline"])
                       for e in per[k]):
                per[k].append(c)

    print("[search] %s 질의 %d개 -> 항목별 후보" % (day, len(jobs)))
    dump = {}
    for i, x in enumerate(items, 1):
        k = str(i)
        cands = per.get(k, [])[:12]
        dump[k] = cands
        print()
        print(" [%s] 요약: %s" % (k, x.get("summary", "")[:96]))
        if not cands:
            print("      후보 없음")
        for j, c in enumerate(cands, 1):
            # [직링크] = 네이버에서 온 후보라 URL 이 이미 확정돼 있다.
            # 표시 없음 = 구글뉴스 헤드라인만 있어 나중에 URL 을 다시 찾아야 하고,
            #             실패하면 링크 없음이 된다. 같은 사건이면 [직링크]를 고를 것.
            mark = "[직링크]" if c["url"] else "        "
            print("      %2d) %s %s  | %s %s" % (j, mark, c["headline"][:62],
                                                 c["outlet"], c["date"] or ""))
    p = os.path.join(OUTDIR, "samsung-cands-%s.json" % day)
    json.dump(dump, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print()
    print("[search] 후보 저장: %s" % p)
    print("[search] 각 항목에서 '같은 사건'인 후보 번호를 고르세요."
          " 없으면 null. 애매하면 null.")


def fetch_title(url):
    """URL 의 실재 여부를 확인한다. -> (title, status)

    status: "ok"       200 이고 제목을 뽑았다. title 에 실제 페이지 제목.
            "blocked"  403/401/429. 봇 차단일 뿐 링크는 살아 있다 -> 채택한다.
            "dead"     404/410/DNS 실패 등. 채택하면 안 된다.

    [중요] 403 을 죽은 링크로 보면 안 된다. 언론사 상당수(Axios·Techzine·
    The Information 등)가 서버 대 서버 요청을 막는다. 사람이 브라우저로 열면
    정상인데 여기서만 막히는 것이라, 이걸 이유로 버리면 멀쩡한 원문을 잃는다.
    """
    import re as _re
    import ssl
    import urllib.error
    import urllib.request
    if not url.startswith("http"):
        return None, "dead"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    hdr = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/126.0 Safari/537.36"),
           "Accept": "text/html,application/xhtml+xml",
           "Accept-Language": "ko,en;q=0.8"}
    try:
        req = urllib.request.Request(url, headers=hdr)
        body = urllib.request.urlopen(req, timeout=15, context=ctx
                                      ).read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 429):
            return None, "blocked"
        return None, "dead"
    except Exception:
        return None, "dead"
    m = (_re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', body)
         or _re.search(r"<title[^>]*>(.*?)</title>", body, _re.S))
    if not m:
        return None, "blocked"          # 200 인데 제목이 없으면 JS 렌더링. 살아는 있다.
    import html as _html
    t = _html.unescape(m.group(1)).strip()
    return (t[:120] or None), ("ok" if t else "blocked")


def cmd_show(day):
    """--search 로 모아둔 후보를 재검색 없이 다시 출력한다."""
    _, _, items = samsung_items(day)
    p = os.path.join(OUTDIR, "samsung-cands-%s.json" % day)
    if not os.path.exists(p):
        print("[오류] %s 없음. --search 를 먼저 실행" % p)
        sys.exit(1)
    dump = json.load(open(p, encoding="utf-8"))
    for i, x in enumerate(items, 1):
        k = str(i)
        print()
        print(" [%s] 요약: %s" % (k, x.get("summary", "")[:96]))
        cands = dump.get(k, [])
        if not cands:
            print("      후보 없음")
        for j, c in enumerate(cands, 1):
            mark = "[직링크]" if c["url"] else "        "
            print("      %2d) %s %s  | %s %s" % (j, mark, c["headline"][:62],
                                                 c["outlet"], c["date"] or ""))


def cmd_apply(day, ppath):
    p, data, items = samsung_items(day)
    picks = json.load(open(ppath, encoding="utf-8"))
    cpath = os.path.join(OUTDIR, "samsung-cands-%s.json" % day)
    cands = json.load(open(cpath, encoding="utf-8"))

    linked = cleared = 0
    for k, sel in picks.items():
        i = int(k)
        if not (1 <= i <= len(items)):
            print("[경고] 범위 밖 번호 %s 무시" % k)
            continue
        x = items[i - 1]

        # 직접 URL 지정: {"url": "...", "outlet": "TechCrunch"}
        # 국내에 보도가 없어 후보 목록으로 못 잡는 사안을 웹검색으로 찾아 넣는 경로.
        # 링크를 그냥 믿지 않는다 - 실제로 열어보고 페이지 제목을 뽑아 기록한다.
        # (열리지 않으면 링크 없음 처리. 죽은 링크가 브리프에 실리는 것이 최악이다.)
        if isinstance(sel, dict):
            url = (sel.get("url") or "").strip()
            title, status = fetch_title(url)
            if status == "dead":
                print("[apply] %s번 URL 이 죽었음(404/접속불가) -> 링크 없음 처리" % k)
                x["url"] = x["title"] = x["url_via"] = None
                x["confidence"] = "unresolved"
                x["matched_by"] = "llm-deadlink"
                cleared += 1
                continue
            if status == "blocked":
                print("[apply] %s번 봇 차단(403 등)이라 제목 확인 불가. 링크는 살아있어 채택"
                      % k)
            else:
                print("[apply] %s번 직접URL 검증 OK: %s" % (k, title[:60]))
            x["url"] = url
            x["title"] = sel.get("title") or title
            x["outlet"] = sel.get("outlet") or SR.norm_source(url)
            x["pub_date"] = sel.get("date") or x.get("pub_date")
            x["url_via"] = "llm-websearch"
            x["confidence"] = "auto"
            x["matched_by"] = "llm-direct"
            x["verified_page_title"] = title
            x["link_check"] = status
            linked += 1
            continue

        if sel is None:
            # 매칭 실패는 '링크 없음'으로 확정한다. 제목도 지워야 refine 이
            # 잘못된 제목으로 엉뚱한 클러스터에 병합하지 않는다.
            x["url"] = x["title"] = x["url_via"] = None
            x["confidence"] = "unresolved"
            x["matched_by"] = "llm-reject"
            cleared += 1
            continue
        c = cands.get(k, [])[int(sel) - 1]
        url, via = c["url"], c["via"]
        if not url:
            # 구글뉴스 후보는 헤드라인만 있다. 그 헤드라인으로 실제 원문 URL 을
            # 확정한다(인덱스/네이버 제목매칭 - 제목이 정확하므로 신뢰도 높다).
            d = datetime.date.fromisoformat(day)
            r = SR.resolve_link(c["headline"], c["outlet"],
                                d - datetime.timedelta(days=2), d)
            if not r:
                print("[apply] %s번 '%s' -> URL 확정 실패, 링크 없음 처리"
                      % (k, c["headline"][:44]))
                x["url"] = x["title"] = x["url_via"] = None
                x["confidence"] = "unresolved"
                x["matched_by"] = "llm-nourl"
                cleared += 1
                continue
            url, via = r["url"], r["via"]
        x["url"] = url
        x["title"] = c["headline"]
        x["outlet"] = c["outlet"]
        x["pub_date"] = c["date"] or x.get("pub_date")
        x["url_via"] = "llm-" + via
        x["confidence"] = "auto"
        x["matched_by"] = "llm"
        linked += 1

    json.dump(data, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    tot = sum(1 for x in items if x.get("url"))
    print("[apply] 확정 %d건 / 해제 %d건 -> 삼성 %d건 중 링크 %d건 (%.0f%%)"
          % (linked, cleared, len(items), tot, 100.0 * tot / max(len(items), 1)))
    print("[apply] 저장: %s" % p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--search")
    ap.add_argument("--show", action="store_true",
                    help="이미 모아둔 후보를 다시 출력(재검색 안 함)")
    ap.add_argument("--apply")
    a = ap.parse_args()
    day = (datetime.date.fromisoformat(a.date) if a.date
           else datetime.date.today()).isoformat()
    if a.dump:
        cmd_dump(day)
    elif a.show:
        cmd_show(day)
    elif a.search:
        cmd_search(day, a.search)
    elif a.apply:
        cmd_apply(day, a.apply)
    else:
        print("--dump / --search queries.json / --apply picks.json 중 하나")
        sys.exit(1)


if __name__ == "__main__":
    main()
