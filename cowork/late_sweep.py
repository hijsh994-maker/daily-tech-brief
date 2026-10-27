#!/usr/bin/env python3
"""경쟁사 속보 재점검 — 오전 수집 뒤에 나온 경쟁사 AI 기사를 판정 목록에 붙인다. [2026-10-27 신설]

[왜] 10-27 KT '토큰팩토리' 출시(경쟁사 신규 서비스, F10 고)를 놓쳤다. 발표가 10:00 광화문
  행사였고 대부분의 기사가 10시 이후에 나왔는데, 구글뉴스 수집은 09:44 에 끝났다. 수집 시점에
  잡힌 것은 미리 나온 1건(비즈니스플러스)뿐이라 링크도 못 찾고 단독 6점으로 묻혔다.
  사용자: "가나다 모두 적용해줘" (다 = 발송본 쓰기 직전 경쟁사 속보 재점검)

[무엇을] 경쟁사 이름(KT·LG유플러스·네이버·카카오·LG AI연구원·LG CNS·삼성SDS)으로 네이버 뉴스 API 를
  최신순 검색해, (1) 오늘 발행 (2) 제목에 AI 관련 낱말 (3) 오늘 후보 목록에 같은 사건이 없음
  — 세 조건을 다 맞는 기사만 고른다. 같은 사건 여러 매체는 한 건으로 묶고 보도수를 센다.
  고른 건은 prompt 에 {"idx": "L1", ..., "tier": "late"} 줄로 끼워 넣어 blind 목록의
  '=== 경쟁사속보 ===' 그룹으로 나오게 하고, 본문을 bodycache 에 받아 둔다(bodygo·newfact 용).
  같은 날 다시 돌리면 이미 붙인 L 건은 건너뛰고 새로 나온 것만 L(n+1).. 로 붙인다.

  bash cowork/run.sh late            # 7단계 blind 앞, 그리고 발송본 쓰기 직전(budget 앞)에 한 번 더
  bash cowork/run.sh late --dry      # 붙이지 않고 목록만
"""
import argparse
import datetime
import difflib
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "cowork"))
import naver_api                      # noqa: E402
import body_rescore                   # noqa: E402

QUERIES = ["KT AI", "KT", "LG유플러스", "LGU+", "네이버 AI", "네이버클라우드", "카카오 AI",
           "카카오", "LG AI연구원", "LG CNS", "삼성SDS AI"]
COMP = re.compile(r"(?<![A-Za-z])KT(?![A-Za-z&])|LG유플러스|LGU\+|LG U\+|유플러스|네이버|카카오|"
                  r"LG AI연구원|LG CNS|엑사원|삼성SDS")
AIREL = re.compile(r"AI|인공지능|에이전트|데이터센터|AIDC|GPU|LLM|모델|로봇|토큰|클라우드|엣지|반도체|피지컬")
# 경쟁사 이름이 들어가도 사건이 아닌 것(주가·인사 동정·행사 홍보)
NOISE = re.compile(r"주가|특징주|목표주가|애프터마켓|프리마켓|\[포토\]|\[인사\]|부고|시황|증시")


def norm(t):
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", t or "")
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", t).lower()


def toks(t):
    return set(re.findall(r"[가-힣A-Za-z0-9]{2,}", t or ""))


GENERIC = set("""AI 인공지능 출시 공개 본격 강화 확대 플랫폼 서비스 모델 기업 기업용 시장 사업 전략 도입
선보 개시 추진 협력 업무 이용자 고객 기술 개발 지원 데이터 에이전트 클라우드 데이터센터 경쟁 최대
네이버 카카오 KT LG유플러스 LGU 유플러스 LG CNS 삼성SDS 연구원 단독 영상 통신 통신사 통신픽 인사이드""".split())
PART = re.compile(r"(으로|에서|에게|까지|부터|이다|으로써|로|를|을|는|은|이|가|의|에|도|와|과|만|서)$")


def dtoks(t):
    """사건을 가리키는 특징 낱말(조사 뗀 3자 이상, 회사명·일반어 제외)."""
    out = set()
    for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", re.sub(r"\[[^\]]*\]", " ", t or "")):
        w = PART.sub("", w)
        if len(w) >= 3 and w not in GENERIC and not COMP.fullmatch(w):
            out.add(w.lower())
    return out


def same_event(a, b):
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    if difflib.SequenceMatcher(None, na, nb).ratio() >= 0.55:
        return True
    # [10-27] 같은 발표를 매체마다 다르게 쓴다('토큰팩토리 출시'/'토큰팩토리 띄운다').
    #   특징 낱말(제품·사업명 등)을 하나라도 공유하면 같은 사건으로 본다.
    if dtoks(a) & dtoks(b):
        return True
    ta, tb = toks(a), toks(b)
    inter = ta & tb
    return len(inter) >= 3 and len(inter) / max(1, min(len(ta), len(tb))) >= 0.5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    day = datetime.date.fromisoformat(a.date) if a.date else datetime.date.today()
    pp = os.path.join(BASE, "out", "prompt-%s.txt" % day)
    if not os.path.exists(pp):
        print("[late] %s 없음 — brief/extend 뒤에 돌린다" % pp)
        sys.exit(1)
    prompt = open(pp, encoding="utf-8").read()
    pool, late_n = [], 0
    for line in prompt.splitlines():
        if line.startswith('{"idx"') or line.startswith('{"nolink_idx"'):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            pool.append(d.get("headline") or "")
            if str(d.get("idx", "")).startswith("L"):
                late_n = max(late_n, int(str(d["idx"])[1:]))
    # tier0 원문(슬랙·뉴스클립 등)에 있던 제목도 같은 사건으로 본다
    found = []
    for q in QUERIES:
        try:
            items = naver_api.search(q, display=100, sort="date")
        except Exception as e:
            print("[late] '%s' 검색 실패: %s" % (q, str(e)[:60]))
            continue
        for it in items or []:
            pd = naver_api._pubdate(it.get("pubDate"))
            if pd != day:
                continue
            t = naver_api.strip_tags(it.get("title", ""))
            if not COMP.search(t) or not AIREL.search(t) or NOISE.search(t):
                continue
            found.append(dict(title=t, url=it.get("originallink") or it.get("link"),
                              naver=it.get("link") or "", pub=it.get("pubDate", "")))
    # 같은 사건 묶기
    groups = []
    for f in found:
        for g in groups:
            if f["url"] == g[0]["url"] or same_event(f["title"], g[0]["title"]):
                if f["url"] not in {x["url"] for x in g}:
                    g.append(f)
                break
        else:
            groups.append([f])
    new = []
    for g in groups:
        rep = g[0]
        if any(same_event(rep["title"], p) for p in pool if p):
            continue
        new.append((rep, len(g)))
    print("[late] %s 경쟁사 속보 — 검색 %d건 / 사건 %d개 / 후보에 없던 사건 %d개"
          % (day, len(found), len(groups), len(new)))
    for i, (rep, n) in enumerate(new, late_n + 1):
        print("  L%d  [%s매체] %s\n       %s" % (i, n, rep["title"], rep["url"]))
    if a.dry or not new:
        return
    # 본문 받기(bodygo·newfact 용)
    cpath = os.path.join(BASE, "out", "bodycache-%s.json" % day)
    cache = json.load(open(cpath, encoding="utf-8")) if os.path.exists(cpath) else {}
    with ThreadPoolExecutor(6) as ex:
        bodies = list(ex.map(lambda r: body_rescore.fetch(r[0]["url"], r[0]["title"]), new))
    ok = 0
    for (rep, n), (body, err) in zip(new, bodies):
        if body:
            cache[rep["url"]] = body
            ok += 1
    json.dump(cache, open(cpath, "w", encoding="utf-8"), ensure_ascii=False)
    lines = []
    for i, (rep, n) in enumerate(new, late_n + 1):
        host = re.sub(r"^https?://(www\.)?", "", rep["url"]).split("/")[0]
        lines.append(json.dumps({
            "idx": "L%d" % i, "category": None, "headline": rep["title"],
            "outlet": host, "url": rep["url"], "pub_date": str(day), "n_outlets": n,
            "score": None, "tier": "late", "signals": ["경쟁사속보"],
        }, ensure_ascii=False))
    block = "\n[경쟁사 속보 — 오전 수집 이후 (run.sh late)]\n" + "\n".join(lines) + "\n"
    marker = "\n\n[링크 미확보 후보]"
    if marker in prompt:
        h, t = prompt.split(marker, 1)
        prompt = h + "\n" + block + marker + t
    else:
        prompt += "\n" + block
    open(pp, "w", encoding="utf-8").write(prompt)
    print("[late] prompt 에 %d건 추가(L%d~L%d), 본문 %d/%d -> 다음: bash cowork/run.sh blind 후 L 건 판정"
          % (len(new), late_n + 1, late_n + len(new), ok, len(new)))


if __name__ == "__main__":
    main()
