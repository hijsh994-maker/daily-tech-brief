#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
이슈 타임라인 — 진행 중인 이야기의 '현재 위치'를 만든다.  [2026-09-14 신설]

[왜 만들었나]
  2026-09-14 사용자 지적: "이렇게 단발성 규칙으로는 또 중요하지 않은 기사를
  중요하게 판단할 수도 있고. 사람은 규칙으로 기사를 결정하는 게 아니다."

  그날 편집자가 뺀 4건 중 3건의 사유가 전부 같았다.
    - 모두의 AI SKT·KT : "선정 이후 발행된 기사 대비 새로운 내용이 거의 없다"
    - 앤트로픽 중국 증류 : "이전부터 많이 나왔고 계정 동원 외 새 내용 없음"
    - LG CNS 베라루빈  : "협력은 이전부터 있던 내용, 새 건 GPU 확보뿐"

  셋 다 **'이 사업/사건이 지금 어디까지 왔나'를 알아야만** 내릴 수 있는 판단이다.
  종전에 에이전트가 받던 것은 `[최근 발송분 제목]` 이라는 **5회분 평면 목록**뿐이라
  "선정 이후 무엇이 이미 알려졌나"를 알 방법이 없었다. 규칙을 더 얹어도 안 된다 —
  없는 것은 규칙이 아니라 **상태값**이었다.

[무엇을 하나]
  최근 N일(기본 30) 동안 **실제로 발송된 기사**를 사건 단위로 묶어
  진행 순서대로 늘어놓는다. 그리고 오늘 후보를 그 줄에 붙여
  "이 기사가 이 줄에 새 항목을 더하는가"를 눈으로 볼 수 있게 한다.

    ## 모두의 AI (6건, 09-04 → 09-13)
       09-04 에임인텔리전스, SKT '모두의 AI' 컨소시엄 합류…보안 고도화 담당
       09-07 SKT '수요'·KT '이음'·카카오 '편리'…모두의 AI 개발 시동
       09-08 '모두의 AI' 1700억 GPU 지원금, 서비스 성과 맞춰 3社에 차등배분
       ...
       >> 오늘 후보: [3] KT, '이음 에이전트'로… / [263] 실행형 AI VS 연결형 AI…

  판정할 때 규칙표를 훑지 말고 **이 줄을 보고 한 문장으로 답한다** —
  "이 기사가 이 줄에 더하는 새 사실은 무엇인가." 못 쓰면 '고'가 아니다.

[입력]
  - truth-YYYY-MM-DD.json            사람이 발송한 최종본(2026-09-10 이 마지막)
  - out/brief-YYYY-MM-DD.md          에이전트 발송본(09-11 이후는 이쪽이 정본)
  - out/candidates-blind-<날짜>.txt   오늘 후보(있으면 줄에 붙인다)

[출력]
  out/timeline-<날짜>.md

[주의]
  이 파일은 **판정 보조 자료**이지 등급을 정하지 않는다.
  줄이 길다고 고가 아니고, 새 줄이라고 고가 아니다.
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "cowork"))

try:
    from dedup_prev import _orgs, _bigrams, _canon  # noqa
except Exception:                                    # pragma: no cover
    _orgs = lambda s: set()                          # noqa: E731
    _bigrams = lambda s: set()                       # noqa: E731
    _canon = lambda s: s                             # noqa: E731

# 사건을 붙일지 가르는 임계값.
#   - 조직명 2개 이상 공유       -> 같은 줄 (dedup_prev 의 소프트 신호와 같은 근거)
#   - 제목 2-gram 유사도 0.30 이상 -> 같은 줄
# dedup_prev 의 중복 컷(0.55)보다 **훨씬 낮게** 둔다. 여기서는 '같은 기사'가
# 아니라 '같은 이야기'를 묶는 것이라 넓게 잡아야 한다. 잘못 묶여도 손해가
# 작다(눈으로 보고 무시하면 된다). 반대로 못 묶으면 이 도구는 쓸모가 없다.
SIM_JOIN = 0.30
MIN_SHARED_ORGS = 2

# 줄 이름에 쓰지 않을 흔한 말. 주제명이 '공개'·'출시'가 되면 안 된다.
STOP = set("""
AI 인공지능 관련 기술 산업 시장 기업 서비스 사업 개발 공개 출시 도입 확대 강화
추진 발표 진행 계획 전망 예정 위해 통해 최초 세계 국내 글로벌 미국 한국 중국
단독 인터뷰 분석 종합 오늘 내년 올해 지난 대한 하는 있다 한다 된다 밝혀 나선
THE AND FOR WITH FROM THAT THIS INTO OVER AFTER ITS HAS ARE WAS NOT NEW
IN TO OF ON AT BY AS IS IT BE OR AN A US UP OUT WHO WHY HOW ALL MORE
""".split())

RE_BRIEF_ITEM = re.compile(r"^\*{0,2}(\d+)\\?\.\s*(.+?)\*{0,2}\s*\((.+?),\s*\[원문보기\]\((.*?)\)\)\s*$")
RE_BLIND = re.compile(r"^(N?\d+) \| ([^|]+)\|([^|]+)\|([^|]+)\|([^|]+)\| (.*)$")


def log(m):
    print("[timeline] %s" % m)


def _clean_title(s):
    """blind 목록의 꼬리표를 뗀다. '[링크없음]' 을 남기면 그 자체가
    가장 흔한 '특징 낱말'이 되어 묶음 이름이 전부 '링크없음'이 된다."""
    s = re.sub(r"\s*\[중복의심:.*$", "", s)
    s = re.sub(r"\s*\[링크없음\]\s*", " ", s)
    return s.strip().strip("[]").strip()


# ---------------------------------------------------------------- 입력 읽기
def load_truth(day):
    p = os.path.join(BASE, "truth-%s.json" % day)
    if not os.path.exists(p):
        return None
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        return None
    out = []
    for it in d.get("items", []):
        t = (it.get("title") or "").strip()
        if t:
            out.append({"date": day, "title": t, "src": "사람", "sent": True})
    return out or None


def load_brief(day):
    p = os.path.join(BASE, "out", "brief-%s.md" % day)
    if not os.path.exists(p):
        return None
    out = []
    for line in open(p, encoding="utf-8"):
        m = RE_BRIEF_ITEM.match(line.rstrip())
        if not m:
            continue
        t = m.group(2).strip().strip("*").strip()
        if t:
            out.append({"date": day, "title": t, "src": "에이전트", "sent": True})
    return out or None


def load_pool(day, days, top=120):
    """[2026-09-14 저녁 신설] 발송은 안 됐지만 **후보로는 올라왔던** 기사.

    왜 필요한가 — 사용자 지적:
      "'엔비디아 대항마' 리벨리온, 젠슨 황 손잡나 이 기사는 며칠 전부터
       유사한 내용의 기사들이 계속 나왔었기 때문에 이번에 후보로 올렸어도
       내가 잘랐을 거야. 철 지난 기사들은 빼는 센스도 있어야 한다."

    실측: 그 사건은 09-08(한국경제, tier0 사람큐레이션)·09-09 에 이미 후보로
    올라왔고 발송되지 않았다. **타임라인을 발송분으로만 만들면 이런 줄이
    통째로 안 보인다.** 발송 여부와 무관하게 '보도가 며칠째 돌고 있는지'가
    신규성 판단의 재료이므로 후보 풀도 같이 읽는다.

    점수 상위 top 건만 쓴다 — 후보 전량(하루 500건)을 넣으면 줄이 잡음에
    묻힌다. 발송분과 구분되게 `sent=False` 로 표시한다.
    """
    d0 = datetime.date.fromisoformat(day)
    out = []
    for k in range(1, days + 1):
        d = (d0 - datetime.timedelta(days=k)).isoformat()
        p = os.path.join(BASE, "out", "refined-%s.json" % d)
        if not os.path.exists(p):
            continue
        try:
            items = (json.load(open(p, encoding="utf-8")) or {}).get("items") or []
        except Exception:
            continue
        items = sorted(items, key=lambda x: -(x.get("score") or 0))[:top]
        for it in items:
            ti = (it.get("title") or "").strip()
            if ti:
                out.append({"date": d, "title": ti, "src": "후보", "sent": False})
    return out


def load_sent(day, days):
    """최근 days 일치 발송분. 같은 날 truth 가 있으면 truth 우선(사람 발송본)."""
    d0 = datetime.date.fromisoformat(day)
    items = []
    for k in range(1, days + 1):
        d = (d0 - datetime.timedelta(days=k)).isoformat()
        got = load_truth(d) or load_brief(d)
        if got:
            items.extend(got)
    items.sort(key=lambda x: x["date"])
    return items


def load_candidates(day):
    p = os.path.join(BASE, "out", "candidates-blind-%s.txt" % day)
    if not os.path.exists(p):
        return []
    out, seen = [], set()
    for line in open(p, encoding="utf-8"):
        m = RE_BLIND.match(line.rstrip())
        if not m:
            continue
        idx = m.group(1)
        if idx in seen:
            continue
        seen.add(idx)
        out.append({"idx": idx, "route": m.group(2).strip(),
                    "title": _clean_title(m.group(6))})
    return out


# ------------------------------------------------------------------ 묶기
#
# [2026-09-14 1차 시도 실패 기록 — 같은 실수를 반복하지 말 것]
#   처음에는 dedup_prev 처럼 '조직명 2개 공유'로 묶었다. 결과는 쓸모가 없었다.
#   `앤트로픽 · 오픈AI` 한 줄에 발송분 67건 + 오늘 후보 97건이 들어갔다.
#   이유 둘 - (1) 오픈AI·구글·앤트로픽은 **매일 나오는 이름**이라 조직명 공유가
#   신호가 아니다. (2) 최근 멤버와 비교하며 이어 붙이면 **사슬 병합**이 일어나
#   A-B, B-C 만으로 A 와 C 가 한 줄이 된다.
#   그래서 아래 셋으로 바꿨다.
#     - 흔한 낱말은 IDF 로 죽인다(30일 문서의 15% 넘게 나오면 특징이 아니다)
#     - 최근 멤버가 아니라 **줄의 서명(signature)** 과 비교한다(사슬 차단)
#     - 줄이 커질수록 붙는 조건을 올린다

# [2026-09-14 2차 수정] '공유 낱말 2개'라는 개수 기준을 버리고 **IDF 가중합**을
#   쓴다. 개수로 세면 희귀한 낱말 하나가 흔한 낱말 둘보다 약하게 취급된다.
#   실측: '리벨리온'(전체 1796건 중 3건)은 그 자체로 사건을 특정하는데,
#   '엔비디아'는 너무 흔해 잘려 나가 결국 공유 낱말이 1개가 되고 안 붙었다.
#   그래서 09-08 한국경제 「'AI 독립' 외치는 리벨리온에… 엔비디아는 M&A 손
#   내밀었다」와 09-14 「리벨리온, 젠슨 황 손잡나」가 다른 줄로 갈렸다.
DF_MAX_RATIO = 0.15      # 이 비율 넘게 나오는 낱말은 아예 안 본다
JOIN_IDF = 6.0           # 공유 낱말들의 IDF 합이 이 값을 넘으면 같은 줄
                         #   df=3  -> 6.4 (희귀어 하나로 통과)
                         #   df=40 -> 3.8 (둘이면 7.6 통과)
                         #   df=200-> 2.2 (둘이어도 4.4 미달)
SIM_STRONG = 0.40        # 또는 제목 2-gram 유사도가 이만큼이면 같은 줄
BIG_STORY = 8            # 줄이 이보다 커지면 문턱을 올린다
BIG_BONUS = 2.0
SATURATE = 25            # 이만큼 커진 줄은 더 받지 않는다.
                         #   [2026-09-14 5차] 영문 제목끼리 BILLION·NVIDIA·DATA
                         #   같은 낱말을 공유해 한 줄이 240건까지 불어났다.
                         #   240건짜리 '이야기'는 어차피 판정에 못 쓴다.
                         #   문턱을 더 만지는 것보다 상한을 두는 편이 안전하다.


# 한국어 조사·어미. [2026-09-14 7차 — 진짜 원인은 임계값이 아니라 이것이었다]
#   `리벨리온에` 와 `리벨리온`, `엔비디아는` 과 `엔비디아` 가 서로 다른 낱말로
#   잡혀서, 09-08 「'AI 독립' 외치는 리벨리온에 … 엔비디아는 M&A 손 내밀었다」가
#   08-24 「엔비디아, 리벨리온과 인수 협상 중」과 다른 줄로 갈렸다.
#   그래서 사용자가 "며칠 전부터 계속 나온 기사"라고 한 것을 못 봤다.
#   임계값을 여섯 번 고치는 동안 이걸 못 본 것이 오늘의 진짜 실수다.
JOSA = ("에서", "으로", "에게", "한테", "부터", "까지", "보다", "처럼", "라며",
        "이라", "과의", "와의", "의", "은", "는", "이", "가", "을", "를",
        "에", "로", "와", "과", "도", "만", "및")


def _strip_josa(w):
    if len(w) < 3:
        return w
    for j in JOSA:
        if w.endswith(j) and len(w) - len(j) >= 2:
            return w[: -len(j)]
    return w


def keywords(title):
    """제목에서 뽑은 낱말 집합. 조사·기호를 털고 STOP 을 뺀다."""
    ws = re.findall(r"[가-힣]{2,}|[A-Za-z][A-Za-z0-9.\-]{1,}", title)
    out = set()
    for w in ws:
        w = w.strip(".-")
        if re.match(r"^[가-힣]", w):
            w = _strip_josa(w)
        if len(w) < 2 or w in STOP:
            continue
        out.add(w.upper() if re.match(r"^[A-Za-z]", w) else w)
    return out


def build_df(items):
    from collections import Counter
    df = Counter()
    for it in items:
        for w in keywords(it["title"]):
            df[w] += 1
    return df


def build_stories(items):
    """줄 서명과 비교하는 응집 군집. IDF 가중합으로 붙일지 정한다."""
    import math
    df = build_df(items)
    n = max(1, len(items))
    cap = max(2, int(n * DF_MAX_RATIO))

    def key(title):
        return {w for w in keywords(title) if df.get(w, 0) <= cap}

    def idf(w):
        return math.log(float(n) / max(1, df.get(w, 1)))

    def weight(shared):
        return sum(idf(w) for w in shared)

    stories = []
    for it in items:
        k = key(it["title"])
        best, best_score = None, 0.0
        for s in stories:
            if len(s["items"]) >= SATURATE:
                continue
            need = JOIN_IDF + (BIG_BONUS if len(s["items"]) >= BIG_STORY else 0)
            sc = weight(k & s["sig"])
            if sc >= need:
                if sc > best_score:
                    best, best_score = s, sc
                continue
            # 특징 낱말이 모자라도 제목 자체가 아주 비슷하면 같은 사건이다.
            # [2026-09-14 4차] 단 **공통 특징 낱말이 최소한 하나는 있어야** 한다.
            #   글자 2-gram 만으로 열어 두었더니 영문 제목끼리 흔한 철자가 겹쳐
            #   `BILLION · ANTHROPIC · NVIDIA` 한 줄이 295건까지 불어났다.
            for m in s["items"][-4:]:
                ba, bb = _bigrams(it["title"]), _bigrams(m["title"])
                if not (ba and bb):
                    continue
                if len(ba & bb) / float(min(len(ba), len(bb))) < SIM_STRONG:
                    continue
                if weight(k & key(m["title"])) < JOIN_IDF * 0.5:
                    continue
                if best_score < 0.5:
                    best, best_score = s, 0.5
                break
        if best is not None:
            best["items"].append(it)
            # 서명은 넓히지 않고 **좁힌다**. 새 멤버와 공통인 것만 남겨
            # 줄이 커질수록 주제가 흐려지는 것을 막는다.
            # [2026-09-14 3차] 서명은 **조건 없이 좁힌다.** 종전에는 너무 좁아지면
            #   옛 서명을 유지했는데, 그 탓에 서명이 넓은 채로 남아 한 줄이
            #   285건까지 불어났다. 좁혀서 더 못 붙으면 그게 맞는 동작이다.
            best["sig"] = best["sig"] & k
        else:
            stories.append({"items": [it], "sig": k})
    return stories


def story_name(s):
    """줄 이름 = 서명(특징 낱말) 중 그 줄에서 자주 쓰인 것 3개.

    [2026-09-14] 처음에는 조직명을 앞세웠는데 `앤트로픽 · 오픈AI` 같은 이름이
    여러 줄에 똑같이 붙어 구분이 안 됐다. **줄을 가른 근거는 서명이므로
    이름도 서명에서 뽑는다.**
    """
    from collections import Counter
    word = Counter()
    for it in s["items"]:
        for w in keywords(it["title"]):
            word[w] += 1
    sig = s.get("sig") or set()
    ranked = sorted(word.items(), key=lambda kv: (kv[0] in sig, kv[1]), reverse=True)
    parts = []
    for w, _c in ranked:
        if len(w) < 2 or w in STOP:
            continue
        if any(w in p or p in w for p in parts):
            continue
        parts.append(w)
        if len(parts) >= 3:
            break
    return " · ".join(parts) if parts else s["items"][0]["title"][:24]


# ------------------------------------------------------------------ 출력
def biz_block():
    """docs/사업현황.md 의 대조표를 타임라인 맨 앞에 붙인다.

    [2026-09-14] 왜 별도 파일로 두지 않고 여기 끼워 넣나 —
    그날 `bodygo` 로 본문을 받아 놓고 한 건도 안 읽은 전례가 있다.
    **읽어야 하는 것은 읽는 파일 안에 있어야 한다.** 판정 전에 여는 파일은
    타임라인 하나이므로 여기에 합친다.
    """
    p = os.path.join(BASE, "docs", "사업현황.md")
    if not os.path.exists(p):
        return ["> [사업현황] docs/사업현황.md 가 없습니다. 질문지를 먼저 채워야 합니다.", ""]
    txt = open(p, encoding="utf-8").read()
    keep, on = [], False
    for line in txt.splitlines():
        if line.startswith("## "):
            on = not line.startswith("## 판정할 때")
        if on:
            keep.append(line)
    L = ["# 사업현황 대조표 (docs/사업현황.md 요약 — 판정 전 필독)", ""]
    L.append("**타임라인으로 '새로운 게 뭔가'를 보고, 아래로 '그게 우리한테 뭔가'를 본다.**")
    L.append("**둘 다 답이 나와야 '고'다.** 걸리면 사유에 `사업현황: <줄 이름>` 을 적는다.")
    L.append("")
    L.extend(keep)
    L.append("")
    L.append("=" * 70)
    L.append("")
    return L


def render(day, days, stories, cands, attach):
    L = biz_block()
    L.append("# 이슈 타임라인 %s (최근 %d일 발송분)" % (day, days))
    L.append("")
    L.append("판정 전에 **이것부터 읽는다.** 규칙표는 그 다음이다.")
    L.append("각 줄은 '이미 알려진 것'이다. 오늘 후보를 볼 때 물을 것은 하나 —")
    L.append("**이 기사가 이 줄에 더하는 새 사실을 한 문장으로 쓸 수 있는가.**")
    L.append("못 쓰면 '고'가 아니다(2026-09-14 편집자 피드백).")
    L.append("")
    L.append("줄 앞의 `·` 는 **후보로만 올랐고 발송되지 않은** 기사다.")
    L.append("**[2026-09-30 사용자 지시] `·` 는 독자가 본 것이 아니다.** 내가 후보로 본 것일 뿐이다.")
    L.append("`·` 만 여러 날 이어진 이야기는 독자 기준으로 **첫 게재**다. 신규성으로 깎지 않는다.")
    L.append("비교할 ‘이미 알려진 것’은 `·` 없는 줄(실제 발송분)뿐이다.")
    L.append("")
    multi = [s for s in stories if len(s["items"]) >= 2 or s.get("today")]
    multi.sort(key=lambda s: (bool(s.get("today")), len(s["items"])), reverse=True)
    L.append("진행 중인 이야기 %d줄 / 오늘 후보가 붙은 줄 %d개"
             % (len(multi), sum(1 for s in multi if s.get("today"))))
    L.append("")
    for s in multi:
        its = s["items"]
        head = "## %s  (%d건" % (story_name(s), len(its))
        if its:
            head += ", %s → %s" % (its[0]["date"][5:], its[-1]["date"][5:])
        head += ")"
        L.append(head)
        for it in its[-10:]:
            mark = "  " if it.get("sent") else "· "   # '·' = 후보로만 올랐고 발송 안 됨
            L.append("   %s%s %s" % (mark, it["date"][5:], it["title"]))
        if s.get("today"):
            L.append("   >> **오늘 후보 %d건** — 이 줄에 새 항목을 더하는가?" % len(s["today"]))
            for c in s["today"][:10]:
                L.append("      [%s] %s  (%s)" % (c["idx"], c["title"], c["route"]))
        L.append("")
    if attach["new"]:
        L.append("# 오늘 처음 나온 묶음 (진행 중인 이야기에 안 붙는 후보 %d건)"
                 % len(attach["new"]))
        L.append("")
        L.append("**여기는 한 건씩 등급을 찍지 말고 묶음째로 비교한다.**")
        L.append("같은 묶음 안에서 '오늘 이 중 하나만 싣는다면 어느 것인가'를 먼저")
        L.append("정하고, 그 다음에 그 하나가 발송본에 들어갈 값을 하는지 본다.")
        L.append("(2026-09-14 사용자 지적: 한 건씩 분류하면 트레이드오프가 숨는다)")
        L.append("")
        groups = attach["newgroups"]
        multi = [g for g in groups if len(g["items"]) >= 2]
        solo = [g for g in groups if len(g["items"]) == 1]
        for g in multi:
            L.append("## [새] %s  (후보 %d건)" % (group_name(g), len(g["items"])))
            for c in g["items"]:
                L.append("      [%s] %s  (%s)" % (c["idx"], c["title"], c["route"]))
            L.append("")
        if solo:
            L.append("## [새·단건] 묶이지 않은 후보 %d건" % len(solo))
            for g in solo:
                c = g["items"][0]
                L.append("      [%s] %s  (%s)" % (c["idx"], c["title"], c["route"]))
            L.append("")
    return "\n".join(L) + "\n"


def group_name(g):
    from collections import Counter
    word = Counter()
    for c in g["items"]:
        for w in keywords(c["title"]):
            word[w] += 1
    parts = []
    for w, _c in word.most_common(12):
        if len(w) < 2 or w in STOP:
            continue
        if any(w in p or p in w for p in parts):
            continue
        parts.append(w)
        if len(parts) >= 3:
            break
    return " · ".join(parts) if parts else g["items"][0]["title"][:24]


def group_new(cands):
    """줄에 안 붙은 오늘 후보끼리 다시 묶는다. 주제별 비교를 하려면 필요하다."""
    items = [{"date": "", "title": c["title"], "_c": c} for c in cands]
    stories = build_stories(items)
    out = []
    for s in stories:
        out.append({"items": [m["_c"] for m in s["items"]], "sig": s.get("sig", set())})
    out.sort(key=lambda g: len(g["items"]), reverse=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--days", type=int, default=30)
    a = ap.parse_args()

    sent = load_sent(a.date, a.days)
    pool = load_pool(a.date, a.days)
    if not sent and not pool:
        log("최근 %d일 발송분·후보를 못 찾았습니다 - 건너뜁니다" % a.days)
        return 0
    corpus = sorted(sent + pool, key=lambda x: x["date"])
    stories = build_stories(corpus)

    cands = load_candidates(a.date)
    df = build_df(corpus)
    cap = max(2, int(len(corpus) * DF_MAX_RATIO))
    new = []
    for c in cands:
        k = {w for w in keywords(c["title"]) if df.get(w, 0) <= cap}
        best, best_score = None, 0
        for s in stories:
            sh = sum(__import__("math").log(float(len(corpus)) / max(1, df.get(w, 1)))
                     for w in (k & s["sig"]))
            if sh >= JOIN_IDF and sh > best_score:
                best, best_score = s, sh
        if best is not None:
            best.setdefault("today", []).append(c)
        else:
            new.append(c)

    txt = render(a.date, a.days, stories, cands,
                 {"new": new, "newgroups": group_new(new)})
    outp = os.path.join(BASE, "out", "timeline-%s.md" % a.date)
    open(outp, "w", encoding="utf-8").write(txt)
    hit = sum(len(s.get("today", [])) for s in stories)
    log("발송분 %d + 과거후보 %d = %d건 -> 이야기 %d줄 (2건 이상 %d줄)"
        % (len(sent), len(pool), len(corpus), len(stories),
           sum(1 for s in stories if len(s["items"]) >= 2)))
    log("오늘 후보 %d건 중 %d건이 진행 중인 이야기에 붙음 / 새 줄 %d건"
        % (len(cands), hit, len(new)))
    log("저장: %s" % outp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
