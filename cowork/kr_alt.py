#!/usr/bin/env python3
"""발송본의 영문 기사마다 같은 사건의 국내 보도가 있는지 네이버로 찾는다. [2026-11-03 신설]

[왜] 11-03 사용자: *"오늘 유독 영문기사가 많은데 한국매체 보도가 없어서 그런거야? 아니면
  니가 제대로 못찾은거야??"* → 실측: 발송본 20건 중 영문 8건, 그중 6건(오라클 공모·메타
  회사채·알파벳 채권·오픈AI 1GW·주지사 모라토리엄·앤트로픽 RSI)은 네이버에 국내 보도가 있었다.
  원인 ① ② 삼성 링크 찾기를 WebSearch(미국 기준)로만 해 영문 링크가 tier0 로 들어왔고
       ② ④ 에서 tier0 링크를 '검증된 값'으로 보고 국문 우선 규칙을 다시 적용하지 않았다.

[무엇을] out/brief-<날짜>.md 에서 헤드라인에 한글이 거의 없는(30% 미만) 항목을 골라,
  그 항목의 '주요 내용'(한국어로 쓴 요약)에서 주체·숫자 낱말 3~4개로 질의를 만들어
  네이버 뉴스(발행일 창 3일)를 찾는다. 질의 낱말이 2개 이상 제목에 겹치는 국내 기사가
  있으면 후보로 찍는다.
  - 후보가 있는데 영문을 유지하려면 out/kralt-ok-<날짜>.txt 에 `<영문 URL> | <사유>` 를 적는다
    (예: 회사 공식 블로그·뉴스룸 원문, 국문 기사가 다른 날짜·다른 사건).
  - 후보가 있고 ok 기록이 없으면 종료코드 1. 국문 기사로 바꾼 뒤(후보목록 밖 링크 허용 (1))
    linkcheck 를 다시 돌린다.

  bash cowork/run.sh kr-alt            # 8단계 linkcheck 뒤·cats 앞. send 도 자동 호출(경고)
"""
import argparse
import datetime
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
import naver_api  # noqa: E402

HANGUL = re.compile(r"[가-힣]")
ITEM = re.compile(r"^\*\*\d+\\?\.\s*(.+?)\*\*\s*\((.+?),\s*\[원문보기\]\((https?://[^)]+)\)\)", re.M)
PARTICLE = re.compile(r"(으로|에서|에게|과의|와의|으로써|까지|부터|이며|하며|하고|에|의|은|는|이|가|을|를|과|와|도|로)$")
STOP = {"주요", "내용", "시사점", "추진", "공개", "발표", "착수", "약속", "계약", "체결", "규모", "최대", "최소",
        "동시", "위한", "통해", "대상", "관련", "가능", "기업", "업계",
        "가능한", "모델", "도구", "에이전트", "직후", "매우", "다음", "역량", "스스", "개발하", "비상시"}


def hangul_ratio(s):
    letters = [c for c in s if c.isalpha()]
    return (sum(1 for c in letters if HANGUL.match(c)) / len(letters)) if letters else 0.0


SYN = {"DC": "데이터센터", "AIDC": "데이터센터", "앤스로픽": "앤트로픽", "엔트로픽": "앤트로픽"}
VARIANTS = {"앤트로픽": ["앤트로픽", "앤스로픽", "Anthropic"], "오픈AI": ["오픈AI", "OpenAI"],
            "메타": ["메타", "Meta"], "알파벳": ["알파벳", "구글", "Alphabet"], "오라클": ["오라클", "Oracle"]}


def query_from(summary, head=""):
    toks = []
    for t in re.split(r"[\s,·'\"“”‘’()\[\]/…~]+", summary):
        t = t.strip(".:")
        if not re.search(r"\d", t):
            t = PARTICLE.sub("", t)
        t = SYN.get(t, t)
        if len(t) < 2 or t in STOP or t in toks or re.fullmatch(r"\d+(년|개|월|일|시)?", t):
            continue
        if HANGUL.search(t) or re.search(r"\d", t):
            toks.append(t)
        if len(toks) >= 8:
            break
    # 영문 헤드라인의 금액($20 Billion / €6 billion)을 국문 표기(200억달러 / 60억유로)로 보탠다
    for cur, num in re.findall(r"([$€])\s?(\d+(?:\.\d+)?)\s?(?:B\b|bn|billion|Billion)", head):
        v = float(num) * 10
        t = ("%g" % v) + ("억달러" if cur == "$" else "억유로")
        if t not in toks:
            toks.insert(1, t)
    return toks


def variants(subj):
    return VARIANTS.get(subj, [subj])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--stem", default="brief")
    ap.add_argument("--days", type=int, default=3)
    a = ap.parse_args()
    day = datetime.date.fromisoformat(a.date) if a.date else datetime.date.today()
    lo = day - datetime.timedelta(days=a.days - 1)
    path = os.path.join("out", "%s-%s.md" % (a.stem, day))
    if not os.path.exists(path):
        print("[kr-alt] 발송본 없음: %s" % path)
        sys.exit(2)
    text = open(path, encoding="utf-8").read()
    okpath = os.path.join("out", "kralt-ok-%s.txt" % day)
    ok = {}
    if os.path.exists(okpath):
        for ln in open(okpath, encoding="utf-8"):
            if "|" in ln:
                u, r = ln.split("|", 1)
                ok[u.strip()] = r.strip()
    items = list(ITEM.finditer(text))
    eng = []
    for m in items:
        head, outlet, url = m.group(1), m.group(2), m.group(3)
        if hangul_ratio(head) >= 0.3:
            continue
        tail = text[m.end():m.end() + 600]
        sm = re.search(r"주요 내용:\*\*\s*(.+)", tail)
        eng.append((head, outlet, url, sm.group(1).strip() if sm else ""))
    print("[kr-alt] 발송본 %d건 중 영문 헤드라인 %d건 — 국내 보도 확인" % (len(items), len(eng)))
    bad = 0
    for head, outlet, url, summ in eng:
        toks = query_from(summ, head)
        print("\n  [영문] %s (%s)" % (head[:70], outlet))
        if url in ok:
            print("        영문 유지 기록: %s" % ok[url])
            continue
        if len(toks) < 2:
            print("        [건너뜀] 주요 내용에서 질의 낱말을 못 만듦 — 손으로 run.sh find")
            continue
        subj, others = toks[0], toks[1:]
        hits = []
        queries = [subj + " " + t for t in others[:7]] + [" ".join(others[:2]), " ".join(toks[:3])]
        for q in queries:
            try:
                res = naver_api.search(q, display=20, sort="sim") or []
            except Exception as e:
                print("        [오류] 네이버 검색 실패: %s" % str(e)[:60])
                res = []
            for it in res:
                pd = naver_api._pubdate(it.get("pubDate"))
                if pd and not (lo <= pd <= day):
                    continue
                t = naver_api.strip_tags(it.get("title", ""))
                d = naver_api.strip_tags(it.get("description", ""))
                # 주체가 제목에 있고 다른 낱말 1개 이상(제목+요약) / 또는 주체 없이도 다른 낱말 2개 이상이 제목에
                subj_in = any(v in t for v in variants(subj))
                n_td = sum(1 for k in others if k in t + " " + d)
                n_t = sum(1 for k in others if k in t)
                if not ((subj_in and n_td >= 1) or n_t >= 2):
                    continue
                link = it.get("link") if "n.news.naver.com" in (it.get("link") or "") else (it.get("originallink") or it.get("link"))
                if link not in [h[1] for h in hits]:
                    hits.append((t, link, str(pd or "")))
            if len(hits) >= 3:
                break
        print("        질의 낱말: %s" % " ".join(toks))
        if not hits:
            print("        국내 보도 못 찾음 — 영문 유지")
            continue
        bad += 1
        print("        **국내 보도 있음 %d건** — 국문 우선(규칙 5)으로 바꾸거나 kralt-ok 에 사유를 적을 것" % len(hits))
        for t, link, d in hits[:3]:
            print("          - %s [%s]\n            %s" % (t[:70], d, link))
    print()
    if bad:
        print("[kr-alt] 국문으로 바꿀 수 있는 영문 기사 %d건 (종료코드 1)" % bad)
        print("[kr-alt]   바꾼 뒤 run.sh linkcheck 를 다시 돌린다. 유지하려면 %s 에 '<URL> | <사유>'" % okpath)
        sys.exit(1)
    print("[kr-alt] 통과 — 영문 기사는 모두 국내 보도가 없거나 유지 사유가 있음")


if __name__ == "__main__":
    main()
