# -*- coding: utf-8 -*-
"""
refine.py - 전 소스를 합쳐 중복 제거하고 중요도를 매겨 브리프 후보를 뽑는다.

입력 (있는 것만 자동으로 사용):
  out/tier0-YYYY-MM-DD.json   슬랙/삼성/에이닷 큐레이션 (링크 보유)
  index/articles.sqlite       tier1 매체 RSS 상시적재 (직링크 보유)
  out/tier2-YYYY-MM-DD.json   구글뉴스 키워드 검색 (링크 없음)

출력:
  out/refined-YYYY-MM-DD.json   임계점수 이상 후보 (링크 해결 완료)
  out/preview-YYYY-MM-DD.txt    사람이 읽는 랭킹

[순서가 중요하다]
  링크 해결은 항목당 1~3초가 든다. 원시 후보가 수천 건이므로 전부 해결하면
  수십 분이 걸리고 대부분 점수 미달로 버려진다.
  그래서 '수집 -> 중복제거 -> 채점 -> 임계 컷 -> 살아남은 것만 링크 해결' 순서다.

[클러스터링]
  후보가 수천 건이라 전수 비교(O(n^2))는 불가능하다.
  변별토큰 역색인으로 후보쌍을 좁힌 뒤에만 비교한다.
"""
import argparse
import datetime
import html
import json
import os
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yaml                                          # noqa: E402
import samsung_resolve as SR
import datewin as DW                         # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.join(BASE, "out")
KW = os.path.join(BASE, "config", "keywords.yaml")
DB = os.path.join(BASE, "index", "articles.sqlite")

# 소스 신뢰도는 '누가 골랐는가'에 따라 다르다.
#   samsung = 사람(삼성 이영진)이 직접 선별. 잡음이 거의 없다 -> 높은 가산 + 필터 면제
#   adot/slack = AI 가 자동 선별. 잡음이 섞인다 -> 가산 낮추고 relevance 필터 적용
# 실제 7/20 에이닷 50건에는 '경북도 AI 정책 경진대회', '전주시장 인터뷰',
# '카카오 펫레터 이벤트', '[알립니다] 스마트클라우드쇼 개최' 같은 항목이 있었다.
# 이런 것을 큐레이션이라고 믿고 가산점을 주면 브리프 품질이 무너진다.
#   newsclips = SKT NewsClips. 사람이 종이 신문 지면을 스크랩한 뒤 AI 관련만
#     추린 것이라 잡음이 거의 없다(실측 08-05: 10건 전부 AI 기사).
#     지면 기사는 RSS/구글뉴스에서 잘 안 잡히는데 사람 브리프와는 가장
#     가깝다 -> samsung 과 같은 계층으로 둔다.
CURATED_HUMAN = {"samsung", "newsclips"}
CURATED_AI = {"adot", "slack", "aibrief",
              "community"}   # [2026-09-23] HN·GitHub·HF (cowork/community.py)
# [2026-09-09] aibrief = 사용자의 08:00 스케줄 작업 '일일 AI 뉴스 브리핑'.
#   사람이 아니라 에이전트가 웹서치로 고른 목록이라 봇큐레이션(+1)이다.
#   tier0 이므로 잡음 필터는 면제되고 전량 판정 대상에는 들어간다.
# [2026-08-22] CURATED_IMPACT(개인테스트 Daily AI Tech Trends) 삭제.
#   그 소스는 더 이상 들어오지 않는다. impact 가점 경로도 함께 지웠다.
# 필터 면제 대상: tier0 전부.
# [2026-08-11 사용자 지시] "tier0 에서 수집된 기사는 LLM 선별 후보 단계까지
#   무조건 남아 있어야 한다." 예전에는 사람 선별(samsung/newsclips)과 임팩트
#   큐레이션(curated)만 면제하고 봇 선별(adot/slack)은 잡음 필터에 걸었다.
#   실측 근거: 정답지 112건 중 65건(58%)이 tier0 통로로 들어왔다. tier0 는
#   하루 90건 안팎인데 tier2 는 4,000건이 넘으므로 건당 적중 밀도가 비교가
#   안 된다. 잡음 2~8건이 후보 300건에 더 붙는 비용은 감수하고, 버릴지는
#   기계가 아니라 선별 단계에서 판단한다.
FILTER_EXEMPT = CURATED_HUMAN | CURATED_AI
TRUSTED = CURATED_HUMAN | CURATED_AI
# 이보다 흔한 토큰은 클러스터 후보 탐색에서 제외한다(변별력 없음 + 폭발 방지)
MAX_DF = 120

# 클러스터링 토큰에 주입하는 canonical 엔티티 토큰의 접두. 일반 토큰과 안 겹치게.
ENT_PREFIX = "❖"     # ❖


def _flat_lower(s):
    return re.sub(r"[\s\-·_]", "", s or "").lower()


def build_entity_canon(cfg):
    """config entity_aliases -> {표면형(flat,lower): canonical}. 긴 표면형 우선."""
    canon = {}
    for grp in cfg.get("entity_aliases") or []:
        if not grp:
            continue
        cid = _flat_lower(grp[0])
        for surf in grp:
            key = _flat_lower(surf)
            if key:
                canon[key] = cid
    return canon


def entity_ids(text, canon):
    """제목/요약에서 등장하는 canonical 엔티티 집합.

    소문자·공백제거한 문자열에 표면형이 부분문자열로 있으면 해당 엔티티로 본다.
    (한글은 토크나이즈가 어려워 부분문자열이 가장 실용적. 오탐 위험 표면형은
     config 에서 이미 배제했고, 교차언어 병합은 2개 이상 공유를 요구해 방어한다.)
    """
    f = _flat_lower(text)
    if not f:
        return set()
    return {cid for surf, cid in canon.items() if surf in f}


def log(m):
    print("[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), m))


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


def txt(i):
    return "%s %s" % (i.get("title") or "", i.get("summary") or "")


def is_self_subject(item, sig):
    """이 기사의 '주어'가 SKT 본체인지 판정한다.

    [2026-08-06 사용자 지시] SKT 내부 기사는 브리프에서 뺀다. 다만 SKT 가
    그냥 언급된 기사까지 빼면 안 된다.
      뺄 것:   "SKT, 5GW AIDC '선구축' 없다…"        (SKT 가 주어)
      남길 것: "엔비디아, SKT·네이버와 AI 보안 가이드라인" (엔비디아가 주어)
      남길 것: "SKT 독파모 2차 모델 공개"              (독파모 = 면제 주제)

    판정은 제목만 본다(요약까지 보면 SKT 가 스쳐 나온 기사가 다 걸린다).
      1) self_demote_exempt 주제면 면제
      2) 제목에서 SKT 키워드보다 앞에 다른 주체(self_demote_actors)가 있으면
         그 기사의 주어는 SKT 가 아니다
      3) 그 외에 제목에 SKT 키워드가 있으면 자사 주체로 본다

    반환: 걸린 키워드(문자열) 또는 None
    """
    title = item.get("title") or ""
    if not title:
        return None
    if hit(title, sig.get("self_demote_exempt")):
        return None
    marks = [(title.find(k), k) for k in (sig.get("self_demote") or [])
             if k in title]
    if not marks:
        return None
    pos, kw = min(marks)
    for a in (sig.get("self_demote_actors") or []):
        i = title.find(a)
        if 0 <= i < pos:
            return None                  # 앞에 다른 주체가 있다 -> 그쪽 기사
    return kw


def is_korean(it):
    """제목이 한글 위주면 국내 기사로 본다.

    한글 문자가 하나라도 있으면 한글 기사로 간주(국내 매체가 영문 고유명사를
    많이 써도 제목에 한글이 섞이므로). 영문 원문은 제목에 한글이 전혀 없다.
    """
    t = it.get("title") or it.get("summary") or ""
    return bool(re.search(r"[가-힣]", t))


# 매체 URL 에 흔히 박히는 발행일 패턴(YYYYMMDD). 국내 매체 다수가 이 형식.
_URLDATE = re.compile(r"(20\d{2})[/_-]?(0[1-9]|1[0-2])[/_-]?(0[1-9]|[12]\d|3[01])")


def url_date(u):
    """URL 에서 발행일(YYYY-MM-DD)을 추출. 없으면 None."""
    if not u:
        return None
    m = _URLDATE.search(u)
    if not m:
        return None
    y, mo, d = m.groups()
    return "%s-%s-%s" % (y, mo, d)


# ---------------------------------------------------------------- 입력
def load_tier0(day):
    p = os.path.join(OUTDIR, "tier0-%s.json" % day)
    if not os.path.exists(p):
        return []
    return json.load(open(p, encoding="utf-8"))["items"]


def load_community(day):
    """[2026-09-23] cowork/community.py 산출물. 없으면 빈 목록(구 날짜 재처리 호환)."""
    p = os.path.join(OUTDIR, "community-%s.json" % day)
    if not os.path.exists(p):
        return []
    try:
        return json.load(open(p, encoding="utf-8")).get("items", [])
    except Exception:
        return []


def load_buzz(day):
    """[2026-09-23] cowork/buzz.py 산출물. 반향 낱말 목록(점수 10 이상만)."""
    p = os.path.join(OUTDIR, "buzz-%s.json" % day)
    if not os.path.exists(p):
        return []
    try:
        return [t for t in json.load(open(p, encoding="utf-8")).get("terms", [])
                if (t.get("score") or 0) >= 10 or t.get("community")]
    except Exception:
        return []


def _occurs(term_lower, text_lower):
    # 낱말 경계: 영문 양쪽, 한글 앞쪽만(조사 허용). buzz.py term_re 와 같은 규칙.
    if re.fullmatch(r"[a-z0-9 .\-]+", term_lower):
        return re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(term_lower), text_lower) is not None
    return re.search(r"(?<![가-힣a-z0-9])%s" % re.escape(term_lower), text_lower) is not None


def load_tier2(day):
    p = os.path.join(OUTDIR, "tier2-%s.json" % day)
    if not os.path.exists(p):
        return []
    return json.load(open(p, encoding="utf-8"))["items"]


def load_tier1(day, cfg):
    """매체 RSS 상시적재 인덱스에서 발간일 창 안의 기사를 꺼낸다.

    인덱스에는 연예·스포츠·증시까지 매체 전체가 들어 있으므로
    여기서는 relevance must_any 를 반드시 적용한다.
    발간일(published)이 전일~당일 창을 벗어나면 제외. 원시 수집이므로
    발간일 미상(published NULL)도 제외한다(오래된 기사가 first_seen 으로
    창 안에 들어오는 것을 막기 위해).
    """
    if not os.path.exists(DB):
        return []
    pol = cfg.get("policy", {})
    d = datetime.date.fromisoformat(day)
    win = DW.window_days(pol, d)          # 공휴일 보정 포함(연휴 다음날은 창 확대)
    lo = (d - datetime.timedelta(days=win)).isoformat()
    hi = d.isoformat()
    allow_undated = pol.get("allow_undated_raw", False)
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT title, url, outlet, published FROM articles").fetchall()
    conn.close()
    rel = cfg.get("relevance", {})
    out = 0
    res = []
    for t, u, o, pub in rows:
        pd = (pub or "")[:10]
        if not pd:
            if not allow_undated:
                continue
        elif not (lo <= pd <= hi):
            continue
        if not hit(t, rel.get("must_any")):
            continue
        if hit(t, rel.get("must_none")):
            continue
        res.append({"src": "rss", "title": t, "outlet": o, "url": u,
                    "pub_date": pd or None, "cluster": 1,
                    "confidence": "auto", "queries": [], "groups": []})
    return res


# ---------------------------------------------------------------- 클러스터
def cluster(items, canon, min_shared=3, min_jaccard=0.34):
    """같은 사건 묶기.

    [왜 union-find 가 아닌가]
      처음엔 역색인 + union-find 로 짰는데 3,808건에서 최대 클러스터가
      1,052건이 됐다. A~B 가 비슷하고 B~C 가 비슷하면 A~C 가 전혀 달라도
      한 덩어리가 되는 전이 연쇄 때문이다.
      그래서 전이를 허용하지 않는 그리디 방식으로 바꿨다:
      새 기사는 '기존 클러스터의 대표'와만 비교하고, 대표 토큰은 고정한다.

    [교차언어 병합]
      국문/영문은 엔티티 이름이 달라(앤스로픽↔Anthropic) 일반 토큰이 거의
      안 겹친다. canonical 엔티티(❖anthropic)로 이를 잇되, 엔티티 토큰은
      '교차언어 병합'에만 쓴다. 동일언어 병합 임계(min_shared/jaccard)는
      base 토큰만으로 판정해 기존 동작을 그대로 보존한다(엔티티를 임계 계산에
      섞으면 'SK하이닉스' 같은 흔한 엔티티가 무관한 국문 기사들을 과병합함).
      후보 탐색 색인에는 엔티티 토큰도 넣어 교차언어 후보가 발견되게 한다.
      병합 조건:
        동일언어: base_shared >= min_shared  또는  j(base) >= min_jaccard
        교차언어: 위 조건  또는  엔티티 2개 이상 공유(엔티티 1개론 안 묶음)
    """
    order = sorted(range(len(items)),
                   key=lambda i: (items[i]["src"] in TRUSTED,
                                  len(SR.distinctive(txt(items[i])))),
                   reverse=True)

    reps = []                     # [{base, ents, kor, members}]
    inv = defaultdict(list)       # token(base 또는 엔티티) -> [cluster_id]
    byurl = {}

    for i in order:
        it = items[i]
        base = SR.distinctive(txt(it))
        ents = entity_ids(txt(it), canon)
        kor = is_korean(it)
        u = it.get("url")
        if u and u in byurl:                    # URL 같으면 무조건 동일 기사
            reps[byurl[u]]["members"].append(it)
            continue
        if not base and not ents:
            reps.append({"base": base, "ents": ents, "kor": kor, "members": [it]})
            continue

        # 후보 탐색: base 토큰 + 엔티티 토큰 색인 양쪽에서 클러스터를 모은다.
        cand = set()
        for t in base:
            lst = inv.get(t)
            if lst and len(lst) <= MAX_DF:
                cand.update(lst)
        for e in ents:
            lst = inv.get(ENT_PREFIX + e)
            if lst and len(lst) <= MAX_DF:
                cand.update(lst)

        # 엔티티가 2개 이상이면, 흔한 엔티티(MAX_DF 초과)라도 '2개 동시 공유'
        # 클러스터는 드물다. 각 엔티티 포스팅을 교집합해 그런 클러스터만 후보에
        # 추가한다(엔비디아·네이버처럼 그날 둘 다 흔해 교차언어 다리가 끊기는 것 방지).
        if len(ents) >= 2:
            ec = Counter()
            for e in ents:
                for cid in inv.get(ENT_PREFIX + e, []):
                    ec[cid] += 1
            cand.update(cid for cid, n in ec.items() if n >= 2)

        best, bkey = None, None
        for cid in cand:
            rp = reps[cid]
            bshared = len(base & rp["base"])
            union = len(base | rp["base"])
            j = bshared / union if union else 0.0
            eshared = len(ents & rp["ents"])
            cross = (kor != rp["kor"])
            # [2026-08-21] jaccard 하한을 함께 건다. 종전 조건은
            #   'bshared >= 3  또는  j >= 0.34' 라 **공유 토큰 3개만으로**
            #   묶였다. 겹친 3개가 전부 흔한 주제어면 전혀 다른 사건이 합쳐진다.
            #   실측: 'AX 새 먹거리 찾는 KT 스타트업 16곳과 협력'(NewsClips)이
            #   '공공 AX 수주 전쟁… 官 출신 잡아라'(NewsClips)에 흡수됐다.
            #   공유 토큰은 ['ax','분야','전환'] 셋뿐이고 j=0.088 이었다.
            #   고유명사는 하나도 안 겹쳤다(KT vs 네이버·카카오).
            #   NewsClips·삼성처럼 **요약문이 붙은 항목은 토큰이 18~19개**라
            #   흔한 주제어 3개가 우연히 겹칠 확률이 높고, 분모가 커져 jaccard 는
            #   오히려 낮게 나온다. 절대 개수 조건만으로는 이걸 못 거른다.
            #   결과(08-21 실측): 클러스터 331 -> 333, 오병합 74건 분리,
            #   KT 지면판이 제 클러스터(size 21)의 대표로 복귀.
            same_ok = ((bshared >= min_shared and j >= 0.15)
                       or j >= min_jaccard)
            cross_ok = (cross and eshared >= 2)
            if not (same_ok or cross_ok):
                continue
            # 동일언어 강한 매칭(높은 j)이 약한 교차언어 매칭에 밀리지 않도록
            # 1순위는 j, 2순위로만 교차언어 엔티티 수를 본다.
            key = (round(j, 4), eshared if cross else 0)
            if bkey is None or key > bkey:
                bkey, best = key, cid

        if best is not None:
            reps[best]["members"].append(it)
            if u:
                byurl[u] = best
        else:
            cid = len(reps)
            reps.append({"base": base, "ents": ents, "kor": kor, "members": [it]})
            for t in base:
                inv[t].append(cid)
            for e in ents:
                inv[ENT_PREFIX + e].append(cid)
            if u:
                byurl[u] = cid

    return [rp["members"] for rp in reps]


def pick_representative(group):
    """대표 기사: 한글 기사 > 큐레이션 소스 > 링크 보유 > 한국 매체 > 제목 김.

    한글 우선을 최상위 기준으로 둔다(영문 대표가 뽑히면 브리프에 영문이 나감).
    """
    def key(x):
        return (
            1 if is_korean(x) else 0,
            1 if x["src"] in TRUSTED else 0,
            1 if x.get("url") else 0,
            1 if re.search(r"[가-힣]", x.get("outlet") or "") else 0,
            len(x.get("title") or ""),
        )
    return sorted(group, key=key, reverse=True)[0]


# ---------------------------------------------------------------- 채점
def demote_score(item, cfg):
    rel = cfg.get("relevance", {})
    title = item.get("title") or ""
    outlet = (item.get("outlet") or "").strip()
    flagged = bool(hit(title, rel.get("demote_patterns")))
    if outlet and outlet in (rel.get("demote_outlets") or []):
        flagged = True
    if not flagged:
        return 0, None
    if hit(title, rel.get("demote_exempt")):
        return 0, "demote면제(연구성과)"
    return -2, "demote(홍보성)"


def categorize(item, cfg):
    """카테고리 배정.

    [tie-break] 예전엔 '가장 많이 겹친 카테고리'만 봤는데, 동점이면 목록 첫째인
    AI Model 이 무조건 이겼다. 그래서 '문샷 홍콩 상장'(투자)·'스페이스X 국방부
    계약'(투자)이 AI Model 로 샜다.
    이제 동점이면 config 의 앞선 카테고리(= 더 구체적)를 우선한다. keywords.yaml
    에서 독파모·정책·보안·투자 같은 '성격' 카테고리를 앞에, AI Model 처럼 넓은
    '주제' 카테고리를 뒤에 두는 이유가 이것이다.
    """
    t = txt(item)
    today = datetime.date.today()
    best, bestn, bestrank, fallback = None, 0, 999, "기타"
    for rank, c in enumerate(cfg.get("categories", [])):
        if c.get("fallback"):
            fallback = c["name"]
            continue
        exp = c.get("expires")
        if exp and datetime.date.fromisoformat(str(exp)) < today:
            continue
        n = len(hit(t, c.get("keywords")))
        if n == 0:
            continue
        if n > bestn or (n == bestn and rank < bestrank):
            best, bestn, bestrank = c["name"], n, rank
    return best or fallback


# 제목 속 수치+단위. 큰 숫자가 붙은 사건은 그 자체로 규모가 크다.
#   '2.4조 파라미터', '100억 달러', '27,500개 칩', '550조원', 'GPU 5만장'
# 보도 매체 수에만 의존하던 채점의 결함을 메운다.
NUM = r"(\d[\d,\.]*\s*)"
SCALE_UNIT = [
    (re.compile(NUM + r"(만|천)?\s*개.{0,4}(칩|GPU|반도체)"), 2, "칩수량"),
    (re.compile(NUM + r"만\s*장"), 2, "GPU장수"),
    (re.compile(r"(\d+)\s*조\s*(파라미터|매개변수)"), 3, "파라미터"),
    (re.compile(NUM + r"%\s*(하락|급락|상승|급등)"), 1, "주가변동"),
]

# 한글 통화 표기(조/억 + 원·달러·엔·위안)를 달러로 환산해 층을 나눈다.
#   [왜 고쳤나 - 2026-08-04]
#   (1) '원|달러'만 받아서 '日 NTT데이터, AI 데이터센터에 1.5조 엔 투입'이
#       규모 신호 0점이었다(전체 1점 -> 후보 컷 탈락).
#   (2) '억' 단위는 아예 없어서 '500억 달러'($50B)가 규모 0점이었다.
#   (3) 층이 없어 1조원과 550조원이 같은 +3 이었다. USD 는 이미 층화돼 있는데
#       한글 표기만 평평했다.
#   환율은 대략치다. 층을 가르는 용도라 소수점 정확도는 필요 없다.
FX_TO_USD = {"달러": 1.0, "원": 0.00072, "엔": 0.0067, "위안": 0.14}
RE_KRW_FX = re.compile(r"(\d[\d,\.]*)\s*(조|억)\s*(원|달러|엔|위안)")

# [2026-08-31] 통화어가 생략된 한글 금액('3500억 받은')을 원화로 읽는다.
#   왜 고쳤나: RE_KRW_FX 가 '조|억' 뒤에 원/달러/엔/위안을 **반드시** 요구해서
#   국문 기사에서 흔한 '3500억 받은 스타트업' 같은 표기가 규모 0점이었다.
#   실측 08-31: '우주에 데이터센터 짓는다고 3500억 받은 스타트업'(tier0,
#   아시아경제)이 5점에 묻혀 컷됐는데 정답지였다. 같은 날 후보 553건 중
#   8건이 같은 사유로 금액을 못 읽혔다(3조 유상증자·15.4조 투자·1400억 지원 등).
#   숫자만 보고 원화로 단정하면 '300억 파라미터'·'1.56조 토큰'·'1억㎞' 같은
#   비(非)금액 표현까지 금액으로 세므로 가드를 둘로 건다.
#     (1) 뒤에 붙는 단위 명사가 금액이 아니면 제외(파라미터·토큰·개·명·㎞...)
#     (2) 같은 제목 안에 '돈 맥락' 어휘가 있을 때만 인정
RE_KRW_BARE = re.compile(
    r"(\d[\d,\.]*)\s*(조|억)"
    r"(?!\s*(?:원|달러|엔|위안))"                      # 통화어가 있으면 위 정규식 담당
    r"(?!\s*(?:개|명|건|회|장|번|배|달|년|월|일|시간|"
    r"파라미터|매개변수|토큰|뷰|다운로드|마일|㎞|km|㎡|평|kWh))")
RE_MONEY_CTX = re.compile(
    r"(투자|출자|조달|유치|증자|인수|합병|대출|채권|투입|지원|펀드|자금|"
    r"예산|수주|계약|배당|기부|매출|영업익|이익|가치|밸류|몸값|규모|"
    r"베팅|들여|쏟아|사들|매입|매각|현금|보조금|인센티브|머니)")


def _usd_tier(musd):
    """백만 달러 단위 금액 -> (점수, 라벨). usd_signal 과 같은 기준."""
    if musd >= 100_000:
        return 5, "$100B+"
    if musd >= 10_000:
        return 4, "$10B+"
    if musd >= 1_000:
        return 3, "$1B+"
    if musd >= 100:
        return 2, "$100M+"
    return 1, "$M"


def krw_fx_signal(text):
    best, label = 0, None
    for m in RE_KRW_FX.finditer(text):
        try:
            n = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        mult = 1e12 if m.group(2) == "조" else 1e8
        musd = n * mult * FX_TO_USD[m.group(3)] / 1e6
        w, lb = _usd_tier(musd)
        # '조' 단위는 최소 3점을 보장한다. 환산만 따르면 1조원이 2점으로
        # 내려가 기존 동작이 후퇴한다(예전엔 조 단위 일괄 +3 이었다).
        if m.group(2) == "조":
            w = max(w, 3)
        if w > best:
            best, label = w, "%s단위(%s)" % (m.group(2), lb)

    # 통화어가 생략된 표기는 '돈 맥락'이 함께 있을 때만 원화로 친다.
    if RE_MONEY_CTX.search(text):
        for m in RE_KRW_BARE.finditer(text):
            try:
                n = float(m.group(1).replace(",", ""))
            except ValueError:
                continue
            mult = 1e12 if m.group(2) == "조" else 1e8
            musd = n * mult * FX_TO_USD["원"] / 1e6
            w, lb = _usd_tier(musd)
            if m.group(2) == "조":
                w = max(w, 3)
            if w > best:
                best, label = w, "%s단위(%s·통화어생략)" % (m.group(2), lb)
    return best, label


# 전력 규모는 자릿수로 층을 나눈다. USD 를 층화한 것과 같은 이유다.
#   예전엔 GW 든 MW 든 일괄 +2 라, '대기만 10GW'(국가 단위 적체)와
#   '20㎿ 데이터센터'(중형 1개)가 같은 점수였다. 게다가 '㎿'(U+33BF) 같은
#   조판용 합자 문자를 못 받아 20㎿ 는 아예 0점이었다.
RE_POWER = re.compile(r"(\d[\d,\.]*)\s*(GW|기가와트|MW|㎿|메가와트|㎾|kW)", re.I)


def power_signal(text):
    best, label = 0, None
    for m in RE_POWER.finditer(text):
        try:
            n = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        u = m.group(2).lower()
        if u in ("gw", "기가와트"):
            n *= 1000                      # MW 환산
        elif u in ("㎾", "kw"):
            n /= 1000
        if n >= 1000:                      # 1GW 이상 = 국가·초대형
            w, lb = 3, "전력규모(GW급)"
        elif n >= 100:                     # 100MW 이상 = 대형 DC
            w, lb = 2, "전력규모(100MW+)"
        else:
            w, lb = 1, "전력규모"
        if w > best:
            best, label = w, lb
    return best, label


# 영문 금액($750B / $30 billion / $5B / $152 million). 브리프에 실리는 영문
# 원문(투자·펀딩·M&A·DC 투자)이 한글 단위가 없어 규모 신호를 못 받던 결함을 메운다.
RE_USD = re.compile(r"\$\s?(\d[\d,\.]*)\s?(billion|bn\b|B\b|million|mn\b|M\b|trillion|T\b)", re.I)


def usd_signal(text):
    """달러 금액을 실제 크기로 환산해 등급을 매긴다. (점수, 근거) 반환.

    예전엔 billion 이면 무조건 +3 이었다. 그러면 $1.7B 스타트업 펀딩과
    $950B 국가급 딜이 같은 점수가 된다. 실제 브리프는 후자를 훨씬 크게 다루므로
    자릿수에 따라 층을 나눈다. (07-27 'Samsung·SK $950B deals' 3점,
    07-23 'OpenAI $30B Georgia DC' 3점이 임계 아래 있던 사례)
    """
    best, label = 0, None
    for m in RE_USD.finditer(text):
        try:
            n = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        u = m.group(2).lower()
        if u.startswith("t"):
            n *= 1_000_000
        elif u.startswith(("b", "bn")):
            n *= 1_000
        # million 은 그대로(단위: 백만 달러)
        if n >= 100_000:                      # $100B 이상 = 국가·빅테크 최상급
            w, lb = 5, "$100B+"
        elif n >= 10_000:                     # $10B 이상
            w, lb = 4, "$10B+"
        elif n >= 1_000:                      # $1B 이상
            w, lb = 3, "$1B+"
        elif n >= 100:                        # $100M 이상 = 대형 펀딩
            w, lb = 2, "$100M+"
        else:
            w, lb = 1, "$M"
        if w > best:
            best, label = w, lb
    return best, label


def scale_signal(text):
    """제목에서 수치+단위를 직접 파싱. (점수, 근거) 반환."""
    best, label = usd_signal(text)
    for fn in (krw_fx_signal, power_signal):
        w, lb = fn(text)
        if w > best:
            best, label = w, lb
    for rx, w, name in SCALE_UNIT:
        if rx.search(text):
            if w > best:
                best, label = w, name
    return best, label


def score(rep, group, cfg, cat=None):
    sig = cfg.get("impact_signals", {})
    t = " ".join(txt(x) for x in group)          # 클러스터 전체 텍스트로 신호 탐색
    s, why = 0, []

    for name, w in (("high_weight", 3), ("medium_weight", 2), ("low_weight", 1)):
        h = hit(t, sig.get(name))
        if h:
            s += w
            why.append("%s+%d(%s)" % (name.split("_")[0], w, h[0]))

    # 사안 규모 어휘 (조 파라미터, 초대형 등)
    sc = hit(t, sig.get("scale_weight"))
    if sc:
        s += 2
        why.append("규모어휘+2(%s)" % sc[0])

    # 제목 속 수치를 직접 파싱한 규모 신호
    ns, nl = scale_signal(t)
    if ns:
        s += ns
        why.append("수치규모+%d(%s)" % (ns, nl))

    # [2026-08-04] 주제 적합도. 이 브리프의 정식 카테고리로 분류된 건에 가점한다.
    #   그전까지 채점은 '무슨 행위인가(인수·출시)'와 '얼마나 큰가(수치)'만 봤고
    #   '이 브리프가 다루는 주제인가'는 아예 점수에 없었다. 그래서 정면으로
    #   주제에 걸리는 기사가 1~2점에 머물러 컷에 걸렸다.
    #     실측 08-04: 'NTT데이터 AI 데이터센터 1.5조 엔 투입' 1점,
    #                 'EU AI법 전격 시행' 2점, '지방 AIDC 전력평가 병목' 2점.
    #   카테고리는 이미 분류돼 있으므로 새 키워드 사전을 만들지 않는다.
    #   '기타'는 주제 밖이라는 뜻이므로 가점하지 않는다.
    cat = cat or ""
    if cat and cat not in ("기타",):
        s += 2
        why.append("주제적합+2(%s)" % cat)

    # 프런티어 주체의 발표는 단일 보도라도 브리프감
    fp = hit(t, sig.get("frontier_players"))
    if fp:
        s += 2
        why.append("프런티어+2(%s)" % fp[0])

    # 보도 매체 수. 강한 신호지만 백테스트가 며칠 지나 재수집하면 저평가되므로
    # 예전(+4)보다 비중을 낮춘다. 규모·주체 신호가 이를 보완한다.
    outlets = {x.get("outlet") for x in group if x.get("outlet")}
    cl = max([x.get("cluster", 1) for x in group] + [len(outlets), len(group)])
    if cl >= 11:
        s += 3
        why.append("보도수+3(%d)" % cl)
    elif cl >= 6:
        s += 2
        why.append("보도수+2(%d)" % cl)
    elif cl >= 3:
        s += 1
        why.append("보도수+1(%d)" % cl)

    # 서로 다른 수집 경로가 동시에 집은 사건
    srcs = {x["src"] for x in group}
    if len(srcs) > 1:
        s += 2 * (len(srcs) - 1)
        why.append("교차소스+%d(%s)" % (2 * (len(srcs) - 1), "+".join(sorted(srcs))))

    # 큐레이션 가산은 '누가 골랐는가'로 나눈다.
    if srcs & CURATED_HUMAN:
        # [2026-08-27 사용자 지시] +3 -> +5 상향.
        #   실측 08-27: 정답지 18건 중 tier0 통로가 14건(78%)인데 내 30건은
        #   13건(43%)이었다. 놓친 8건 중 5건이 이 신호가 붙은 NewsClips·삼성
        #   건이었다(남부권 전기료 3점, AI 인간생각 5점, 李-이재용 10점,
        #   엔비디아·앤트로픽 크레딧 15점, 통신3사 모두의AI 13점).
        s += 5
        why.append("사람큐레이션+5")
    elif srcs & CURATED_AI:
        s += 1
        why.append("AI큐레이션+1")

    # 여러 discovery 쿼리에 동시에 걸림 = 주제 적합도
    qn = max([len(x.get("queries") or []) for x in group] + [0])
    if qn >= 3:
        s += 2
        why.append("복수쿼리+2(%d)" % qn)
    elif qn == 2:
        s += 1
        why.append("복수쿼리+1")

    if hit(t, sig.get("competitor")):
        s += 2
        why.append("경쟁사+2")

    # 소버린 AI(국산 파운데이션 모델). 규모·프런티어 신호가 없어 체계적으로
    # 저평가되던 SKT 전략 관심 주제(엑사원·솔라·하이퍼클로바 등)를 보정한다.
    sov = hit(t, sig.get("sovereign_weight"))
    if sov:
        s += 2
        why.append("소버린+2(%s)" % sov[0])

    # 지정학(미중 기술패권·수출통제). 기업·규모 신호 없이도 AI 산업의 핵심 사안.
    geo = hit(t, sig.get("geopolitics_weight"))
    if geo:
        s += 2
        why.append("지정학+2(%s)" % geo[0])

    # [2026-09-23] 반향 어휘(제3자 반응). 돌풍·열광·반응 폭발·잇따라·trending·viral…
    #   실측 09-21~23: '제브' 기사 8건 중 5건 제목에 이 낱말이 있었는데 점수에 없었다.
    #   낱말 자체가 사건은 아니라 +2 에 둔다(소버린·지정학과 같은 층위).
    bz = hit(t, sig.get("buzz_words"))
    if bz:
        s += 2
        why.append("반향어휘+2(%s)" % bz[0])

    # 디지털트윈. [2026-08-08 사용자 지시] 사내 R&D 분야라 상시 관심 주제다.
    #   실측 08-07: 사람이 실은 '콘테크 도입 가속'(3점)과 '경북도 가상공장
    #   플랫폼'(8점)이 둘 다 이 신호 하나 없이 저점에 묻혀 탈락했다.
    dtw = hit(t, sig.get("digitaltwin_weight"))
    if dtw:
        s += 2
        why.append("디지털트윈+2(%s)" % dtw[0])

    vip = []
    for grp in (cfg.get("people", {}).get("vip_boost") or {}).values():
        vip += hit(t, grp)
    if vip:
        s += 1
        why.append("VIP+1(%s)" % vip[0])

    d, dwhy = demote_score(rep, cfg)
    if d:
        s += d
    if dwhy:
        why.append(dwhy)

    # 딜(투자·M&A·자금조달) 가점 +3.
    # [2026-08-06 사용자 지시] "나는 투자, M&A, 자금 조달 이런 것에 관심이 있어."
    #   실적 강등(-5)과 짝을 이룬다. 같은 회사·같은 날이라도 앵글이 딜이면
    #   올리고 실적이면 내린다. 둘 다 걸리면 상쇄돼 -2 가 되는데, 이건
    #   '실적 발표에서 파생된 투자 계획' 같은 회색 지대라 의도한 결과다.
    deal = hit(t, sig.get("deal_boost"))
    if deal:
        s += 3
        why.append("딜+3(%s)" % deal[0])

    # 순수 분기실적 강등(-5). 클러스터 텍스트로 판정하므로 멤버 중 하나라도
    # AI 투자 내러티브(현금흐름·자본지출 등)를 담으면 면제된다(Meta/Google/Tesla).
    earn = hit(t, sig.get("earnings_demote")) or \
        re.search(r"(분기\s*)?매출\s*[\d,\.]+\s*(조|억\s*달러)", t)
    if earn and not hit(t, sig.get("earnings_exempt")):
        s -= 5
        lbl = earn[0] if isinstance(earn, list) else "매출규모"
        why.append("실적강등-5(%s)" % lbl)

    # SKT 본체가 주어인 기사는 강등(-8). 사용자가 원하는 건 경쟁사·트렌드다.
    # 언급만 된 기사(엔비디아가 주어)는 빼면 안 되므로 주어 판정을 거친다.
    self_sub = is_self_subject(rep, sig)
    if self_sub:
        s -= 8
        why.append("자사강등-8(%s)" % self_sub)
        return s, why, False

    # 자사·그룹사 관련.
    # 처음엔 무조건 최상단으로 올렸는데(FLOOR 방식) SK하이닉스만 89건이 걸려
    # 랭킹 자체가 무의미해졌다. 지금은 가산점만 주고 별도 트랙으로 뽑는다.
    mr = hit(t, sig.get("must_review"))
    if mr:
        s += 3
        why.append("자사관련+3(%s)" % mr[0])
        return s, why, True
    return s, why, False


def tag_orgs(item, cfg):
    t = txt(item)
    return [o for o, k in (cfg.get("org_tagging") or {}).items() if hit(t, k)]


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--tier0-date", help="tier0 파일 날짜가 다를 때")
    ap.add_argument("--min-score", type=int, default=6, help="이 점수 이상만 채택")
    ap.add_argument("--max-items", type=int, default=40, help="링크 해결 상한")
    ap.add_argument("--max-own", type=int, default=8, help="자사 별도 트랙 상한")
    ap.add_argument("--max-rescue", type=int, default=150,
                    help="구제 후보 상한 (컷 밖·링크보유. LLM 이 최종 선별)")
    ap.add_argument("--max-nolink", type=int, default=150,
                    help="링크 미확보 구제 풀 상한. 링크는 없지만 LLM 이 웹검색으로 "
                         "확보할 수 있는 후보를 여기에 담는다")
    ap.add_argument("--rescue-min-score", type=int, default=2,
                    help="구제 후보 최소 점수")
    ap.add_argument("--no-links", action="store_true", help="링크 해결 생략(빠른 확인)")
    ap.add_argument("--link-workers", type=int, default=8,
                    help="링크 해결 동시 실행 수. 1이면 기존 순차 방식. "
                         "네이버 API 가 429 를 주면 낮출 것")
    a = ap.parse_args()
    day = (datetime.date.fromisoformat(a.date) if a.date
           else datetime.date.today()).isoformat()

    cfg = yaml.safe_load(open(KW, encoding="utf-8"))
    t0 = load_tier0(a.tier0_date or day)
    tc = load_community(day)              # [2026-09-23] HN·GitHub·HF — tier0 급으로 취급
    if tc:
        t0 = t0 + tc
    t1 = load_tier1(day, cfg)
    t2 = load_tier2(day)
    # HTML 엔티티 정리. 구글뉴스/RSS 제목에 &#039; &amp; &quot; 가 그대로 실려
    # 토큰화(예: '&quot;엔비디아' 가 한 토큰)와 브리프 표시를 해친다. 한 곳에서
    # 풀어 두면 클러스터링·채점·매칭·출력이 모두 깨끗한 제목을 쓴다.
    for it in t0 + t1 + t2:
        for k in ("title", "summary"):
            if it.get(k):
                it[k] = html.unescape(it[k])
    log("입력  tier0 %d(커뮤니티 %d 포함) / tier1(RSS) %d / tier2(구글) %d  = %d건"
        % (len(t0), len(tc), len(t1), len(t2), len(t0) + len(t1) + len(t2)))
    if not (t0 or t1 or t2):
        log("입력이 없다. ingest_tier0.py / ingest_tier2.py 를 먼저 실행할 것")
        sys.exit(1)

    # 발간일 창 필터. tier0 는 큐레이션(당일 뉴스레터)이라 미상도 허용,
    # tier1/tier2 는 load 단계에서 이미 창 필터를 통과함.
    pol = cfg.get("policy", {})
    d = datetime.date.fromisoformat(day)
    win = DW.window_days(pol, d)          # 공휴일 보정 포함(연휴 다음날은 창 확대)
    lo, hi = (d - datetime.timedelta(days=win)).isoformat(), d.isoformat()
    allow_undated_trusted = pol.get("allow_undated_trusted", True)

    def in_window(it):
        pd = (it.get("pub_date") or "")[:10]
        if not pd:
            return allow_undated_trusted if it["src"] in TRUSTED else False
        return lo <= pd <= hi

    # AI 가 고른 소스(에이닷/슬랙)는 잡음이 섞이므로 relevance 필터를 건다.
    # 사람이 고른 삼성 소스는 면제.
    rel = cfg.get("relevance", {})
    items, filtered, out_of_window = [], [], 0
    for it in t0 + t1 + t2:
        if not in_window(it):
            out_of_window += 1
            continue
        if it["src"] in FILTER_EXEMPT:
            items.append(it)
            continue
        t = txt(it)
        bad = hit(t, rel.get("must_none"))
        if bad:
            filtered.append((it, bad[0]))
            continue
        items.append(it)
    log("발간일 창 밖 제외 %d건 (창 %s ~ %s)" % (out_of_window, lo, hi))
    if filtered:
        log("잡음 제외 %d건 (예: %s)"
            % (len(filtered), " / ".join(
                "%s[%s]" % ((x.get("title") or "")[:26], w)
                for x, w in filtered[:4])))

    canon = build_entity_canon(cfg)
    groups = cluster(items, canon)
    # 임팩트 전파: 클러스터 안 큐레이션 멤버의 impact 를 같은 클러스터 전원에
    # 올려 둔다. 아래 '한글 우선' 단계에서 영문 큐레이션 멤버가 버려져도
    # 국문 대표가 impact 를 그대로 이어받게 하기 위함(안 하면 점수가 증발한다).
    for g in groups:
        imp = max([x.get("impact") or 0 for x in g] + [0])
        if imp:
            for x in g:
                if (x.get("impact") or 0) < imp:
                    x["impact"] = imp
    # 한글 우선 정책: 클러스터 안에 한글 기사가 있으면 영문(비한글) 멤버를 버린다.
    # 같은 사건을 국내가 다뤘으면 국내 기사로만 브리프를 낸다.
    if pol.get("drop_english_when_korean_exists", True):
        dropped_en = 0
        newgroups = []
        for g in groups:
            has_ko = any(is_korean(x) for x in g)
            if has_ko:
                # [2026-08-11] tier0 멤버는 영문이어도 남긴다.
                #   이 규칙의 목적은 '대표를 국문으로 뽑는 것'인데, 멤버를
                #   아예 지우면 클러스터의 sources 에서 tier0 표시가 사라져
                #   아래 tier0 고정 핀이 그 클러스터를 못 잡는다.
                #   실측 2026-08-11: 삼성 큐레이션 영문 3건(리만 가설 진전,
                #   Claude Sonnet 5 가격, 메타 텍사스 DC 규제)이 이 경로로
                #   통째로 사라졌고 그중 '리만 가설'은 사람이 실제로 발송했다.
                #   대표 선정(pick_representative)은 한글을 최우선으로 두므로
                #   멤버를 남겨도 브리프에 영문 대표가 나가지 않는다.
                kept = [x for x in g if is_korean(x) or x["src"] in TRUSTED]
                dropped_en += len(g) - len(kept)
                newgroups.append(kept)
            else:
                newgroups.append(g)          # 전부 영문 = 국내 미보도 글로벌 이슈, 유지
        groups = newgroups
        log("한글 우선: 영문 중복 멤버 %d건 제외" % dropped_en)

    log("클러스터 %d개 (평균 %.2f건, 최대 %d건)"
        % (len(groups), len(items) / len(groups), max(len(g) for g in groups)))

    rows = []
    row_group = {}          # id(row) -> 원본 클러스터. tier0 보존 점검 전용
    for g in groups:
        rep = pick_representative(g)
        # 카테고리를 먼저 정한다. score() 가 주제 적합도 가점에 쓴다.
        cat = categorize(rep, cfg)
        sc, why, must = score(rep, g, cfg, cat)
        rows.append({
            "title": rep.get("title"), "summary": rep.get("summary"),
            "outlet": rep.get("outlet"), "url": rep.get("url"),
            "pub_date": rep.get("pub_date"), "confidence": rep.get("confidence"),
            "category": categorize(rep, cfg), "orgs": tag_orgs(rep, cfg),
            "score": sc, "must_review": must, "why": why,
            "size": len(g), "sources": sorted({x["src"] for x in g}),
            "outlets": sorted({x.get("outlet") for x in g if x.get("outlet")})[:6],
            # 클러스터 멤버 표면 제목. compare.py 가 한/영 표기가 달라도 대조할 수
            # 있게(대표는 한글이어도 영문 멤버로 매칭) 원문 표기를 함께 남긴다.
            "members": sorted({(x.get("title") or x.get("summary") or "").strip()
                               for x in g if (x.get("title") or x.get("summary"))})[:12],
            # 멤버가 수집 시점에 이미 갖고 있던 원문 링크(tier0 직링크·tier1 RSS).
            # compare.py 가 사람 브리프의 링크와 대조해 '같은 기사'를 어휘 추정이
            # 아니라 확정으로 판정하는 데 쓴다. 링크 해결 전이라 없는 멤버도 많다.
            "member_urls": sorted({x.get("url") for x in g if x.get("url")})[:20],
        })
        # [2026-08-11] tier0 보존 점검용. members/member_urls 는 12·20건에서
        #   잘리므로(74건짜리 클러스터도 있다) 직렬화된 값으로 세면 오탐이
        #   난다. 원본 그룹을 id 로 붙여 두고 아래 점검에서만 쓴다.
        row_group[id(rows[-1])] = g
    rows.sort(key=lambda r: -r["score"])

    # [2026-09-23] 반향(버즈) 후보. 임원 피드백: "타입세이프AI·오픈클로처럼 새로운
    #   반향을 일으키는 이슈를 빠르게 캐치하고 싶다." 실측 09-21~23: '제브'가 tier2 에
    #   사흘간 8건 들어왔는데 사전에 없는 이름이라 점수 미달로 판정에 못 갔다.
    #   buzz.py 가 '30일간 없던 낱말 + 제3자 반응·커뮤니티' 로 뽑은 낱말이 제목에
    #   있으면 +3 을 주고, 점수와 무관하게 컷에 고정한다(tier0 와 같은 취급).
    #   **판정을 대신하지 않는다.** 검토 대상으로 끌어올릴 뿐이다.
    buzz_terms = load_buzz(day)
    n_buzz = 0
    if buzz_terms:
        for r in rows:
            text = " ".join([r.get("title") or ""] + (r.get("members") or [])).lower()
            hit_b = [b for b in buzz_terms if _occurs((b.get("term") or "").lower(), text)]
            if hit_b:
                b = hit_b[0]
                r["score"] += 3
                r["why"].append("반향+3(%s·매체%d·%d일·반응%d·커뮤니티%d)" % (
                    b.get("term"), b.get("n_outlets", 0), b.get("days", 0),
                    b.get("reaction", 0), b.get("community", 0)))
                r["buzz"] = b.get("term")
                n_buzz += 1
        rows.sort(key=lambda r: -r["score"])
        log("반향 낱말 %d개 -> 후보 %d건에 +3·컷 고정 (%s)"
            % (len(buzz_terms), n_buzz, ", ".join(b.get("term", "") for b in buzz_terms[:6])))

    dist = Counter(min(r["score"], 20) for r in rows)
    log("점수분포: " + ", ".join("%d점 %d건" % (k, v)
                                for k, v in sorted(dist.items(), reverse=True)[:10]))

    # [2026-08-03] tier0(슬랙·에이닷·삼성·큐레이션)에서 온 항목은 컷에 무조건 넣는다.
    #   그날 발행일 창을 3일로 넓히자 tier2 후보가 2,758 -> 4,385 건으로 늘었고,
    #   그 여파로 '[AI DC 완판 흥행 속 1.3조 추가 투자...LGU+]'(에이닷 큐레이션,
    #   사람이 실제 발송)이 상위 300 컷 밖으로 밀려 후보 목록에서 사라졌다.
    #   tier0 는 하루 60~70건뿐이고 이미 사람/봇이 한 번 거른 것이라
    #   기계 점수로 다시 떨어뜨릴 이유가 없다. 컷을 넓히는 대신 이것만 고정한다.
    CURATED = {"slack", "adot", "samsung", "newsclips", "aibrief",
               "community"}   # [2026-09-23] HN·GitHub·HF 도 컷 고정

    def from_tier0(r):
        # 클러스터 단위 레코드라 개별 src 가 아니라 sources(집합)를 본다.
        # [2026-09-23] 반향 낱말이 붙은 건도 고정한다(buzz.py).
        return bool(set(r.get("sources") or []) & CURATED) or bool(r.get("buzz"))

    scored = [r for r in rows if r["score"] >= a.min_score]
    cut = scored[:a.max_items]
    incut = {id(r) for r in cut}
    pinned = [r for r in rows if from_tier0(r) and id(r) not in incut]
    if pinned:
        cut = cut + pinned
        log("tier0 큐레이션 %d건을 컷에 고정 추가 (컷 %d -> %d건)"
            % (len(pinned), a.max_items, len(cut)))

    # [2026-08-11] tier0 보존 점검.
    #   지시상 tier0 는 후보 단계까지 전량 살아 있어야 하는데, 새는 경로가
    #   여러 군데(발간일 창, 잡음 필터, 한글 우선 멤버 제거, 클러스터 병합)라
    #   조용히 사라진다. 실제로 08-11 에 삼성 큐레이션 3건이 이렇게 없어졌고
    #   그중 1건이 정답지였다. 여기서 세어 로그로 남긴다.
    t0_in = [x for x in items if x["src"] in CURATED]
    covered = set()
    for r in cut:
        for x in row_group.get(id(r), ()):
            covered.add(id(x))
    lost = [x for x in t0_in if id(x) not in covered]
    if lost:
        log("[경고] tier0 %d/%d건이 후보에 없다: %s"
            % (len(lost), len(t0_in),
               " / ".join((x.get("title") or x.get("summary") or "")[:34]
                          for x in lost[:3])))
    else:
        log("tier0 보존 점검 OK (%d건 전량 후보 진입)" % len(t0_in))
    # 자사 관련은 점수가 낮아도 놓치면 안 되므로 별도 트랙으로 따로 뽑는다.
    # (7/21 "저희에게 좀 아픈 기사" 유형)
    own = [r for r in rows if r["must_review"] and r not in cut][:a.max_own]

    # 구제 후보(rescue): 컷 밖이지만 '이미 링크를 가진' 항목.
    #
    # [왜 필요한가] 기계 점수는 은유적 제목이나 키워드 없는 사안을 저평가한다.
    # 실측(07-30): 사람 브리프에 실린 '저커버그 개인 AI 에이전트'가 146위,
    # 'OpenAI 학술 연구자 무료'가 447위였다. 컷(상위 N)만 LLM 에 넘기면
    # 이런 항목은 선별 후보에조차 오르지 못한다.
    #
    # [왜 링크 보유분만인가] 링크 해결은 건당 1~3초다. 81~400위를 전부 해결하면
    # 220건 x 수초 = 수 분이 걸려 운영 창을 넘긴다. 반면 tier0 직링크와
    # [2026-08-11] 원래 '09:35 운영 창'이라고 적혀 있었는데 스케줄이 바뀐 뒤
    #   갱신되지 않은 값이었다. 현재 daily-tech-brief 는 평일 10:05 실행이다
    #   (cron 0 10 * * 1-5). 시각을 다시 박아두면 또 낡으므로 적지 않는다.
    # tier1 RSS 링크를 이미 가진 항목은 해결이 불필요해 비용이 0이다.
    # 실측 07-30 기준 이 방식으로 114건을 공짜로 확보했다.
    picked = {id(r) for r in cut} | {id(r) for r in own}
    rescue = [r for r in rows
              if id(r) not in picked
              and r.get("url")
              and r["score"] >= a.rescue_min_score][:a.max_rescue]
    log("임계 %d점 이상 -> %d건 채택 | 자사 별도 %d건 | 구제후보 %d건 "
        "(링크보유·해결생략) (전체 %d 클러스터)"
        % (a.min_score, len(cut), len(own), len(rescue), len(rows)))

    # 링크 미확보 구제 풀(rescue_nolink).
    #
    # [왜 필요한가 - 실측 2026-07-31]
    #   위 rescue 는 '링크 보유'가 조건이다. 그래서 링크가 없는 저점수 기사는
    #   구제 자체가 불가능했다. 그런데 사람 브리프에 실제로 실린 기사 중
    #   이 사유로 사라진 것이 여전히 5건 남아 있었다(점수 12·7·4·3·2).
    #   링크는 나중에 LLM 이 웹검색으로 찾아줄 수 있다(삼성 섹션에서 11건 중
    #   10건 확보, 정밀도 100% 로 입증됨). 링크가 없다는 이유로 '선별 기회'
    #   자체를 막는 것이 문제였다.
    #
    # [왜 rescue 를 고치지 않고 따로 두는가]
    #   brief.py 는 rescue 에서 url 보유분만 쓴다. rescue 에 링크 없는 항목을
    #   섞으면 그쪽 계약이 깨진다. 별도 키로 내보내고, 프롬프트에 싣는 일은
    #   cowork/prompt_extend.py 가 '[링크 미확보 후보]' 섹션으로 처리한다.
    #   메인 트랙의 '링크는 결정론 단계에서 확정' 원칙은 그대로 유지된다.
    #
    #   실측상 점수 상위 150건을 담으면 위 5건 중 3건이 들어온다. 나머지 2건은
    #   2~3점이라 이 방식으로는 닿지 않는다(별도 접근이 필요하다).
    picked_all = picked | {id(r) for r in rescue}
    # [2026-08-11] 이 목록은 점수순 150건이라 저점수 tier0 는 창 밖으로
    #   밀릴 수 있다. 다만 tier0 는 위에서 전량 cut 에 고정되므로 여기
    #   picked_all 에 걸려 애초에 들어오지 않고, 링크가 없는 tier0 는
    #   prompt_extend 가 items 안의 url 없는 항목을 따로 긁어
    #   '[링크 미확보 후보]' 맨 앞에 올린다. 그 경로로 프롬프트에 닿는다.
    rescue_nolink = [r for r in rows
                     if id(r) not in picked_all
                     and not r.get("url")
                     and r["score"] >= a.rescue_min_score][:a.max_nolink]
    log("링크미확보 구제 풀 %d건 (점수 %s~%s)"
        % (len(rescue_nolink),
           rescue_nolink[-1]["score"] if rescue_nolink else "-",
           rescue_nolink[0]["score"] if rescue_nolink else "-"))

    # [2026-08-04] 키가 없으면 크게 경고한다.
    #   naver_api 는 키가 없으면 not_configured 를 돌려주고, resolve_link 는
    #   조용히 다음 경로로 넘어간다. 결과적으로 전량 실패인데 로그에는
    #   '링크 확보 111/256' 같은 그럴듯한 숫자만 남는다. 실제로 08-04 가
    #   그 상태로 돌아 사람이 실은 3건을 통째로 놓쳤다.
    #   (run.sh 를 안 거치고 python3 refine.py 를 직접 부르면 이렇게 된다)
    if not a.no_links and not (os.environ.get("NAVER_CLIENT_ID")
                               and os.environ.get("NAVER_CLIENT_SECRET")):
        log("!! 네이버 API 키 없음 - 링크 해결이 전량 실패합니다 !!")
        log("   run.sh 를 통해 실행하세요(secrets.env 를 읽습니다).")

    # 링크 해결은 여기서. 살아남은 것만.
    if not a.no_links:
        dd = datetime.date.fromisoformat(day)
        rlo, rhi = dd - datetime.timedelta(days=win), dd
        need = [r for r in cut + own if not r["url"]]
        log("링크 해결 %d건... (workers=%d)" % (len(need), a.link_workers))

        # [병렬화 2026-07-31]
        #   항목별로 완전히 독립적인 작업인데 순차로 돌고 있었다. max-items 를
        #   160 -> 300 으로 올리면 42초를 넘겨(Cowork bash 45초 제한) 실행 자체가
        #   불가능했고, 그래서 컷을 못 넓혔다. 컷을 못 넓히면 저점수 기사가 링크
        #   해결 단계에 도달하지 못하고, 링크가 없으면 brief.py 에서 제외된다.
        #   실측상 사람 브리프 손실의 27%가 이 경로였다.
        #
        #   resolve_link 는 호출마다 자기 sqlite 커넥션을 열고 HTTP 도 자기 것만
        #   쓰므로 스레드 간 공유 상태가 없다. 결과를 원소별로 되돌려 받아 쓰기
        #   때문에 순서에 의존하지 않는다(순차 실행과 결과가 같아야 정상이다).
        #   [예외를 삼키면 안 된다] 처음엔 실패를 그냥 None 으로 넘겼더니 순차 대비
        #   링크가 10건 사라졌다. 동시 요청이 늘면 네이버·웹문서 쪽에서 순간적으로
        #   거절(429/타임아웃)이 나는데, 그걸 '해당 기사 없음'과 구분하지 못해
        #   멀쩡한 링크를 영구히 버린 것이다. 실패는 세어서 로그로 남기고,
        #   짧게 쉬었다가 한 번 더 시도한다.
        errs = []

        def _resolve(r):
            head = r.get("title") or r.get("summary")
            if not head:
                return r, None
            for attempt in (0, 1):
                try:
                    return r, SR.resolve_link(head, r["outlet"] or "", rlo, rhi)
                except Exception as e:
                    if attempt == 0:
                        time.sleep(0.7)
                        continue
                    errs.append("%s (%s)" % (head[:30], str(e)[:40]))
            return r, None

        if a.link_workers > 1 and need:
            with ThreadPoolExecutor(max_workers=a.link_workers) as ex:
                results = list(ex.map(_resolve, need))
        else:
            results = [_resolve(r) for r in need]
        for r, link in results:
            if link:
                r["url"], r["confidence"] = link["url"], "auto"
                r["url_via"] = link["via"]
        if errs:
            log("링크 해결 중 예외 %d건(재시도 후에도 실패): %s"
                % (len(errs), " | ".join(errs[:3])))
        # 최종 방어선: URL 에 박힌 발행일(YYYYMMDD)이 창 밖이면 링크를 버린다.
        # (웹문서 검색 등 발행일을 못 받는 경로가 과거 기사를 물어오는 것 차단.
        #  예: fnnews '...202603022110...' = 3월 기사)
        stripped = 0
        for r in cut + own:
            ud = url_date(r.get("url"))
            if ud and not (rlo.isoformat() <= ud <= rhi.isoformat()):
                r["url"], r["url_via"] = None, None
                r["confidence"] = "unresolved"
                stripped += 1
        if stripped:
            log("URL 발행일 창 밖 링크 제거 %d건" % stripped)
        log("링크 확보 %d/%d" % (sum(1 for r in cut + own if r["url"]), len(cut + own)))

    lines = ["%s  브리프 후보  (임계 %d점, %d건)" % (day, a.min_score, len(cut)),
             "=" * 68]
    for r in cut:
        lines.append("%s[%2d] [%s] %s"
                     % ("★자사 " if r["must_review"] else "      ", r["score"],
                        r["category"], (r["title"] or r["summary"] or "")[:50]))
        lines.append("            %s | %s | 묶임%d | %s"
                     % (r["outlet"] or "?", ",".join(r["orgs"]) or "-",
                        r["size"], "+".join(r["sources"])))
        lines.append("            %s" % " ".join(r["why"]))
        lines.append("            %s" % (r["url"] or "링크없음"))
    if own:
        lines += ["", "-" * 68, "★ 자사·그룹 관련 (임계 미달이나 반드시 확인)", "-" * 68]
        for r in own:
            lines.append("      [%2d] [%s] %s"
                         % (r["score"], r["category"],
                            (r["title"] or r["summary"] or "")[:50]))
            lines.append("            %s | %s" % (r["outlet"] or "?", r["url"] or "링크없음"))
    lines += ["", "카테고리 분포:"]
    for c, v in Counter(r["category"] for r in cut).most_common():
        lines.append("  %-16s %d건" % (c, v))

    os.makedirs(OUTDIR, exist_ok=True)
    # rescue 는 페이로드를 가볍게(선별에 필요한 최소 필드만) 실어 보낸다.
    # brief.py 가 '구제 후보' 2단으로 프롬프트에 붙인다.
    rescue_slim = [{k: r.get(k) for k in
                    ("title", "summary", "outlet", "url", "pub_date",
                     "score", "category")} for r in rescue]
    # 링크 미확보 구제 풀. brief.py 는 이 키를 읽지 않는다(기존 계약 유지).
    # cowork/prompt_extend.py 가 '[링크 미확보 후보]' 섹션으로 프롬프트에 붙인다.
    nolink_slim = [{k: r.get(k) for k in
                    ("title", "summary", "outlet", "pub_date",
                     "score", "category", "size", "why", "sources")}
                   for r in rescue_nolink]
    json.dump({"date": day, "min_score": a.min_score, "count": len(cut),
               "total_clusters": len(rows), "items": cut, "own_track": own,
               "rescue": rescue_slim, "rescue_nolink": nolink_slim},
              open(os.path.join(OUTDIR, "refined-%s.json" % day), "w",
                   encoding="utf-8"), ensure_ascii=False, indent=1)
    open(os.path.join(OUTDIR, "preview-%s.txt" % day), "w",
         encoding="utf-8").write("\n".join(lines))
    # 임계값 보정을 위해 전체 랭킹도 남긴다 (compare.py 가 쓴다)
    json.dump({"date": day, "items": [
        {k: r.get(k) for k in ("title", "summary", "outlet", "score", "why",
                           "category", "size", "sources", "must_review", "buzz",
                           "members", "member_urls")}
        for r in rows]},
        open(os.path.join(OUTDIR, "ranked-%s.json" % day), "w",
             encoding="utf-8"), ensure_ascii=False)
    log("저장: out/refined-%s.json, out/preview-%s.txt, out/ranked-%s.json"
        % (day, day, day))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
