# -*- coding: utf-8 -*-
"""
scorecard.py - 정답지 대조 결과를 하루 한 줄씩 append-only 로 쌓는다.

## 왜 필요한가
backtest.py 는 채점 결과를 stdout 에만 찍는다. 그래서
  (1) 내가 사건 단위로 다시 센 값(URL 일치는 하한값이다)
  (2) 놓친 기사가 '왜' 놓쳤는지의 판단
이 둘이 대화창과 함께 사라진다. 세션이 바뀌면 처음부터 다시 판단하게 되고,
어제 고친 편향을 오늘 또 반복한다. 실제로 08-03 -> 08-04 에 그랬다.

그래서 하루 한 줄 JSONL 로 남긴다. 스키마는 logs/scorecard.jsonl 참조.

## 놓친 사유를 4가지로 나누는 이유
고칠 곳이 각각 다르기 때문이다. 뭉뚱그리면 어디를 손볼지 알 수 없다.

  수집실패     원시 수집물에도 없음        -> tier2 쿼리 / RSS 피드를 늘린다
  발행일 창 밖 탈락  tier0/1/2 엔 있으나 refine 창 밖 -> datewin 을 손본다
  링크미확보   ranked 엔 있으나 링크 없음  -> refine 의 링크 해결을 손본다
  채점 결과 후보에서 탈락   링크는 있으나 점수 컷 밖   -> min-score / max-items 를 손본다
  LLM 선별 후보에서 탈락   후보로 다 보였는데 안 골랐음 -> brief.py 편집 방침을 손본다

앞의 4개는 ranked/refined 산출물만 있으면 기계가 판정한다.
사람(=에이전트) 판단이 필요한 건 'LLM 선별 후보에서 탈락'의 세부 유형뿐이다.

## 사건 단위 일치를 기계가 세는 방법
refine 은 같은 사건의 여러 기사를 한 클러스터(member_urls)로 묶는다.
정답지 URL 과 내 브리프 URL 이 같은 클러스터에 있으면 '같은 사건을 다른 매체로
실은 것'이다. 지금까지 눈으로 세던 걸 이걸로 대신한다.
(클러스터링이 놓친 쌍은 여전히 못 잡으므로 --annotate 로 손보정할 수 있다.)
"""
import argparse
import datetime
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(BASE, "out")
LOGDIR = os.path.join(BASE, "logs")
LEDGER = os.path.join(LOGDIR, "scorecard.jsonl")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from match import match_all                                        # noqa: E402
from match import title_sim as M_title_sim                         # noqa: E402

# 버킷 판정(=놓친 사유 분류)에서 제목을 같은 사건으로 볼 임계값.
#
# match_all 의 일치 판정은 0.28 이지만 여기서는 더 높게 잡는다. match_all 은
# IDF 가중치와 1:1 배정으로 우연한 충돌을 눌러주는데, 버킷 판정은 '이 풀
# 안에 비슷한 제목이 하나라도 있나'라 그 보호장치가 없다.
#   실측 08-18: 0.28 에서 '씨티그룹 2029년 2.8조달러 전망'(정답지)이
#   '장부에는 안 보여…빅테크 AI 지출 3조달러 더 많아'(내 후보)에 0.29 로
#   붙어, 수집조차 안 된 건이 'LLM 선별 후보에서 탈락'으로 찍혔다.
#   사유가 파이프라인 문제에서 편집 판단 문제로 뒤집히는 오분류다.
BUCKET_THR = 0.35

# [2026-09-01] tier1(상시 적재 sqlite)을 '오늘의 수집 창고'로 볼 때의 날짜 폭(일).
#   refine 의 발행일 창(1~2일)보다 넓어야 '창밖 탈락'을 잡아낼 수 있고,
#   그렇다고 무제한이면 몇 주 전 같은 회사 기사에 붙어 오탐이 난다.
#   _raw_bucket() 주석 참조.
RAW_TIER1_WIN = 7

# 원시 수집 풀(tier0+tier1 sqlite+tier2)은 제목이 1만 6천 개가 넘어
# 무관한 제목도 0.33~0.35 까지 올라온다. 그래서 더 높게 잡는다.
#   실측 08-18: 정답인 'Grok 4.6 in GitHub Copilot' 은 0.40 이고
#   2위가 0.22 로 뚝 떨어진다. 반면 오탐들은 0.33~0.35 에 몰려 있다.
RAW_THR = 0.40

# 사유 이름. 사람이 리포트에서 바로 읽고 어느 단계에서 빠졌는지 알 수 있게
# 단계 이름을 그대로 쓴다(2026-08-04 사용자 지시).
#   앞의 3개는 파이프라인 문제, R_DROP 만 편집 판단 문제다.
R_COLLECT = "수집실패"                     # 애초에 안 잡힘
R_WINDOW = "발행일 창 밖 탈락"               # 수집은 됐으나 refine 날짜창에서 잘림
R_NOLINK = "링크미확보"                    # 잡혔으나 링크를 못 찾음
R_CUT = "채점 결과 후보에서 탈락"             # 기계 점수 컷에 걸림
R_DROP = "LLM 선별 후보에서 탈락"            # 후보엔 있었는데 최종 선별에서 뺌
R_EXPOSED = "노출됨"                       # 내 브리프가 없는 날(백필)용 중립 라벨
REASONS = (R_COLLECT, R_WINDOW, R_NOLINK, R_CUT, R_DROP, R_EXPOSED)

# 예전 이름 -> 새 이름. 이미 쌓인 기록을 읽을 때 갈아끼운다.
# 이걸 빼면 이름을 바꾼 날 이전 기록이 추이 표에서 다른 사유로 갈라진다.
RENAME = {"컷탈락": R_CUT, "내가버림": R_DROP}

# 기록 시작일. 이전 날짜는 적재하지 않는다.
#   07-30 이전은 파이프라인이 초기 상태라(tier0 없음, 입력량 1/3, 산출물 결손)
#   사유 판정이 '?' 아니면 '노출됨'으로만 찍혀 추이를 읽는 데 방해가 된다.
#   깨끗한 산출물이 남기 시작한 07-31 부터 센다. (2026-08-04 사용자 지시)
START = "2026-07-31"


def norm_url(u):
    u = (u or "").strip().split("#")[0]
    u = re.sub(r"^https?://", "", u, flags=re.I)
    u = re.sub(r"^www\.", "", u, flags=re.I)
    u = re.sub(r"[?&]utm_[^&]*", "", u)
    return u.rstrip("/&?").lower()


# --- 제목으로 '같은 사건'을 재는 자 -------------------------------------
# URL 일치만 세면 하한값이다(실측 08-03: URL 4/17, 눈으로 세면 12/17).
# 같은 사건을 다른 매체가 다른 제목으로 쓰기 때문이다. 그래서 제목의 문자
# 2-gram 포함도로 잰다. 형태소 분석기 없이 국문·영문을 함께 다루려면 이게
# 가장 덜 깨진다. 임계값 0.45 는 08-03 정답지로 맞췄다(높이면 아스트라·
# 위성사진 건을 놓치고, 낮추면 서로 다른 데이터센터 기사가 붙는다).
_STOP = re.compile(r"[^0-9a-zA-Z가-힣]+")

# [2026-08-04] 잔여 문자열이 짧으면 무조건 불일치로 본다.
#   _STOP 이 일본어·중국어를 통째로 지우기 때문에, 일본어 제목은 숫자만 남는다.
#   실제로 '創業振興院が2026年政策アイデア公募開始' 가 "2026" 만 남아
#   '2026년 「인간-AI협업형 LAM...」' 과 포함도 1.00 으로 붙었다.
#   비교 대상이 8-gram(=9글자) 미만이면 판단 근거가 없다고 보고 버린다.
MIN_GRAMS = 8


def bigrams(s):
    s = _STOP.sub("", (s or "").lower())
    if len(s) < 2:
        return set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def same_event(a, b, thr=0.45):
    ga, gb = bigrams(a), bigrams(b)
    if not ga or not gb:
        return False
    if min(len(ga), len(gb)) < MIN_GRAMS:
        return False
    return len(ga & gb) / float(min(len(ga), len(gb))) >= thr


def load_json(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return None


def parse_brief(day, stem="brief"):
    """내 브리프에서 (카테고리, 헤드라인, 매체, URL) 을 뽑는다.

    stem="brief20" 이면 상위 20건 추림본을 읽는다(2026-08-26 신설).
    양식이 같으므로 파서를 공유한다.

    [주의] 양식이 두 가지다. 07-31 이전은 볼드도 '원문보기' 링크도 없는 평문이라
      신양식 정규식으로는 0건이 나온다. 그러면 제목 기반 사건 매칭이 통째로
      죽어서 그날 성적이 실제보다 낮게 찍힌다(07-31: 5/12 로 기록됐으나 6/12).
      report.py 도 이 함수를 import 해서 쓴다 - 파서가 두 벌이면 또 어긋난다.
        신양식: **1\\. 헤드라인** (매체, [원문보기](url))
        구양식: 1. 헤드라인  ([매체](url), 원문보기)
    """
    p = os.path.join(OUTDIR, "%s-%s.md" % (stem, day))
    if not os.path.exists(p):
        return []
    out, cat = [], ""
    for line in open(p, encoding="utf-8"):
        m = re.match(r"\*{0,2}■\s*(.+?)\*{0,2}\s*$", line.strip())
        if m:
            cat = m.group(1).strip().rstrip("*")
            continue
        if "](http" not in line:
            continue
        urls = re.findall(r"\]\((https?://[^)]+)\)", line)
        if not urls:
            continue
        om = (re.search(r"\(([^(),\[\]]+),\s*\[원문", line)
              or re.search(r"\(\[([^\]]+)\]\(https?://", line))
        title = re.sub(r"^\s*\**\s*\d+\s*\\?\.\s*", "", line.split("(")[0])
        title = title.replace("**", "").strip()
        if title:
            out.append((cat, title, om.group(1).strip() if om else "", urls[0]))
    return out


# ------------------------------------------------------------------ 판정
def _bucket(refined, ranked):
    """산출물을 '어느 단계까지 살아남았나' 별로 나눈다.

    exposed  후보 목록에 실제로 실린 것(주 후보 + 구제)   -> 여기서 놓치면 내 판단 문제
    nolink   클러스터는 살았으나 링크를 못 찾아 빠진 것    -> 링크 해결 문제
    culled   수집·클러스터링은 됐으나 컷 밖               -> 점수/컷 문제
    (셋 다 아니면 수집 자체가 안 된 것)

    각 버킷은 (url집합, 제목목록) 쌍으로 돌려준다. 정답지 URL 이 안 맞아도
    제목으로 같은 사건을 찾아야 하기 때문이다.
    """
    def pack(items, with_members=True):
        urls, titles = set(), []
        for it in items or []:
            if it.get("url"):
                urls.add(norm_url(it["url"]))
            if with_members:
                for u in (it.get("member_urls") or []):
                    urls.add(norm_url(u))
            titles.append(it.get("title") or "")
            if with_members:
                titles.extend(it.get("members") or [])
        return urls, titles

    # [2026-08-04] 'exposed' 에는 링크가 있는 항목만 넣는다.
    #   brief.py 의 candidates() 가 url 없는 항목을 걸러내므로, 링크 없는
    #   refined.items 는 프롬프트의 주 후보에 실리지 않는다. 그런데 여기서
    #   같이 세고 있어서 '한 번도 안 보여준 기사'가 'LLM 선별 후보에서 탈락'
    #   으로 기록됐다. 실측 08-04: 'AI 환각 줄일 기술'이 그 경우였는데,
    #   실제로는 링크가 없어 [링크 미확보 후보] 하위로 밀려 컷된 건이었다.
    #   링크 없는 항목은 rescue_nolink 와 같은 취급(=링크미확보)으로 옮긴다.
    main = ((refined.get("items") or []) + (refined.get("own_track") or []) +
            (refined.get("rescue") or []))
    exposed = pack([x for x in main if x.get("url")])
    nolink = pack([x for x in main if not x.get("url")] +
                  (refined.get("rescue_nolink") or []))
    culled = pack(ranked.get("items") or [])
    return exposed, nolink, culled


def _raw_bucket(day):
    """refine 에 들어가기 **전**의 원시 수집물을 모은다.

    [2026-08-18] 종전에는 refined/ranked 만 보고 '셋 다 아니면 수집실패'로
    판정했다. 그런데 refine 은 발행일 창(datewin: 기본 1일, 전날이 공휴일이면 확대)에서
    후보를 먼저 잘라낸다. 그래서 **수집은 멀쩡히 됐는데 창 밖이라 잘린 건**이
    '수집실패'와 구별되지 않았다. 둘은 고칠 곳이 정반대다.
      수집실패   -> tier2 쿼리/RSS 피드를 늘린다
      창밖탈락   -> datewin 을 손본다 (피드를 늘려도 절대 안 잡힌다)
    실측 08-18: 'Grok 4.6 깃허브 코파일럿'은 tier0(x.ai, 08-14 발행)에
    링크까지 있었는데 화요일 창(1일)에 걸려 사라졌다. 피드 문제가 아니다.

    tier0/tier2 는 그날 파일, tier1 은 상시 적재 sqlite 를 본다.

    [2026-09-01 사용자 지적] **tier1 을 날짜로 잘라 읽는다.**
      종전에는 `SELECT url, title FROM articles` 로 **DB 전체를 조건 없이**
      읽었다. tier0/tier2 는 그날 파일이라 자동으로 그날치인데, tier1 만
      상시 적재라 몇 주 전 기사까지 '오늘의 수집 창고'에 들어왔다.
      그 결과 오늘 수집조차 안 된 기사가, 같은 회사의 2주 전 기사와 제목이
      비슷하다는 이유로 '창고에 있음' 판정을 받고 **'발행일 창 밖 탈락'**
      으로 잘못 분류됐다. 사유가 뒤집히면 고칠 곳도 정반대가 된다.
      실측 09-01: 정답지 '엘리스그룹, IPO 자금 활용 모듈 데이터센터 전국
      확대'(전자신문 08-31)는 DB 에 아예 없었는데(URL 조회 0건),
      '엘리스그룹, 1조 가치 시험[IPO 엑스레이]'(08-24 수집)와 title_sim
      0.47 로 붙어 창밖으로 찍혔다. 실제 사유는 수집실패다.

      창 폭은 refine 보다 **넓게** 둔다. 창밖 탈락은 정의상 refine 창
      (1~2일) **밖**에 발행된 건이므로, 버킷을 refine 과 같은 폭으로
      잡으면 그 사유가 영원히 안 찍힌다. RAW_TIER1_WIN=7 일이면 진짜
      창밖 건은 잡고 몇 주 전 기사는 걸러진다.
      first_seen 상한도 둔다. 실행일 **뒤에** 수집된 기사는 그날 쓸 수
      없었으므로 창고에 있었다고 볼 수 없다.
      발행일 미상(published NULL)은 refine 이 제외하므로 여기서도 뺀다.
    """
    urls, titles = set(), []
    for name in ("tier0", "tier2"):
        d = load_json(os.path.join(OUTDIR, "%s-%s.json" % (name, day)))
        items = d if isinstance(d, list) else (d or {}).get("items") or []
        for it in items:
            if it.get("url"):
                urls.add(norm_url(it["url"]))
            titles.append(it.get("title") or "")
    db = os.path.join(BASE, "index", "articles.sqlite")
    if os.path.exists(db):
        try:
            import sqlite3
            lo = (datetime.date.fromisoformat(day)
                  - datetime.timedelta(days=RAW_TIER1_WIN)).isoformat()
            conn = sqlite3.connect(db)
            rows = conn.execute(
                "SELECT url, title FROM articles "
                " WHERE published IS NOT NULL"
                "   AND substr(published, 1, 10) BETWEEN ? AND ?"
                "   AND substr(first_seen, 1, 10) <= ?", (lo, day, day))
            for u, t in rows:
                if u:
                    urls.add(norm_url(u))
                titles.append(t or "")
            conn.close()
        except Exception:
            pass
    return urls, titles


def _in_bucket(t_url, t_title, bucket, thr=None):
    """정답지 1건이 이 버킷 안에 있나. URL -> 제목 순으로 본다.

    [2026-08-18] 제목 비교를 bigram(same_event)만 쓰다가 **사유가 통째로
    틀렸다**. 정답지는 사람이 영문 기사를 국문 제목으로 옮겨 적기 때문에
    원문이 영문이면 글자 bigram 이 거의 안 겹친다. 그래서 tier0 에 멀쩡히
    수집돼 있던 건이 '수집실패'로 찍혔다.
      실측: 'Grok 4.6, 깃허브 코파일럿에 정식 탑재'(정답지) 와
            'Grok 4.6 in GitHub Copilot'(tier0, x.ai) 이
            bigram 0.00 / title_sim 0.40 이다.
    판정(match_all)은 이미 match.title_sim 을 쓰는데 여기만 안 쓰고 있었다.
    같은 매처로 통일한다(2026-08-12 '단일 매처' 방침의 누락분).
    임계값은 match_all 기본값과 같은 0.28 을 쓴다.
    """
    urls, titles = bucket
    thr = BUCKET_THR if thr is None else thr
    if t_url and t_url in urls:
        return True
    for x in titles:
        if same_event(t_title, x):
            return True
        try:
            if M_title_sim(t_title, x) >= thr:
                return True
        except Exception:
            pass
    return False


def load_false_hits(day):
    """원장에 손보정으로 적어둔 '자동 일치 오탐' 제목 조각을 읽는다."""
    for r in read_ledger():
        if r.get("date") == day:
            return tuple(r.get("false_hits") or ())
    return ()


def classify(day, truth_items, my_urls, my_titles=(), false_hits=(),
             stem="brief"):
    """정답지 각 건을 4사유로 분류하고 사건 단위 일치를 센다.

    stem = 어느 브리프를 채점하는지("brief" 30건 / "brief20" 상위 20건).
      [2026-08-31] 아래 pair_by_title 이 parse_brief(day) 를 stem 없이
      불러 **항상 30건 브리프**로 짝을 지었다. 그래서 상위 20건을 채점할 때도
      30건의 매칭 결과가 그대로 쓰여 url_hit20/event_hit20 이 30건 값으로
      찍혔다(실측 08-31: 헤더는 URL 5/19 인데 원장은 6/19, 사건도 11/19 로
      30건과 동일). dropped_in_20 만 rows 집합으로 따로 세서 맞았다.
      호출부가 넘긴 stem 을 그대로 parse_brief 에 물려준다.

    false_hits = 자동 매칭이 '일치'로 잘못 잡은 정답지 제목 조각들.
      아래 my_cluster_* 확장은 내 기사가 속한 클러스터를 통째로 펼치는데,
      refine 의 클러스터링이 서로 다른 사건을 한 덩어리로 묶으면 내가 싣지도
      않은 기사가 '일치'로 계산된다(실측 2026-08-06: 2건. 블랙스톤 기사
      클러스터에 MS 기본모델 기사가, 내 기사 클러스터에 NewsClips 지면
      항목이 섞였다). 확장을 좁히면 정탐도 같이 줄어(그날 정탐 5건이 전부
      이 확장으로 잡혔다) 사람이 눈으로 본 결과를 여기에 적어 되돌린다.
      쓰는 법: cowork/scorecard.py --annotate '{"false_hits":["제목 조각"]}'

    반환: (rows, stats)
      rows  = [{title, cat, url, hit, hit_kind, reason}]
      stats = {url_hit, event_hit, ...}
    """
    ranked = load_json(os.path.join(OUTDIR, "ranked-%s.json" % day)) or {}
    refined = load_json(os.path.join(OUTDIR, "refined-%s.json" % day)) or {}
    have_artifacts = bool(ranked.get("items") or refined.get("items"))

    exposed, nolink, culled = _bucket(refined, ranked)
    raw = _raw_bucket(day)

    # 내가 실은 기사가 속한 클러스터를 통째로 펼친다.
    # URL 뿐 아니라 멤버 제목까지 모아야 한다. 정답지가 그 클러스터의 멤버
    # 기사인데 member_urls 에는 URL 이 안 담긴 경우가 있기 때문이다
    # (실측 08-03: '통제이탈'·'보안특화 AI' 두 건이 이 경우였다).
    my_cluster_urls = set(my_urls)
    my_cluster_titles = list(my_titles)
    for it in (refined.get("items") or []) + (refined.get("own_track") or []):
        us = {norm_url(u) for u in (it.get("member_urls") or []) if u}
        if it.get("url"):
            us.add(norm_url(it["url"]))
        titles = [it.get("title") or ""] + list(it.get("members") or [])
        if (us & my_urls) or any(same_event(t, m)
                                 for t in my_titles for m in titles):
            my_cluster_urls |= us
            my_cluster_titles.extend(titles)

    # [2026-08-12] 표시용(report.pair)과 같은 매처를 쓴다.
    #   종전에는 여기서 클러스터로 일치를 잡아도 '내 어느 기사 때문인지'를
    #   남기지 않아, 엑셀이 짝을 다시 찾다 실패하면 '대응기사 미상'이 됐다.
    #   match_all 은 짝까지 함께 돌려주므로 두 값이 갈릴 수 없다.
    #   기존 경로(클러스터 URL/제목 확장)는 보조로 남겨 회수율을 유지한다.
    pair_by_title = {}
    try:
        mine_rows = parse_brief(day, stem=stem)
        if mine_rows:
            for mt in match_all(truth_items, mine_rows, refined, norm_url):
                if mt["mine"] is not None:
                    pair_by_title[mt["truth"].get("title")] = mt
    except Exception:
        pair_by_title = {}      # 매처가 실패해도 아래 기존 경로로 판정한다

    rows = []
    for t in truth_items:
        tu = norm_url(t.get("url"))
        ttl = t.get("title") or ""
        mt = pair_by_title.get(ttl)
        if tu in my_urls:
            hit_kind = "url"
        elif mt is not None:
            hit_kind = "url" if mt["kind"] == "동일기사" else "event"
        elif tu in my_cluster_urls:
            hit_kind = "event"          # refine 이 같은 클러스터로 묶은 경우
        elif any(same_event(ttl, m) for m in my_cluster_titles):
            hit_kind = "event"          # 클러스터가 놓친 쌍을 제목으로 잡는다
        else:
            hit_kind = None

        # 사람이 오탐으로 표시한 건은 일치를 취소한다. 아래 사유 분류가
        # 그대로 이어져 '어느 단계에서 빠졌는지'까지 다시 계산된다.
        if hit_kind and any(f and f in ttl for f in false_hits):
            hit_kind = None

        reason = None
        if not hit_kind:
            if not have_artifacts:
                reason = None                       # 산출물 없음 - 판정 불가
            elif _in_bucket(tu, ttl, exposed):
                # [주의] 내 브리프가 없는 날(백필 대상)에 이걸 R_DROP 으로
                #   적으면 안 된다. 선별을 한 적이 없으므로 편집 판단의 잘못이
                #   아니고, 그대로 두면 프롬프트에 주입되는 편향 통계가 오염된다.
                reason = R_DROP if my_urls else R_EXPOSED
            elif _in_bucket(tu, ttl, nolink):
                reason = R_NOLINK
            elif _in_bucket(tu, ttl, culled):
                reason = R_CUT
            elif _in_bucket(tu, ttl, raw, thr=RAW_THR):
                # 원시 수집물에는 있는데 refined/ranked 어디에도 없다
                # = refine 이 발행일 창에서 잘라낸 것.
                reason = R_WINDOW
            else:
                reason = R_COLLECT
        rows.append({"title": ttl, "cat": t.get("category"),
                     "url": t.get("url"), "hit": bool(hit_kind),
                     "hit_kind": hit_kind, "reason": reason})

    stats = {
        "truth_n": len(truth_items),
        "url_hit": sum(1 for r in rows if r["hit_kind"] == "url"),
        "event_hit": sum(1 for r in rows if r["hit_kind"]),
        "auto": have_artifacts,
    }
    return rows, stats


def read_ledger():
    if not os.path.exists(LEDGER):
        return []
    out = []
    for line in open(LEDGER, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        for m in rec.get("misses") or []:      # 예전 사유 이름을 갈아끼운다
            if m.get("reason") in RENAME:
                m["reason"] = RENAME[m["reason"]]
        out.append(rec)
    return out


def write_ledger(rows):
    os.makedirs(LOGDIR, exist_ok=True)
    rows = [r for r in rows if r.get("date", "") >= START]
    rows = sorted(rows, key=lambda r: r.get("date", ""))
    with open(LEDGER, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def upsert(rec):
    """같은 날짜가 있으면 갈아끼우고, 없으면 붙인다.
    [주의] 손보정(note/reason_detail)은 보존한다. 자동 재실행이 사람 판단을
    지우면 기록의 의미가 없어진다."""
    led = read_ledger()
    old = {r["date"]: r for r in led if r.get("date")}
    prev = old.get(rec["date"])
    if prev:
        # [2026-08-31] event_hit20_manual 을 빠뜨려 두면, 재기록할 때마다
        #   상위20 손보정이 통째로 날아간다(08-27 에 annotate 화이트리스트에는
        #   넣었으나 여기 보존 목록에는 안 넣었다). dropped_in_20 은 자동값이
        #   맞으므로 일부러 뺀다 - 매번 새로 계산된 값을 쓴다.
        for k in ("note", "event_hit_manual", "event_hit20_manual",
                  "pairs", "false_hits"):
            if prev.get(k) is not None and rec.get(k) is None:
                rec[k] = prev[k]
        # 사람이 붙인 세부 유형과 손보정한 사유를 제목 기준으로 되살린다.
        # [주의] detail 만 되살리고 reason 을 빼먹으면, 재실행할 때마다
        #   '사건일치(수동)'로 고쳐둔 게 자동 판정으로 되돌아가 추이가 튄다.
        # [주의] 이름을 old 로 쓰면 위의 원장 dict(old)를 덮어써서, 재기록할 때
        #   다른 날짜 기록이 통째로 사라진다. 실제로 그렇게 3일치가 1일치가 됐다.
        old_miss = {m.get("title"): m for m in (prev.get("misses") or [])}
        for m in rec.get("misses") or []:
            o = old_miss.get(m.get("title"))
            if not o:
                continue
            if o.get("detail"):
                m["detail"] = o["detail"]
            if o.get("manual_reason"):
                m["reason"] = o["reason"]
                m["manual_reason"] = True
    old[rec["date"]] = rec
    write_ledger(list(old.values()))


def _recount(rec):
    """false_hits 를 반영해 event_hit / misses 를 그 자리에서 다시 센다.

    일치를 취소하면 그 건은 다시 '어느 단계에서 빠졌는지'를 판정해야 하므로
    classify 를 다시 돌린다. 사람이 붙인 사유(manual_reason)와 detail 은
    그대로 물려받는다 - 자동 재계산이 사람 판단을 지우면 안 된다.
    """
    day = rec.get("date")
    truth = load_json(os.path.join(BASE, "truth-%s.json" % day)) or {}
    titems = truth.get("items") or []
    if not titems:
        print("[scorecard] 정답지가 없어 재집계를 건너뜁니다: %s" % day)
        return
    mine = parse_brief(day) or []
    my_urls = {norm_url(m[3]) for m in mine}
    my_titles = [m[1] for m in mine]
    rows, stats = classify(day, titems, my_urls, my_titles,
                           tuple(rec.get("false_hits") or ()))

    prev = {m.get("title"): m for m in (rec.get("misses") or [])}
    misses = []
    for r in rows:
        if r["hit"]:
            continue
        m = {"title": r["title"], "cat": r["cat"], "reason": r["reason"]}
        o = prev.get(r["title"])
        if o:
            if o.get("detail"):
                m["detail"] = o["detail"]
            if o.get("manual_reason"):
                m["reason"] = o["reason"]
                m["manual_reason"] = True
        misses.append(m)
    rec["misses"] = misses
    if my_urls:
        rec["url_hit"] = stats["url_hit"]
        rec["event_hit"] = stats["event_hit"]


# ------------------------------------------------------------------ 명령
def cmd_record(a):
    day = a.date
    if day < START:
        print("[scorecard] %s 는 기록 시작일(%s) 이전이라 건너뜁니다." % (day, START))
        return 0
    tp = os.path.join(BASE, "truth-%s.json" % day)
    truth = load_json(tp)
    if not truth:
        print("[scorecard] 정답지 없음: %s" % tp)
        return 2
    titems = truth.get("items") or []

    mine = parse_brief(day)
    if mine:
        my_urls = {norm_url(m[3]) for m in mine}
        mine_n = len(mine)
        # 헤드라인도 넘긴다. refine 이 같은 사건으로 못 묶은 쌍을 여기서 잡는다.
        my_titles = [m[1] for m in mine]
    else:
        my_urls, mine_n, my_titles = set(), None, []

    rows, stats = classify(day, titems, my_urls, my_titles,
                           load_false_hits(day))
    misses = [{"title": r["title"], "cat": r["cat"], "reason": r["reason"]}
              for r in rows if not r["hit"]]

    rec = {
        "date": day,
        "truth_n": stats["truth_n"],
        "mine_n": mine_n,
        "url_hit": stats["url_hit"] if my_urls else None,
        "event_hit": stats["event_hit"] if my_urls else None,
        "event_hit_manual": None,
        "misses": misses,
        "auto": stats["auto"],
        "note": None,
        "recorded_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }

    # [2026-08-26 사용자 지시] 30건 브리프에서 다시 추린 상위 20건도 함께 잰다.
    #   30건은 텔레그램 초안 채널로 나가고, 20건은 채팅으로만 남긴다.
    #   둘을 같은 정답지로 채점해 **'더 좁게 골랐을 때 무엇을 잃는가'** 를 본다.
    #   30건에는 있었는데 20건에서 뺀 정답지 항목(dropped_in_20)이 핵심 지표다.
    #   그게 곧 내 우선순위 판단이 틀린 지점이다.
    mine20 = parse_brief(day, stem="brief20")
    if mine20:
        urls20 = {norm_url(m[3]) for m in mine20}
        rows20, st20 = classify(day, titems, urls20, [m[1] for m in mine20],
                                load_false_hits(day), stem="brief20")
        hit30 = {r["title"] for r in rows if r["hit"]}
        hit20 = {r["title"] for r in rows20 if r["hit"]}
        rec.update({
            "mine20_n": len(mine20),
            "url_hit20": st20["url_hit"],
            "event_hit20": st20["event_hit"],
            "event_hit20_manual": None,
            "dropped_in_20": sorted(hit30 - hit20),
        })

    upsert(rec)

    if my_urls:
        print("[scorecard] %s  URL %s/%s -> 사건 %s/%s  (내 브리프 %s건)"
              % (day, rec["url_hit"], rec["truth_n"], rec["event_hit"],
                 rec["truth_n"], mine_n))
    else:
        print("[scorecard] %s  내 브리프 없음 - 파이프라인 사유만 기록"
              % day)
    if rec.get("mine20_n"):
        print("           상위20: URL %s/%s -> 사건 %s/%s  (20건 중)"
              % (rec["url_hit20"], rec["truth_n"], rec["event_hit20"],
                 rec["truth_n"]))
        if rec["dropped_in_20"]:
            print("           30->20 에서 놓친 정답지 %d건: %s"
                  % (len(rec["dropped_in_20"]),
                     ", ".join(t[:26] for t in rec["dropped_in_20"][:4])))
    cnt = {}
    for m in misses:
        cnt[m["reason"] or "판정불가"] = cnt.get(m["reason"] or "판정불가", 0) + 1
    if cnt:
        print("           놓침 %d건: %s" % (
            len(misses), ", ".join("%s %d" % kv for kv in sorted(
                cnt.items(), key=lambda x: -x[1]))))
    print("           기록: %s" % LEDGER)
    return 0


def cmd_annotate(a):
    """사건 단위 수치와 놓친 사유의 세부 유형을 손보정한다.
    사용: --annotate '{"event_hit_manual":12,"note":"...",
                       "details":{"기사제목 일부":"국내 의료·공공데이터"}}'
    """
    led = {r["date"]: r for r in read_ledger()}
    rec = led.get(a.date)
    if not rec:
        print("[scorecard] %s 기록 없음. record 를 먼저 돌리세요." % a.date)
        return 2
    patch = json.loads(a.annotate)
    # [2026-08-27] event_hit20_manual / dropped_in_20 을 화이트리스트에 넣는다.
    #   상위20 채점이 08-26 에 들어왔는데 이 두 키가 빠져 있어 손보정이
    #   조용히 무시됐다. 실측 08-26: 자동이 20건 사건 15/20 으로 찍었으나
    #   실제로는 10/20 이었다(자동이 30건 쪽 매칭을 그대로 재사용한 것).
    for k in ("event_hit_manual", "event_hit20_manual", "dropped_in_20",
              "note", "pairs", "false_hits"):
        # pairs = {"정답지 제목 조각": "내 브리프 제목 조각"}
        #   자동 매칭이 엉뚱한 기사를 짝지을 때 report.py 가 쓰는 지정값
        # false_hits = ["정답지 제목 조각", ...]
        #   자동 매칭이 '일치'로 잘못 잡은 건. 일치를 취소하고 사유를 다시 센다.
        #   (classify 주석 참조. 반대 방향 보정은 reasons 의 '사건일치(수동)')
        if k in patch:
            rec[k] = patch[k]
    if "false_hits" in patch:
        # 일치 취소는 사유 분류가 통째로 바뀌므로 그 자리에서 다시 채점한다.
        _recount(rec)
    for frag, detail in (patch.get("details") or {}).items():
        for m in rec.get("misses") or []:
            if frag in (m.get("title") or ""):
                m["detail"] = detail
    if "reasons" in patch:                       # 사유 자체를 고칠 때
        for frag, reason in patch["reasons"].items():
            for m in rec.get("misses") or []:
                if frag in (m.get("title") or ""):
                    m["reason"] = reason
                    m["manual_reason"] = True    # 재실행해도 안 덮이게
    write_ledger(list(led.values()))
    print("[scorecard] %s 손보정 완료" % a.date)
    return 0


def cmd_trend(a):
    led = [r for r in read_ledger() if r.get("date")][-a.days:]
    if not led:
        print("[scorecard] 기록 없음")
        return 1
    print("날짜         정답  내브리프  URL   사건    놓침 사유")
    print("-" * 74)
    tot = {}
    for r in led:
        cnt = {}
        for m in r.get("misses") or []:
            k = m.get("reason") or "?"
            cnt[k] = cnt.get(k, 0) + 1
            tot[k] = tot.get(k, 0) + 1
        eh = r.get("event_hit_manual")
        eh = eh if eh is not None else r.get("event_hit")
        rate = ("%3.0f%%" % (100.0 * eh / r["truth_n"])) if eh is not None \
            and r.get("truth_n") else "  - "
        print("%s  %3s   %5s  %4s  %4s %s   %s" % (
            r["date"], r.get("truth_n", "-"),
            r.get("mine_n") if r.get("mine_n") is not None else "-",
            r.get("url_hit") if r.get("url_hit") is not None else "-",
            eh if eh is not None else "-", rate,
            ", ".join("%s%d" % kv for kv in sorted(cnt.items(),
                                                   key=lambda x: -x[1]))))
    print("-" * 74)
    print("누적 놓침 사유: %s" % ", ".join(
        "%s %d" % kv for kv in sorted(tot.items(), key=lambda x: -x[1])))

    det = {}
    for r in led:
        for m in r.get("misses") or []:
            if m.get("reason") == R_DROP:
                k = m.get("detail") or m.get("cat") or "?"
                det[k] = det.get(k, 0) + 1
    if det:
        print("'LLM 선별 후보에서 탈락' 유형: %s" % ", ".join(
            "%s %d" % kv for kv in sorted(det.items(), key=lambda x: -x[1])))
    return 0


def recent_bias(days=14, min_n=2):
    """brief.py 가 프롬프트에 넣을 '최근 자주 놓친 유형' 문자열.
    없으면 빈 문자열. (import 해서 쓴다)"""
    led = [r for r in read_ledger() if r.get("date")]
    if not led:
        return ""
    cut = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    led = [r for r in led if r["date"] >= cut]
    det, pipe = {}, {}
    for r in led:
        for m in r.get("misses") or []:
            if m.get("reason") == R_DROP:
                k = m.get("detail") or m.get("cat")
                if k:
                    det[k] = det.get(k, 0) + 1
            elif m.get("reason"):
                pipe[m["reason"]] = pipe.get(m["reason"], 0) + 1
    top = [k for k, v in sorted(det.items(), key=lambda x: -x[1]) if v >= min_n]
    if not top:
        return ""
    return ("최근 %d일간 내가 후보를 다 보고도 반복해서 놓친 유형입니다. "
            "오늘 같은 성격의 기사가 있으면 우선 검토하세요: %s"
            % (days, ", ".join(top[:6])))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--record", action="store_true", help="채점 결과 적재")
    ap.add_argument("--annotate", metavar="JSON", help="손보정 패치")
    ap.add_argument("--trend", action="store_true", help="추이 표")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--backfill", action="store_true",
                    help="truth-*.json 전체를 소급 적재")
    a = ap.parse_args()

    if a.annotate:
        sys.exit(cmd_annotate(a))
    if a.trend:
        sys.exit(cmd_trend(a))
    if a.backfill:
        days = sorted(re.findall(r"truth-(\d{4}-\d{2}-\d{2})\.json",
                                 " ".join(os.listdir(BASE))))
        n = 0
        for d in days:
            a.date = d
            if cmd_record(a) == 0:
                n += 1
        print("[scorecard] 백필 %d일" % n)
        sys.exit(0)
    sys.exit(cmd_record(a))


if __name__ == "__main__":
    main()
