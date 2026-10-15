# -*- coding: utf-8 -*-
"""
body_rescore.py - 링크가 확보된 후보의 '본문'을 받아 관심 주제 신호를 재채점한다.

## 왜 필요한가
refine.py 의 채점은 txt() = title + summary 만 본다. 그런데 실측하면
후보의 96.9%(최근 6일 2140건 중 2074건)가 summary 가 비어 있다.
즉 **사실상 제목 한 줄만 보고 점수를 매기고 있었다.**

  실측 2026-08-07: '경북도, 한국형 AI 기반 가상공장 플랫폼 개발 착수'는
    본문에 '디지털트윈'이 두 번 나오는데 제목에는 없다. 파이프라인은
    이 단어를 영영 못 봤고, 사람은 이 기사를 브리프에 실었다.

본문을 갖고 있는 건 삼성 텔레그램·NewsClips 뿐인데 그것도 기사 본문이
아니라 큐레이터가 쓴 한 줄 요약이다.

## 왜 refine 안에서 안 하나
refine 은 '제목으로 채점 -> 컷 통과분만 링크 해결' 순서로 설계돼 있다.
원시 후보가 수천 건이라 전부 본문을 받으면 수십 분이 걸리기 때문이다.
그래서 링크가 확정된 뒤(=수백 건으로 줄어든 뒤) 이 단계를 따로 돌린다.

## 무엇을 더하고 무엇을 안 더하나
본문은 제목과 달리 '스쳐 지나가는 언급'이 많다. 그래서 **가점만** 주고
**감점은 절대 주지 않는다.** 신호도 오탐이 적은 관심 주제로 한정한다.

  더한다:   디지털트윈 / 소버린 / 지정학      (사내 R&D·전략 관심 주제)
  안 더한다: 프런티어(거의 모든 AI 기사가 엔비디아·오픈AI를 언급한다)
             딜·실적(본문 보일러플레이트에 '투자 유치'가 흔하다)
             high/medium/low(범용 동사라 본문에선 전부 걸린다)

이미 제목에서 같은 신호가 걸린 항목은 중복 가산하지 않는다.
본문 가산은 합계 +4 로 제한한다(제목 신호를 뒤집지 않도록).

## 어떻게 쓰나
  bash cowork/run.sh body 30     # 마감 30초씩 끊어서 '남았습니다'가 없을 때까지
  캐시(out/bodycache-DATE.json)에 URL 기준으로 남으므로 재실행해도
  같은 페이지를 다시 받지 않는다.
"""
import argparse
import datetime
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(BASE, "out")
sys.path.insert(0, BASE)
import yaml                                                   # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
# [2026-09-03] 8 -> 25. 본문 확보율이 13%(40/300)밖에 안 되던 원인이다.
#   왜 고쳤나: 실패 259건을 다시 요청해 사유를 분류했더니 **96%가 타임아웃**
#   이었다(URLError(TimeoutError) 91% + socket.timeout 5%). HTTP 403/404 는
#   0건, 즉 봇 차단이 아니라 그냥 8초 안에 응답이 안 온 것이다.
#   실측(실패했던 40건 재시도):
#       timeout  8초 ->  5/40 성공(12%)
#       timeout 25초 -> 36/40 성공(90%)  성공건 평균 13.7초 / 최대 17.9초
#   국내 언론사 페이지가 광고·트래커를 물고 오느라 첫 바이트가 늦다.
#   **워커도 8 -> 16 으로 함께 올린다.** 타임아웃만 늘리면 같은 마감(120초)
#   안에 처리량이 120건 -> 38건으로 떨어진다. links 단계와 달리 여기는
#   상대 사이트가 100개 넘게 흩어져 있어 워커를 늘려도 한 곳에 몰리지 않는다.
TIMEOUT = 25
BODY_MAX = 4000          # 본문 앞부분만 본다(관련 서술은 리드에 몰려 있다)
BONUS_CAP = 4
# 본문에서 '스쳐 지나가는 한 번'은 그 기사의 주제가 아니다.
#   실측 08-07 오탐: '네이버 2분기 매출 역대 최대'에 디지털트윈 1회,
#     '통신사 AI 기업 변신'에 청문회 1회, 딥시크 가격인상 기사에 '모티프' 1회.
#     ('모티프'는 국산 모델명이자 흔한 일반명사라 본문에선 특히 위험하다)
#   같은 날 진짜 주제인 기사는 경북도 2회, 콘테크 4회로 반복 등장했다.
MIN_BODY_HITS = 2

# 본문에서만 추가로 볼 신호. (config 키 -> 표시 이름, 가점)
BODY_SIGNALS = [("digitaltwin_weight", "디지털트윈", 2),
                ("sovereign_weight", "소버린", 2),
                ("geopolitics_weight", "지정학", 2)]

RE_TAG = re.compile(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>")
RE_ANY = re.compile(r"(?s)<[^>]+>")
RE_WS = re.compile(r"\s+")


def log(m):
    print("[body] %s" % m)


def strip_html(h):
    h = RE_TAG.sub(" ", h)
    h = RE_ANY.sub(" ", h)
    h = (h.replace("&nbsp;", " ").replace("&amp;", "&")
          .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    return RE_WS.sub(" ", h).strip()


def _clip(text, title):
    """본문이 시작되는 지점부터 자른다.

    [왜] 페이지 앞부분은 내비게이션·섹션 메뉴·관련기사 목록이라
      기사와 무관한 단어가 잔뜩 들어 있다. 그대로 4000자를 보면
      엉뚱한 기사가 걸린다.
      실측 08-07: '네이버 2분기 매출 역대 최대'가 사이드바에 있던
      '디지털트윈'에 걸려 가점을 받았다(기사 내용과 무관).
    제목은 본문 바로 위에 한 번 더 나오므로, 제목의 특징적인 앞부분을
    찾아 그 뒤부터 읽는다. 못 찾으면 전체에서 앞부분을 쓴다.
    """
    t = RE_WS.sub(" ", (title or "")).strip().lstrip("[").strip()
    key = t[:18]
    if len(key) >= 8:
        i = text.find(key, 200)          # 200 이전은 <title> 태그 영역
        if i > 0:
            return text[i:i + BODY_MAX]
    return text[:BODY_MAX]


def arc_body(html):
    """Arc Publishing 계열 매체의 본문을 JSON 에서 꺼낸다. [2026-09-22 신설]

    [왜] 조선비즈·조선일보·IT조선은 Arc Publishing(=Fusion) 으로 만들어져
      본문이 <p> 태그가 아니라 `Fusion.globalContent` 자바스크립트 객체
      안에 들어 있다. HTML 태그만 벗겨 내면 남는 글자가 8낱말뿐이라
      `본문짧음` 으로 조용히 실패해 왔다. HTTP 는 200 이고 페이지도
      300KB 넘게 받아 오므로 어떤 실패 통계에도 '오류'로 잡히지 않았다.
      실측(최근 8일 누적, 본문 확보 실패율):
          조선비즈  65/65   100.0%   <- 단 한 건도 성공한 적이 없다
          조선일보  58/76    76.3%
          IT조선    31/77    40.3%
      09-22 '고' 13건 중 본문 미확보 3건의 하나였던 [128] 구광모·나델라
      LG·MS 건이 조선비즈였다. 제목만 보고 rank 4 를 줬다.

    [무엇을] content_elements 중 type=="text" 만 이어 붙인다. 사진 설명·
      관련기사·광고 요소는 다른 type 이라 자연히 빠진다. 이미 기사 본문
      그 자체라 _clip() 으로 앞머리를 잘라 낼 필요가 없다.
    """
    m = re.search(r"Fusion\.globalContent\s*=\s*(\{.*?\});", html, re.S)
    if not m:
        return ""
    try:
        g = json.loads(m.group(1))
    except ValueError:
        return ""
    els = g.get("content_elements") or []
    parts = [e.get("content", "") for e in els
             if isinstance(e, dict) and e.get("type") == "text"]
    if not parts:
        return ""
    return strip_html(" ".join(parts))[:BODY_MAX]


def fetch(url, title=""):
    """(본문, 실패사유) 를 돌려준다. 성공이면 사유는 None.

    [2026-09-03] 종전에는 `except Exception: return ""` 로 실패를 통째로
      삼켰다. 그래서 로그에 '본문 확보 0건' 으로만 찍혔고, 그게 '가점
      대상이 없었다' 인지 '받기를 실패했다' 인지 구분되지 않았다.
      실제로는 300건 중 259건이 조용히 실패하고 있었고 87% 가 빈
      문자열이었는데 두 주 넘게 아무도 몰랐다.
      links 단계는 이미 사유를 갈라 찍는다. 같은 형식으로 맞춘다.
    """
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        })
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read(400000)
        enc = "utf-8"
        m = re.search(rb'charset=["\']?([\w\-]+)', raw[:3000], re.I)
        if m:
            enc = m.group(1).decode("ascii", "ignore")
        html = raw.decode(enc, "ignore")
        # Arc Publishing(조선비즈·조선일보·IT조선) 우선. 실패하면 종전 경로.
        body = arc_body(html)
        if len(body) < 200:
            body = _clip(strip_html(html), title)
        if len(body) < 200:
            return "", "본문짧음"
        return body, None
    except urllib.error.HTTPError as e:
        return "", ("HTTP %d" % e.code)
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", e))
        if "timed out" in reason or isinstance(getattr(e, "reason", None),
                                               socket.timeout):
            return "", "타임아웃"
        return "", ("네트워크: %s" % reason[:30])
    except socket.timeout:
        return "", "타임아웃"
    except Exception as e:
        return "", ("%s" % type(e).__name__)


def hit(text, patterns):
    out = []
    for p in patterns or []:
        p = str(p)
        try:
            if re.search(r"[\[\]\(\)\|\*\+\?]", p):
                if re.search(p, text):
                    out.append(p)
            elif p in text:
                out.append(p)
        except re.error:
            if p in text:
                out.append(p)
    return out


def pool_of(data):
    return (data.get("items") or []) + (data.get("rescue") or []) \
        + (data.get("own_track") or [])


def _verdict_pool(day):
    """verdict-<날짜>.csv 의 '고' 전량을 (url 있는 것만) 돌려준다.

    [2026-09-03 신설] idx -> url 은 out/prompt-<날짜>.txt 의 후보 JSONL 에서
      읽는다. refined 의 idx 와 프롬프트의 idx 가 같은 체계이고, 링크
      미확보 후보(N 접두)는 url 이 없으므로 자연히 빠진다.
    반환: (항목 리스트, 로그에 적을 설명) / 실패면 (None, 사유)
    """
    import csv
    vp = os.path.join(OUTDIR, "verdict-%s.csv" % day)
    pp = os.path.join(OUTDIR, "prompt-%s.txt" % day)
    if not os.path.exists(vp):
        log("verdict 파일 없음: %s" % vp)
        log("  판정을 먼저 끝내세요. (run.sh blind -> 판정 -> run.sh verdict)")
        return None, ""
    if not os.path.exists(pp):
        log("prompt 파일 없음: %s" % pp)
        return None, ""
    url_of = {}
    for line in open(pp, encoding="utf-8"):
        if line.startswith('{"idx"'):
            try:
                j = json.loads(line)
            except Exception:
                continue
            if j.get("url"):
                url_of[str(j["idx"])] = (j["url"], j.get("title") or "")
    gos, nolink = [], 0
    for r in csv.DictReader(open(vp, encoding="utf-8")):
        if (r.get("grade") or "").strip() != "고":
            continue
        hit_ = url_of.get((r.get("idx") or "").strip())
        if not hit_:
            nolink += 1
            continue
        gos.append({"idx": r["idx"], "url": hit_[0], "title": hit_[1]})
    note = "verdict '고' 전량"
    if nolink:
        note += " / 링크 없어 제외 %d건" % nolink
    return gos, note


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    # [2026-08-24] 30 -> 120. bash 호출 상한은 45초가 아니라 약 178초다.
    #   30초로 끊으면 회당 왕복 8초가 붙어 15회에 2분이 포장비로 나갔다.
    ap.add_argument("--deadline", type=float, default=120.0)
    # [2026-09-03] 8 -> 16. TIMEOUT 을 8 -> 25 초로 늘리면서 같은 마감 안의
    #   처리량이 떨어지는 것을 상쇄한다. 상대 사이트가 100개 넘게 흩어져
    #   있어 한 곳에 부하가 몰리지 않는다(네이버 쿼터에 묶인 links 와 다름).
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--limit", type=int, default=300,
                    help="상위 몇 건까지 본문을 받을지")
    # [2026-09-03 신설] 판정이 끝난 뒤 '고' 전량의 본문만 받는 모드.
    #   왜 필요한가: 기본 모드는 **기계 점수 상위 300건**을 받는데, 고/중/저
    #   판정은 09-01 부터 점수를 보지 않고 한다. 두 집합이 설계상 어긋나
    #   실측 09-03 에 '고' 30건 중 본문이 있던 건 9건뿐이었다. 순위를 매기기
    #   전에 리드를 확인하려면 '고' 쪽을 따로 받아야 한다.
    ap.add_argument("--from-verdict", action="store_true",
                    help="out/verdict-<날짜>.csv 의 '고' 전량만 대상으로 한다")
    ap.add_argument("--dry", action="store_true", help="점수 반영 없이 미리보기")
    a = ap.parse_args()

    p = os.path.join(OUTDIR, "refined-%s.json" % a.date)
    if not os.path.exists(p):
        log("refined 파일 없음: %s" % p)
        return 2
    data = json.load(open(p, encoding="utf-8"))
    cfg = yaml.safe_load(open(os.path.join(BASE, "config", "keywords.yaml"),
                              encoding="utf-8"))
    sig = cfg.get("impact_signals", {})

    cpath = os.path.join(OUTDIR, "bodycache-%s.json" % a.date)
    cache = {}
    if os.path.exists(cpath):
        try:
            cache = json.load(open(cpath, encoding="utf-8"))
        except Exception:
            cache = {}

    if a.from_verdict:
        pool, note = _verdict_pool(a.date)
        if pool is None:
            return 2
    else:
        pool = sorted([x for x in pool_of(data) if x.get("url")],
                      key=lambda r: -(r.get("score") or 0))[:a.limit]
        note = "점수 상위 %d건" % a.limit
    todo = [x for x in pool if x["url"] not in cache]
    log("대상 %d건 (%s) / 캐시 %d건 / 받을 것 %d건"
        % (len(pool), note, len(pool) - len(todo), len(todo)))

    t0 = time.time()
    got = 0
    # [2026-09-03] 실패 사유 집계. 아래 재채점 루프가 `why` 라는 이름을
    #   이미 쓰고 있으므로(항목의 가점 사유) 이름을 겹치지 않게 둔다.
    fail_why = Counter()
    # [2026-08-24] resolve_links 와 같은 보정. 마감 직전에 새 덩어리를 시작하면
    #   그 덩어리가 끝날 때까지 기다리느라 마감을 넘긴다. 마감이 120초로
    #   커진 뒤로는 그 초과분이 호스트 상한(178초)을 위협한다.
    # [2026-09-03] 15초 고정이었는데 TIMEOUT 이 8 -> 25 로 늘면서 한 덩어리가
    #   최악 25초를 쓰게 됐다. 고정값을 두면 마감을 넘겨 호스트 상한(178초)을
    #   위협한다. TIMEOUT 에 연동시킨다.
    CHUNK_WORST = TIMEOUT + 5.0
    for i in range(0, len(todo), a.workers):
        if time.time() - t0 > a.deadline - CHUNK_WORST:
            break
        chunk = todo[i:i + a.workers]
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            bodies = list(ex.map(
                lambda it: fetch(it["url"], it.get("title") or ""), chunk))
        for it, (b, err) in zip(chunk, bodies):
            cache[it["url"]] = b
            if b:
                got += 1
            else:
                fail_why[err or "알수없음"] += 1
        json.dump(cache, open(cpath, "w", encoding="utf-8"),
                  ensure_ascii=False)
    json.dump(cache, open(cpath, "w", encoding="utf-8"), ensure_ascii=False)

    # ---- 재채점 (캐시에 있는 것 전부를 매번 다시 적용한다) ----
    changed = []
    for it in pool_of(data):
        body = cache.get(it.get("url") or "")
        if not body:
            continue
        # 이미 본문 가산을 받은 항목은 두 번 더하지 않는다(재실행 안전).
        why = [w for w in (it.get("why") or []) if "(본문)" not in w]
        base = (it.get("score") or 0) - sum(
            int(re.search(r"\+(\d+)", w).group(1))
            for w in (it.get("why") or []) if "(본문)" in w)
        head = "%s %s" % (it.get("title") or "", it.get("summary") or "")
        bonus, adds = 0, []
        for key, name, pts in BODY_SIGNALS:
            pats = sig.get(key)
            if hit(head, pats):          # 제목에서 이미 걸렸으면 중복 금지
                continue
            h = [x for x in hit(body, pats)
                 if len(re.findall(re.escape(x), body)) >= MIN_BODY_HITS]
            if h and bonus + pts <= BONUS_CAP:
                bonus += pts
                adds.append("%s(본문)+%d(%s)" % (name, pts, h[0]))
        if adds:
            changed.append((base, base + bonus, it.get("title") or "", adds))
        if not a.dry:
            it["why"] = why + adds
            it["score"] = base + bonus

    if not a.dry:
        for k in ("items", "rescue", "own_track"):
            if data.get(k):
                data[k] = sorted(data[k],
                                 key=lambda r: -(r.get("score") or 0))
        json.dump(data, open(p, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)

    left = len([x for x in pool if x["url"] not in cache])
    log("본문 확보 %d건 / 가점 적용 %d건 / 남음 %d건 (%.0f초)%s"
        % (got, len(changed), left, time.time() - t0,
           "  [dry-run]" if a.dry else ""))
    # [2026-09-03] 실패 사유를 갈라 찍는다. 이게 없어서 확보율 13% 를
    #   두 주 넘게 못 봤다. 캐시 전체 기준 확보율도 함께 찍는다.
    if fail_why:
        log("  받기 실패 %d건 -> %s"
            % (sum(fail_why.values()),
               " / ".join("%s %d" % kv for kv in fail_why.most_common())))
    filled = sum(1 for x in cache.values() if len(x or "") >= 200)
    if cache:
        rate = 100.0 * filled / len(cache)
        log("  본문 보유율 %d/%d (%.0f%%)" % (filled, len(cache), rate))
        if rate < 60:
            log("  [경고] 본문 보유율 60%% 미만. TIMEOUT·워커 설정을 보세요")
    for b, n, t, adds in sorted(changed, key=lambda x: -(x[1] - x[0]))[:15]:
        log("  %2d -> %2d  %s  | %s" % (b, n, t[:52], ", ".join(adds)[:60]))
    if left:
        log("남았습니다 - 다시 실행하세요")
    else:
        log("전부 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
