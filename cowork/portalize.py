#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""portalize.py - 브리프의 원문 링크를 네이버 뉴스 정적 링크로 바꾼다.

[2026-08-27 사용자 지시]
  "원문링크는 주소가 긴 경우가 많으니 가능하면 daum, naver 같은 포털의
   정적링크를 가져오는 게 더 좋다."

왜 지금 가능한가
  네이버 뉴스 검색 API 는 originallink(원문)와 link(포털)를 **둘 다** 준다.
  그런데 naver_api.resolve() 는 originallink 만 쓰고 link 를 버려 왔다
  ("link 는 news.naver.com 주소라 브리프용으로 부적합" - 과거 판단).
  실측 08-27: 브리프 30건 중 15건(50%)에 n.news.naver.com 링크가 있었고,
  서브도메인을 관대하게 보면 55~60% 까지 올라간다.

부수 효과 - 제목이 더 깨끗하다
  원문: '리벨리온… :: 공감언론 뉴시스 ::'  <- 매체 꼬리표가 붙는다
  포털: '리벨리온, 英 소버린AI 시장 공략…NPU 랙 100대 공급 추진'
  뒤이어 도는 retitle 이 이 제목을 쓰므로 헤드라인 품질도 같이 좋아진다.

왜 후보 단계가 아니라 여기인가
  후보 400여 건을 전부 조회하면 네이버 쿼터(초당 0.4건)로 15분이 넘는다.
  확정된 30건만 보면 75초면 끝난다. linkcheck 가 방금 링크를 검증했고
  retitle 이 곧바로 제목을 맞추므로 순서상으로도 이 자리가 맞다.

안전장치
  - 이미 포털 링크(n.news.naver.com / v.daum.net)면 건드리지 않는다.
  - 원문 도메인이 일치하는 후보만 쓴다(서브도메인 관대: biz.chosun.com ~ chosun.com).
  - 바꾸기 전에 **포털 링크를 실제로 열어 제목을 대조**한다. 겹침이 낮으면
    바꾸지 않는다. 다른 기사로 갈아끼우는 사고를 막는 마지막 관문이다.
  - brief 와 brief20 을 함께 고친다(20건은 30건의 부분집합이라 URL 이 같다).

사용법:  bash cowork/run.sh linkcheck   # 안에서 자동 호출
         python3 cowork/portalize.py --date 2026-08-27 [--dry]
"""
import argparse
import datetime
import json
import os
import re
import sys
import urllib.parse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import naver_api                                    # noqa: E402
import linkcheck                                    # noqa: E402

OUTDIR = os.path.join(BASE, "out")

# retitle.RE_ITEM 과 같은 모양. 헤드라인과 URL 을 함께 잡는다.
RE_ITEM = re.compile(
    r"^(\*\*\d+\\\.\s*)(.+?)(\*\*\s*\([^()]*\[원문보기\]\()(https?://[^)]+)(\)\))$",
    re.M)

PORTAL_HOSTS = ("n.news.naver.com", "news.naver.com", "v.daum.net",
                "m.news.naver.com")

# 포털 링크로 바꾸려면 이 정도는 겹쳐야 한다. retitle 의 보류 문턱과 맞춘다.
MIN_OVERLAP = 0.5


def log(m):
    print("[portalize] %s" % m)


def host(u):
    try:
        return (urllib.parse.urlparse(u).netloc or "").lower().lstrip("www.")
    except Exception:
        return ""


def root(h):
    """서브도메인을 벗긴 등록 도메인 비슷한 것. biz.chosun.com -> chosun.com

    co.kr / or.kr 처럼 2단계 접미사를 쓰는 도메인은 세 조각을 남긴다.
    """
    parts = (h or "").split(".")
    if len(parts) <= 2:
        return h
    if parts[-2] in ("co", "or", "ne", "go", "re", "pe") and parts[-1] == "kr":
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def is_portal(u):
    return host(u) in PORTAL_HOSTS


def find_portal(headline, cur_url):
    """(포털URL, 사유) 또는 (None, 사유)."""
    try:
        items = naver_api.search(headline, display=10)
    except naver_api.NotConfigured:
        return None, "키없음"
    except Exception as e:
        return None, "조회실패(%s)" % type(e).__name__
    if not items:
        return None, "검색결과없음"
    want = root(host(cur_url))
    for it in items:
        link = it.get("link") or ""
        if not link.startswith("https://n.news.naver.com"):
            continue
        if want and root(host(it.get("originallink") or "")) != want:
            continue
        # ?sid=105 는 섹션 표시일 뿐이라 떼도 열린다. 61자 -> 53자.
        return link.split("?")[0], "ok"
    return None, "비제휴/도메인불일치"


def verify(portal_url, headline):
    """포털 링크를 실제로 열어 제목이 그 기사인지 본다."""
    try:
        st, title, _ = linkcheck.fetch_title(portal_url)
    except Exception as e:
        return None, "받기실패(%s)" % type(e).__name__
    if st != 200 or not title:
        return None, "제목못읽음(%s)" % st
    r = linkcheck.title_match(headline, title)
    if r is None:
        return None, "판정불가"
    if r < MIN_OVERLAP:
        return None, "겹침낮음(%.0f%%)" % (r * 100)
    return title, "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    day = a.date

    main_path = os.path.join(OUTDIR, "brief-%s.md" % day)
    if not os.path.exists(main_path):
        log("brief-%s.md 없음 - 건너뜀" % day)
        return 0
    text = open(main_path, encoding="utf-8").read()

    seen, mapping = [], {}
    for m in RE_ITEM.finditer(text):
        headline, url = m.group(2).strip(), m.group(4)
        if url not in [u for _, u in seen]:
            seen.append((headline, url))

    n_skip = n_ok = n_no = n_bad = 0
    reasons = {}
    # 교체한 URL 의 포털 제목. 뒤이어 도는 retitle 이 linkcheck-<날짜>.json 을
    #   **URL 을 키로** 읽으므로, 새 URL 을 그 캐시에 넣어 주지 않으면
    #   교체한 건이 전부 제목 정렬에서 빠진다.
    new_titles = {}
    for headline, url in seen:
        if is_portal(url):
            n_skip += 1
            continue
        p, why = find_portal(headline, url)
        if not p:
            n_no += 1
            reasons[why] = reasons.get(why, 0) + 1
            continue
        title, why2 = verify(p, headline)
        if not title:
            n_bad += 1
            reasons[why2] = reasons.get(why2, 0) + 1
            log("  [보류] %s" % headline[:44])
            log("         %s / %s" % (why2, p))
            continue
        mapping[url] = p
        new_titles[p] = (headline, title)
        n_ok += 1
        log("  [교체] %s" % headline[:44])
        log("         %s" % url[:78])
        log("      -> %s" % p)

    log("결과 교체 %d / 이미포털 %d / 포털없음 %d / 검증보류 %d"
        % (n_ok, n_skip, n_no, n_bad))
    if reasons:
        log("  사유: %s" % ", ".join("%s %d" % kv for kv in
                                     sorted(reasons.items())))
    if a.dry or not mapping:
        return 0

    # brief 와 brief20 을 함께 고친다.
    for name in ("brief-%s.md" % day, "brief20-%s.md" % day):
        p = os.path.join(OUTDIR, name)
        if not os.path.exists(p):
            continue
        s = open(p, encoding="utf-8").read()
        n = 0
        for old, new in mapping.items():
            if old in s:
                s = s.replace(old, new)
                n += 1
        if n:
            open(p, "w", encoding="utf-8").write(s)
        log("%s: %d건 반영" % (name, n))

    # linkcheck 캐시에 새 URL 의 제목을 넣는다. 이걸 빠뜨리면 retitle 이
    #   교체된 16건을 '제목없음'으로 건너뛴다(실측으로 확인한 순서 의존성).
    lc_path = os.path.join(OUTDIR, "linkcheck-%s.json" % day)
    if new_titles and os.path.exists(lc_path):
        try:
            recs = json.load(open(lc_path, encoding="utf-8"))
            have = {r.get("url") for r in recs}
            add = 0
            for u, (hl, ti) in new_titles.items():
                if u in have:
                    continue
                recs.append({"headline": hl, "url": u, "src": "portalize",
                             "verdict": "OK", "reason": "포털 정적링크 교체",
                             "status": 200, "ratio": None,
                             "page_title": ti, "unreached": False})
                add += 1
            with open(lc_path, "w", encoding="utf-8") as f:
                json.dump(recs, f, ensure_ascii=False, indent=1)
            log("linkcheck 캐시에 포털 제목 %d건 추가(retitle 용)" % add)
        except Exception as e:
            log("[경고] linkcheck 캐시 갱신 실패(%s). retitle 이 교체분의 "
                "제목을 못 맞출 수 있습니다" % type(e).__name__)

    # 어떤 URL 이 어떻게 바뀌었는지 남긴다(검증·재현용).
    try:
        with open(os.path.join(OUTDIR, "portalize-%s.json" % day), "w",
                  encoding="utf-8") as f:
            json.dump({"date": day, "mapping": mapping}, f,
                      ensure_ascii=False, indent=1)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
