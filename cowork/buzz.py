#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""반향(버즈) 감지 — 30일 코퍼스에 없던 고유명사가 갑자기 여러 매체에 번지는가.
[2026-09-23 신설]

[왜 만들었나]
  임원 피드백: "타입세이프AI 같이 새로운 반향을 일으키는 이슈를 빠르게 캐치하고
  싶다. 오픈클로가 핫할 때 캐치를 못해 난처했다."
  실측(09-21~23, '제브'):
    09-21  AI타임스 "개발자 반응 폭발" / 토큰포스트 "오픈소스 구현 잇따라"
           -> tier2 에 들어왔지만 refine 점수 미달로 판정에 도달조차 못함
    09-22  지디넷 "돌풍" / 스타트업레시피 "출시" / 와우테일 "$40M" / IT조선 칼럼
           -> 와우테일·IT조선만 판정에 도달, 둘 다 '저'
    09-23  아시아경제 "개발자들이 열광" / 조선일보 "실리콘밸리서 주목"
           -> 조선일보만 도달, '중'
  refine 은 사전에 있는 이름(프런티어·경쟁사·소버린)만 올려 잡는다. **사전에
  없는 이름이 갑자기 뜨는 것**이 반향의 정의인데, 그걸 잡는 신호가 없었다.
  제목 5/8건에 '반응 폭발·돌풍·열광·잇따라·주목'이 있었지만 점수에 안 썼다.

[무엇을 재나]
  오늘 풀(tier2 전체 4천여 건 + tier1 창 안 + tier0 + community)의 제목에서
  따옴표 낱말과 영문 고유명사를 뽑아, 다음 셋을 곱한다.
    novel     : D-11 이전 30일 코퍼스(tier2-*.json + RSS 인덱스)에 없던 낱말인가
                (창을 D-3 으로 두면 제브처럼 8일 동안 후보 밖에서만 커진 이야기가
                 '이미 있던 낱말'로 찍혀 사라진다 — 09-23 백테스트로 확인)
    spread    : 최근 10일 창에서 몇 개 매체·며칠 등장했나 (오늘도 있어야 한다)
    reaction  : 제목에 제3자 반응 어휘가 붙었나 (돌풍·열광·반응 폭발·잇따라·주목·
                trending·viral·Show HN·stars)
    community : HN·GitHub·HF 에서도 보이나 (community-<날짜>.json)
  점수 = min(누적매체,10) + 2×min(등장일,5) + 3×min(반응,3) + 4×(커뮤니티>0) + 2×(커뮤니티>=3)
  임계 : AI 관련 제목이 하나라도 있고, 오늘 등장했고,
         (반응>=1 이고 누적매체>=2) 또는 커뮤니티>=1 또는 (등장일>=3 이고 누적매체>=4)
         -> 보도자료 살포(하루 16매체·반응 0·커뮤니티 0)는 여기서 떨어진다

[출력]  out/buzz-<날짜>.json  -> refine 이 읽어 해당 후보에 '반향+3' 가점과 컷 고정,
        blind_list 가 '=== 반향 후보 ===' 그룹으로 맨 위에 올린다.
        종료코드 0. 판정을 대신하지 않는다 — **검토 대상으로 끌어올릴 뿐**이다.

[백테스트]  bash cowork/run.sh buzz 2026-09-21   처럼 과거 날짜를 주면 그날 파일로 돈다.
"""
import argparse, datetime, glob, json, os, re, sqlite3, sys
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "out")
DB = os.path.join(BASE, "index", "articles.sqlite")

REACTION = ["돌풍", "열광", "반응 폭발", "반응폭발", "잇따라", "잇달아", "잇따르", "주목", "화제",
            "열풍", "급부상", "입소문", "구현 잇", "뜬 이유", "갑자기", "폭발적", "신드롬",
            "속속", "앞다퉈", "너도나도", "급증", "붐 ", "뜨거운",
            "trending", "viral", "show hn", "stars", "fastest-growing", "everyone is",
            "takes over", "explode", "clones", "obsessed", "fast-follow", "surge",
            "hottest", "skyrocket", "blowing up", "developers love", "goes viral",
            "jev-like", "-like family"]
QUOTE_RE = re.compile(r"[‘'“\"「『]([^’'”\"」』]{2,24})[’'”\"」』]")
EN_RE = re.compile(r"\b([A-Z][A-Za-z0-9]{2,}(?:[ -][A-Z][A-Za-z0-9]{1,})*)\b")
EN_STOP = set("""The This That These Those There Here How Why What When Where Who Which
Show Ask Tell New Big Top Best First Last Next Meet Inside Exclusive Opinion Review Watch
Breaking Update Report Live Video Photo Podcast Editorial Letter Weekly Daily Monday Tuesday
Wednesday Thursday Friday Saturday Sunday January February March April May June July August
September October November December Nasdaq Bloomberg Reuters CNBC WSJ Forbes Axios TechCrunch
Global Money Club News Korea Korean China Chinese Japan US USA UK EU Europe India Asia
AI GPT LLM CEO CTO IPO API GPU CPU NPU HBM DC Q1 Q2 Q3 Q4 Pro Max Plus Ultra Mini Series""".split())
AI_RE = re.compile(r"(AI|인공지능|에이전트|LLM|모델|로봇|휴머노이드|GPU|NPU|데이터센터|챗GPT|오픈AI|앤트로픽|딥시크|오픈소스|파운데이션|agent|model|llm|gpt|claude|robot|open-?source|inference|copilot|coding)", re.I)
KO_STOP = {"단독", "속보", "종합", "포토", "인터뷰", "기자수첩", "사설", "칼럼", "기고",
           "AI", "인공지능", "모두의 AI", "세계 최초", "국내 최초", "최초", "1위", "이란"}


def norm(t):
    t = re.sub(r"[\s\-_·.]", "", t).lower()
    return t


def extract(title):
    """제목 -> {정규화: 원표기}"""
    out = {}
    if not title:
        return out
    for m in QUOTE_RE.finditer(title):
        s = m.group(1).strip()
        if len(s.split()) > 2 or s in KO_STOP or re.fullmatch(r"[\d\s.,%조억원달러]+", s):
            continue
        if re.search(r"[가-힣]", s):
            if re.search(r"(다|듯|요|까|니|죠|네|라|자|면|고)$", s) and len(s) > 3:
                continue           # 따옴표 안 서술구('대형 히트작 될듯')는 이름이 아니다
            out[norm(s)] = s
        elif re.search(r"[A-Z]", s):   # 영문 인용구는 대문자가 있어야 이름으로 본다('insane' 제외)
            out[norm(s)] = s
    for m in EN_RE.finditer(title):
        s = m.group(1).strip()
        words = [w for w in re.split(r"[ -]", s) if w and w not in EN_STOP]
        if not words:
            continue
        s = " ".join(words)
        if len(s) < 3 or re.fullmatch(r"[A-Z]{1,3}", s) or s.isdigit():
            continue
        out[norm(s)] = s
    return out


def titles_from_tier2(day):
    p = os.path.join(OUT, "tier2-%s.json" % day)
    if not os.path.exists(p):
        return []
    try:
        return [(it.get("title") or "", it.get("outlet") or "?", "gnews")
                for it in json.load(open(p, encoding="utf-8")).get("items", [])]
    except Exception:
        return []


def titles_from_json(name, day, src):
    p = os.path.join(OUT, "%s-%s.json" % (name, day))
    if not os.path.exists(p):
        return []
    try:
        d = json.load(open(p, encoding="utf-8"))
        items = d.get("items", []) if isinstance(d, dict) else d
        return [(it.get("title") or it.get("summary") or "", it.get("outlet") or src, src)
                for it in items]
    except Exception:
        return []


def titles_from_rss(lo, hi):
    if not os.path.exists(DB):
        return []
    try:
        conn = sqlite3.connect(DB)
        rows = conn.execute("SELECT title, outlet, substr(published,1,10) FROM articles "
                            "WHERE published IS NOT NULL").fetchall()
        conn.close()
    except Exception:
        return []
    return [(t, o, "rss") for t, o, pd in rows if pd and lo <= pd <= hi]


def corpus_text(day, back=40, gap=11):
    """D-40~D-11 제목 전체를 소문자 한 덩어리로. 낱말의 '기존 등장 횟수'를 센다.
    (추출한 낱말 집합으로 비교하면 따옴표 없이 쓰인 일반명사(확산·전망·트럼프)가
     '없던 낱말'로 찍힌다 — 09-23 백테스트에서 그 오탐이 상위를 다 차지했다.)"""
    d = datetime.date.fromisoformat(day)
    lo = (d - datetime.timedelta(days=back)).isoformat()
    hi = (d - datetime.timedelta(days=gap)).isoformat()
    buf = []
    for p in glob.glob(os.path.join(OUT, "tier2-*.json")):
        m = re.search(r"tier2-(\d{4}-\d{2}-\d{2})\.json$", p)
        if not m or not (lo <= m.group(1) <= hi):
            continue
        buf.extend(t for t, _, _ in titles_from_tier2(m.group(1)))
    buf.extend(t for t, _, _ in titles_from_rss(lo, hi))
    return "\n".join(x.lower() for x in buf), len(buf)


def term_re(term_lower):
    """낱말 경계. 영문은 양쪽, 한글은 **앞쪽만** 본다(조사가 바로 붙으므로 '제브에'는
    맞고 '경제브리핑'은 아니다 — 09-23 실측: 뒤쪽 경계 없이 세니 '제브'가 코퍼스에
    '경제브리핑'으로 수십 번 찍혀 신규성에서 떨어졌다)."""
    if re.fullmatch(r"[a-z0-9 .\-]+", term_lower):
        return re.compile(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(term_lower))
    return re.compile(r"(?<![가-힣a-z0-9])%s" % re.escape(term_lower))


def corpus_count(text, term):
    return len(term_re(term.lower()).findall(text))


def occurs(term_lower, title_lower):
    return term_re(term_lower).search(title_lower) is not None


def pool(day):
    d = datetime.date.fromisoformat(day)
    lo = (d - datetime.timedelta(days=1)).isoformat()
    rows = titles_from_tier2(day) + titles_from_json("tier0", day, "tier0") \
        + titles_from_json("community", day, "community") + titles_from_rss(lo, day)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--min-outlets", type=int, default=2)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    day = a.date
    d = datetime.date.fromisoformat(day)

    corpus, ncorp = corpus_text(day)
    today = pool(day)
    window = {0: today}
    for i in range(1, 10):
        di = (d - datetime.timedelta(days=i)).isoformat()
        window[i] = titles_from_tier2(di) + titles_from_json("community", di, "community")

    stat = defaultdict(lambda: {"outlets": set(), "today": set(), "titles": [], "reaction": 0,
                                "community": 0, "days": set(), "term": None, "ai": False,
                                "first": None})
    for i in sorted(window, reverse=True):          # 오래된 날부터
        di = (d - datetime.timedelta(days=i)).isoformat()
        for t, o, src in window[i]:
            terms = extract(t)
            if not terms:
                continue
            low = t.lower()
            react = any(w in low for w in REACTION)
            ai = bool(AI_RE.search(t))
            for k, s in terms.items():
                st = stat[k]
                st["term"] = st["term"] or s
                st["first"] = st["first"] or di
                st["outlets"].add(o); st["days"].add(i)
                st["ai"] = st["ai"] or ai
                if react:
                    st["reaction"] += 1
                if src == "community":
                    st["community"] += 1
                if i == 0:
                    st["today"].add(o)
                    if len(st["titles"]) < 6:
                        st["titles"].append("%s | %s" % (o, t[:80]))
                elif len(st["titles"]) < 3:
                    st["titles"].append("%s(%s) | %s" % (o, di[5:], t[:80]))

    # 2차 집계: 1차에서 걸린 낱말만 골라 **따옴표 없는 표기까지** 부분일치로 다시 센다.
    #   09-21 '제브 공개 일주일 만에 오픈소스 구현 잇따라'는 따옴표가 없어 1차에서
    #   안 잡혔다. 낱말이 정해진 뒤에는 substring 으로 세는 것이 맞다.
    cand = [k for k, st in stat.items()
            if st["today"] and st["ai"] and (st["reaction"] >= 1 or st["community"] >= 1
                                             or (len(st["days"]) >= 3 and len(st["outlets"]) >= 4))]
    if a.verbose:
        print("[buzz] 1차 후보 %d낱말 -> 신규성 검사" % len(cand))
    novel = []
    for k in cand:
        st = stat[k]
        raw = st["term"]
        if len(raw) < 2 or (re.fullmatch(r"[A-Za-z]+", raw) and len(raw) <= 3):
            continue
        if corpus_count(corpus, raw) > 1:      # D-11 이전에 이미 쓰이던 낱말
            continue
        novel.append(k)
    cand = novel
    for k in cand:
        st = stat[k]
        raw = st["term"]
        rl = raw.lower()
        st2 = {"outlets": set(), "today": set(), "days": set(), "reaction": 0, "community": 0,
               "titles": []}
        for i in sorted(window, reverse=True):
            di = (d - datetime.timedelta(days=i)).isoformat()
            for t, o, src in window[i]:
                low = (t or "").lower()
                if not occurs(rl, low):
                    continue
                st2["outlets"].add(o); st2["days"].add(i)
                if any(w in low for w in REACTION):
                    st2["reaction"] += 1
                if src == "community":
                    st2["community"] += 1
                if i == 0:
                    st2["today"].add(o)
                    if len(st2["titles"]) < 6:
                        st2["titles"].append("%s | %s" % (o, t[:80]))
                elif len(st2["titles"]) < 3:
                    st2["titles"].append("%s(%s) | %s" % (o, di[5:], t[:80]))
        for key in ("outlets", "today", "days", "reaction", "community", "titles"):
            st[key] = st2[key]

    found, spread_only = [], []
    for k in cand:
        st = stat[k]
        if not st["today"]:
            continue
        n_out = len(st["outlets"]); days = len(st["days"])
        react = st["reaction"]; comm = st["community"]
        row = {"term": st["term"], "norm": k, "n_outlets": n_out, "n_today": len(st["today"]),
               "outlets": sorted(st["outlets"])[:8], "days": days, "first": st["first"],
               "reaction": react, "community": comm, "titles": st["titles"]}
        # 반향의 정의 = 제3자 반응 또는 커뮤니티. 매체 수만 많은 것은 보도자료 살포이거나
        # 이미 주류가 된 이야기라(속도조절론 50매체) 여기서 올릴 이유가 없다.
        if react >= 1 or comm >= 1:
            row["score"] = (4 * min(react, 3) + (5 if comm else 0) + (3 if comm >= 3 else 0)
                            + min(n_out, 6) + min(days, 4))
            found.append(row)
        elif days >= 3 and n_out >= 4 and n_out < 25:
            row["score"] = min(n_out, 6) + min(days, 4)
            spread_only.append(row)

    found.sort(key=lambda x: (-x["score"], -x["n_outlets"]))
    found = found[:a.top]
    spread_only.sort(key=lambda x: (-x["score"], -x["n_outlets"]))
    spread_only = spread_only[:6]

    print("[buzz] %s  코퍼스 %d제목(D-40~D-11) / 오늘 풀 %d제목 / 반향 후보 %d건"
          % (day, ncorp, len(today), len(found)))
    for f in found:
        print("   %2d점  %-18s 첫등장 %s · 누적매체%d(오늘%d) · %d일 · 반응%d · 커뮤니티%d" % (
            f["score"], f["term"][:18], f["first"][5:], f["n_outlets"], f["n_today"],
            f["days"], f["reaction"], f["community"]))
        for t in f["titles"][:3]:
            print("          %s" % t)
    if spread_only:
        print("[buzz]   (참고) 반응·커뮤니티 없이 퍼지기만 한 낱말 — 컷 고정 안 함:")
        for f in spread_only:
            print("          %-18s 첫등장 %s · 누적매체%d · %d일" % (
                f["term"][:18], f["first"][5:], f["n_outlets"], f["days"]))
    if a.dry:
        return 0
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, "buzz-%s.json" % day)
    json.dump({"date": day, "terms": found, "spread_only": spread_only}, open(p, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("[buzz] 저장: %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
