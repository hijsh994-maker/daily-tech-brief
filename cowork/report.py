# -*- coding: utf-8 -*-
"""
report.py - 하루치 정답지 대조 결과를 엑셀 한 장으로 만든다.

## 왜 필요한가
scorecard.jsonl 은 기계가 읽기엔 좋지만 사람이 열어보긴 나쁘다. 매일
"몇 건 맞았고, 못 맞춘 건 왜 못 맞췄나"를 바로 보려면 표가 있어야 한다.
사유는 4가지로 나뉘고 각각 고쳐야 할 곳이 다르므로, 그 설명까지 같은 줄에 적는다.

시트 구성
  요약        건수·일치율·사유별 집계 (일치율은 수식)
  정답지대조  사람이 발송한 기사 한 줄씩. 매칭된 내 기사와 못 맞춘 사유
  내선별      내가 고른 기사 한 줄씩. 정답지와 겹쳤는지
  추이        날짜별 누적 (scorecard.jsonl 전체)

사용:
  python3 cowork/report.py --date 2026-08-04
"""
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import tempfile
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(BASE, "out")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scorecard import (norm_url, same_event, bigrams, read_ledger,  # noqa: E402
                       classify, load_json, parse_brief, load_false_hits)
from scorecard import BUCKET_THR                                   # noqa: E402
from match import (match_all, build_clusters, build_idf,           # noqa: E402
                   resolve_by_cluster)
from match import title_sim as _title_sim                          # noqa: E402
from match import _fold as _m_fold, STOP as _M_STOP                # noqa: E402

# [2026-08-19] E열 오탐 차단용. 아래 _same_ev 주석 참조.
#   그날 지면에 깔려 변별력이 없는 말. 이것만 겹치면 같은 사건이 아니다.
ROUTE_COMMON = {"ai", "인공지능", "반도체", "데이터", "출시", "공개",
                "투자", "기업", "사업", "기술", "모델", "서비스",
                "협력", "확대", "개발"}


# 조사. 'microsoft의' 처럼 조사가 붙은 채 토큰이 되면 그 형태는 그날
#   한 번만 나와 IDF 가 높게 잡히고, 흔한 말이 고유명사 행세를 한다
#   (실측 08-19: 'microsoft의' IDF 7.9 vs 'microsoft' 4.6).
JOSA = ("으로써", "으로서", "에서는", "에게서", "이라는", "라는", "으로",
        "에서", "에게", "까지", "부터", "이나", "라도", "마다", "조차",
        "처럼", "보다", "와의", "과의", "의", "는", "은", "이", "가",
        "을", "를", "에", "와", "과", "로", "도", "만", "및")
_JOSA_SORTED = sorted(JOSA, key=len, reverse=True)
# 공유 단어가 하나뿐일 때 그것만으로 같은 사건이라고 볼 IDF 문턱.
#   'a2a'(8.3) 는 통과, '에이전틱'(5.9)·'microsoft'(4.6) 은 탈락한다.
RARE_IDF = 7.0
# 공유단어 가드를 면제할 만큼 제목 유사도가 높은 지점.
#   08-19 에 관측된 오탐의 최대치가 0.529('불닭으로 번 돈 삼성전자')였다.
#   그 위라면 우연히 나오는 값이 아니라고 보고 공유단어를 따지지 않는다.
STRONG_SIM = 0.55
# 정답지와 후보의 **매체가 같을 때** 쓰는 낮춘 문턱.
#   같은 날 같은 매체라는 조건이 이미 강한 제약이라 여기까지 내려도
#   무관 기사가 붙지 않는다(실측 08-25 'AR 글라스' 정탐 0.286).
OUTLET_THR = 0.22


def _strip_josa(w):
    for j in _JOSA_SORTED:
        if len(w) > len(j) + 1 and w.endswith(j):
            return w[:-len(j)]
    return w


def _words(s):
    """원형 단어 집합. match.tokens 와 달리 한글 2-gram 조각을 넣지 않는다."""
    try:
        return {_strip_josa(w) for w in _m_fold(s).split()
                if w not in _M_STOP and len(w) >= 2}
    except Exception:
        return set()


def _word_idf(titles):
    """_words 기준 문서빈도로 만든 가중치. build_idf 와 토큰 정의가 다르다."""
    import math
    n = max(1, len(titles))
    df = {}
    for t in titles:
        for w in _words(t):
            df[w] = df.get(w, 0) + 1
    return {k: math.log(1.0 + n / float(v)) for k, v in df.items()}


def _same_ev(a, b, idf=None, widf=None, thr=None):
    """bigram 이 놓치는 국문<->영문 쌍을 title_sim 으로 함께 본다.

    E열(수집통로) 판정과 scorecard 의 사유 판정이 같은 기준을 쓰게 한다.
    둘이 갈리면 '수집은 됐다'와 '수집실패'가 한 파일 안에서 동시에 나온다.

    [2026-08-19] title_sim 단독으로는 E열이 통째로 못 믿을 값이 된다.
      scorecard 는 match_all 로 1:1 매칭을 하지만 route 는 'any' 매칭이라
      한 번이라도 걸리면 그 tier 를 적는다. 문턱만 넘으면 되니 오탐이 쌓인다.
      실측 08-19: 정답지 'MS의 AI 계획, 반도체 부족으로 발목'이 후보에
      아예 없었는데(The Guardian 원문 미수집) E열에 tier0+tier1+tier2 가
      찍혔다. match.tokens 가 '마이크로소프트'를 로마자로 접은 뒤
      2-gram('mi','ic','ro','so','os','ft'...)까지 토큰에 넣는 탓에,
      그 조각들이 'KT·농협·한국MS 농산물 유통' 같은 무관한 제목과 겹쳐
      sim 0.45~0.53 이 나왔다. '불닭으로 번 돈 삼성전자' 조차 0.529 였고,
      정탐인 유니트리 쌍(0.429)보다 높았다. 문턱 조정으로는 못 가른다.
      그래서 **원형 단어를 공유할 것**을 추가 조건으로 둔다. 2-gram
      조각과 흔한 말(ROUTE_COMMON)은 근거로 치지 않는다. 공유 단어가
      하나뿐이면 그 단어가 그날 기준으로 희귀할 때만(RARE_IDF) 인정한다.
      'a2a' 처럼 진짜 고유명사는 통과하고, '에이전틱'·'microsoft' 처럼
      그날 지면에 깔린 말은 걸러진다.
    """
    if same_event(a, b):
        return True
    try:
        sim = _title_sim(a, b, idf)
        # [2026-08-25] 공유단어 가드에 '고신뢰 면제'를 둔다.
        #   08-19 가드는 sim 0.45~0.53 짜리 오탐을 막으려고 넣은 것인데,
        #   그보다 훨씬 높은 정탐까지 같이 죽이고 있었다.
        #   실측 08-25: 정답지 '미·중 정상회담 앞두고 AI 외교전 심화' 와
        #   후보 '미·중 정상회담 한 달 앞… 달아오른 AI 신경전' 은
        #   sim 0.630 인데, 공유 원형단어가 '정상회담' 하나뿐이고 그날
        #   IDF 가 RARE_IDF 미달이라 탈락 -> E열이 '통로미상'이 됐다.
        #   오탐 사례의 최대치(0.529)보다 위인 STRONG_SIM 이상이면
        #   공유단어를 따지지 않는다.
        if sim >= STRONG_SIM:
            return True
        shared = (_words(a) & _words(b)) - ROUTE_COMMON
        if not shared:
            return False
        if len(shared) < 2:
            w = (widf or {})
            # [2026-08-25] 매체가 같아 문턱을 낮춰 보는 호출(thr 지정)에서는
            #   희귀도 조건을 면제한다. '같은 날 같은 매체'라는 조건이
            #   이미 후보를 그날 그 매체 기사로 좁혀 놓아서, 흔한 말 하나가
            #   겹치는 것만으로 무관 기사가 붙을 여지가 작다.
            if thr is None and max(w.get(x, 9.0) for x in shared) < RARE_IDF:
                return False
        return sim >= (BUCKET_THR if thr is None else thr)
    except Exception:
        return False

from openpyxl import Workbook                                       # noqa: E402
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side  # noqa: E402
from openpyxl.utils import get_column_letter                        # noqa: E402

FONT = "맑은 고딕"

# 수집 통로. refined/ranked 항목의 sources 값을 tier 로 접는다.
#   tier0 = 사람·봇이 한 번 거른 입력(슬랙 3_트렌드, 에이닷, 삼성, 지면 스크랩)
#   tier1 = 매체 RSS (collect.py)
#   tier2 = 구글뉴스 (tier2_par.py)
# [2026-08-25] 매핑을 cowork/tiers.py 로 일원화했다. 종전에는 여기와
#   persona.py / brief.py 세 곳에 화이트리스트가 복사돼 있었고, 셋 다
#   'curated' 가 빠져 있어 tier0 137건이 조용히 사라졌다. 자세한 경위는
#   tiers.py 주석 참조.
from tiers import tier_of as _tier_of_one, TIER_ORDER   # noqa: E402


class _TierMap(object):
    """기존 호출부(TIER_OF.get(s))를 그대로 두기 위한 얇은 어댑터."""

    @staticmethod
    def get(s, default=None):
        return _tier_of_one(s) or default


TIER_OF = _TierMap()


def _pools_from_prompt(day):
    """prompt-<day>.txt 의 주 후보 JSONL 을 build_route 가 쓰는 모양으로 바꾼다.

    prompt 는 tier 를 'tier0+tier1+tier2' 문자열로 들고 있으므로
    sources(원본 소스명) 로 되돌려 TIER_OF 를 그대로 태운다.
    [링크 미확보 후보]에는 tier 가 없어 수집은 됐다는 사실만 남기고
    통로는 비운다(오히려 틀린 tier 를 적는 것보다 낫다).
    """
    back = {"tier0": "slack", "tier1": "rss", "tier2": "gnews"}
    path = os.path.join(OUTDIR, "prompt-%s.txt" % day)
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not (line.startswith('{"idx"')
                    or line.startswith('{"nolink_idx"')):
                continue
            try:
                it = json.loads(line)
            except Exception:
                continue
            # 링크 미확보 후보는 tier·url 이 없다. 그래도 '수집은 됐다'는
            # 사실은 남겨야 한다. 안 그러면 수집 성공률이 과소 집계된다.
            srcs = [back[t] for t in str(it.get("tier") or "").split("+")
                    if t in back]
            out.append({"title": it.get("headline") or "",
                        "url": it.get("url") or "",
                        "sources": srcs})
    return out


def build_route(day):
    """정답지 기사 -> 수집 통로 라벨을 돌려주는 함수를 만든다.

    같은 사건이 여러 통로로 들어오는 게 정상이라(지면 스크랩 + 구글뉴스 등)
    라벨은 'tier0+tier2' 처럼 복수로 적는다. 수집 자체가 안 된 건은 ''.

    ranked 까지 훑는 이유: refined 는 컷·클러스터링을 거친 결과라
    '수집은 됐는데 컷에 걸린' 건이 빠질 수 있다. 수집 성공률을 보려면
    가장 넓은 풀을 봐야 한다.
    """
    rf = load_json(os.path.join(OUTDIR, "refined-%s.json" % day)) or {}
    rk = load_json(os.path.join(OUTDIR, "ranked-%s.json" % day)) or {}
    pools = ((rf.get("items") or []) + (rf.get("own_track") or [])
             + (rf.get("rescue") or []) + (rf.get("rescue_nolink") or [])
             + (rk.get("items") or []))

    # [2026-08-10] refined/ranked 가 없으면 prompt-<day>.txt 로 되짚는다.
    #   작업본(/tmp)이 세션 재시작으로 날아가면 이 두 파일만 사라지고
    #   prompt 는 마운트에 남는 일이 있다. 그때 pools 가 비어 route 가
    #   전부 '' -> 17행 모두 '수집실패' 로 찍혔다(실측 08-10).
    #   prompt 의 주 후보 JSONL 은 tier 문자열을 그대로 들고 있어 대체 가능.
    if not pools:
        pools = _pools_from_prompt(day)

    by_url, by_title = {}, []
    for it in pools:
        tiers = {TIER_OF.get(s) for s in (it.get("sources") or [])}
        tiers.discard(None)
        # [2026-08-10] tier 를 모르는 항목도 등록한다. 예전에는 여기서
        #   continue 로 버려서 '수집은 됐는데 통로만 모르는' 건이
        #   '수집실패'(빨강)로 찍혔다. 구제 후보는 prompt 에 tier 가 없다.
        urls = {norm_url(u) for u in (it.get("member_urls") or []) if u}
        if it.get("url"):
            urls.add(norm_url(it["url"]))
        for u in urls:
            by_url.setdefault(u, set()).update(tiers)
        titles = [it.get("title") or ""] + list(it.get("members") or [])
        # [2026-08-25] 매체명을 함께 싣는다. 아래 route 가 '매체 일치'를
        #   1급 신호로 쓰기 위해서다. 클러스터는 여러 매체를 품으므로
        #   outlet 하나가 아니라 outlets 까지 모아 집합으로 둔다.
        outs = {(it.get("outlet") or "").strip().lower()}
        for o in (it.get("outlets") or []):
            if o:
                outs.add(o.strip().lower())
        outs.discard("")
        by_title.append(([t for t in titles if t], tiers, outs))

    # [2026-08-11] tier0-<day>.json 을 별도로 더 본다. 두 가지 구멍이 있었다.
    #   (1) refined/ranked 는 점수 컷·클러스터링을 거친 결과라 tier0 로
    #       들어온 건이 통째로 빠질 수 있다. 실측 08-11 삼성 큐레이션
    #       '리만 가설' 건은 tier0 에 분명히 있는데 refined 에 없어서
    #       route 가 '' 를 돌려줬다.
    #   (2) tier0 항목은 키 이름이 다르다. sources 가 아니라 src/seen_in 이고,
    #       사람이 쓴 국문 요약(summary)을 따로 들고 있다. 정답지 제목은
    #       이 요약과는 겹쳐도 기계가 붙인 기사 제목(영문 원문 등)과는
    #       안 겹치는 일이 잦다.
    #   두 건 다 '수집은 tier0 로 됐는데' E열이 통로미상으로 찍혔다.
    tier0_rows = []          # (요약, tiers, 매체) - 아래 완화 매칭 전용
    t0 = load_json(os.path.join(OUTDIR, "tier0-%s.json" % day)) or {}
    for it in (t0.get("items") or []):
        srcs = list(it.get("seen_in") or [])
        if it.get("src"):
            srcs.append(it["src"])
        tiers = {TIER_OF.get(s) for s in srcs}
        tiers.discard(None)
        if not tiers:
            continue
        if it.get("url"):
            by_url.setdefault(norm_url(it["url"]), set()).update(tiers)
        titles = [it.get("title") or "", it.get("summary") or ""]
        _o = (it.get("outlet") or "").strip().lower()
        by_title.append(([t for t in titles if t], tiers, {_o} if _o else set()))
        # [2026-08-25] 종전에는 summary 가 있는 항목만 완화 매칭 풀에 넣었다.
        #   그런데 지면 스크랩(NewsClips)은 상류 포맷에 따라 요약이 통째로
        #   없는 날이 있다. 08-25 에 지면 포맷이 '제목 (매체)' 두 줄짜리로
        #   바뀌면서 15건 전부 summary 가 비었고, 마지막 안전망인 이 경로가
        #   통째로 죽어 정답지 3건이 '통로미상'으로 찍혔다.
        #   NewsClips 의 title 은 사람이 지면에서 옮겨 적은 실제 헤드라인이라
        #   요약만큼이나 좋은 대조 대상이다. 둘 중 있는 것을 모두 넣는다.
        if it.get("outlet"):
            key = it["outlet"].strip().lower()
            for txt in (it.get("summary"), it.get("title")):
                if txt:
                    tier0_rows.append((txt, tiers, key))

    # [2026-08-19] 그날 제목 뭉치로 IDF 를 만들어 _same_ev 에 태운다.
    #   가중치 없이 개수 비율만 쓰면 흔한 말이 많이 겹치는 쌍이 이긴다.
    _route_titles = [t for titles, _, _ in by_title for t in titles]
    _route_idf = build_idf(_route_titles)
    _route_widf = _word_idf(_route_titles)

    def route(t_url, t_title, alt_url=None, alt_title=None, t_outlet=None):
        """수집 통로 라벨. '' 면 정말 수집이 안 된 것.

        alt_* 는 이 정답지 기사와 '같은 사건'으로 짝지어진 내 기사다.
        내 기사는 반드시 후보 목록에서 나왔으므로, 정답지 URL·제목으로
        못 찾아도 이쪽으로는 반드시 걸린다. 이 폴백이 없으면 매체가
        달라 제목이 안 겹치는 '같은사건(수동확인)' 행이 전부
        '수집실패'로 찍힌다(실측 08-10 4행).
        """
        got, found = set(), False
        for u, ttl in ((t_url, t_title), (alt_url, alt_title)):
            if u:
                hit = by_url.get(norm_url(u))
                if hit is not None:
                    got |= hit
                    found = True
            if ttl:
                # URL 이 다르면(같은 사건 다른 매체) 제목으로 되짚는다.
                # [2026-08-11] URL 이 이미 걸렸어도 제목 대조를 건너뛰지
                #   않는다. 정답지 URL 하나는 통로 하나만 알려준다.
                #   (실측: 엔비디아 5000억달러 건은 정답지가 엔비디아
                #    보도자료 URL 이라 tier2 만 걸리는데, 같은 사건이
                #    삼성 큐레이션·RSS 로도 들어와 실제는 tier0+1+2)
                # [2026-08-11] 첫 매칭에서 break 하지 않고 전부 합친다.
                #   같은 사건이 여러 통로로 들어오는 게 정상인데, 먼저
                #   걸린 항목 하나의 tier 만 적어서 통로가 누락됐다.
                #   어느 게 먼저 걸리느냐는 풀 순서에 달린 우연이었다.
                #   (실측 08-11: 엔비디아 5000억달러 건은 tier0+tier1+tier2
                #    인데 tier2 만, 유니트리 IPO 는 tier0+tier2 인데
                #    tier1+tier2 로 찍혔다)
                # [2026-08-18] bigram(same_event) 만 쓰면 원문이 영문인 건을
                #   통째로 놓친다. 정답지는 사람이 영문 기사를 국문 제목으로
                #   옮겨 적기 때문이다. 그래서 수집은 됐는데 E열이 ''(=빨간
                #   '수집실패')로 찍혔다. scorecard 와 같은 매처·같은 문턱을
                #   쓴다(match.title_sim / BUCKET_THR).
                # [2026-08-25] **매체 일치를 1급 신호로 승격한다.**
                #   지금까지 이 단계는 '사람이 다시 쓴 정답지 제목'과
                #   '후보 제목'의 유사도만 봤다. 정답지 제목은 2차 텍스트라
                #   원문과 어휘가 갈리는 일이 잦고, 그러면 **무관한 기사가
                #   정답 기사보다 더 비슷해 보인다.** 문턱을 올리든 내리든
                #   못 고치는 구조적 결함이다.
                #   실측 08-25 'AR 스마트안경, 2030년 VR 추월 전망'(조선일보):
                #     정탐 'AI 타고 날아오른 AR 글라스, VR 시장도 추월한다'
                #          (조선일보, tier0+1+2)  sim 0.286 -> 문턱 미달 탈락
                #     오탐 '스마트 안경, AR·VR 생태계 키울 미래 기술 되나'
                #          (데일리시사닷컴, tier2) sim 0.401 -> 통과
                #   결과가 tier2 로 찍혀 **tier0 가 통째로 날아갔다.**
                #   정답지는 매체명을 정확히 들고 있는데 그 신호를 여기서
                #   버리고 있었다. 매체가 같으면 문턱을 낮춰 보고(OUTLET_THR),
                #   매체 일치 후보가 하나라도 있으면 매체가 다른 후보의
                #   tier 는 섞지 않는다.
                #   다만 매체 일치 건만 쓰면 '같은 사건이 다른 매체로 다른
                #   통로를 타고 들어온' 사실을 잃는다(08-11 주석 참조).
                #   그래서 매체가 다른 후보는 **버리지 않고 문턱을 높여**
                #   (STRONG_SIM) 함께 합친다. 위 오탐은 0.401 이라 걸러지고,
                #   진짜 같은 사건의 타사 보도는 대개 이 위로 올라온다.
                key = (t_outlet or "").strip().lower()
                same_out, other, strong = set(), set(), set()
                for titles, tiers, outs in by_title:
                    if bool(key) and key in outs:
                        if any(_same_ev(ttl, x, _route_idf, _route_widf,
                                        OUTLET_THR) for x in titles):
                            same_out |= tiers
                        continue
                    hit = strong_hit = False
                    for x in titles:
                        if _title_sim(ttl, x, _route_idf) >= STRONG_SIM:
                            strong_hit = True
                            break
                        if not hit and _same_ev(ttl, x, _route_idf,
                                                _route_widf):
                            hit = True
                    if strong_hit:
                        strong |= tiers
                    elif hit:
                        other |= tiers
                if same_out:
                    # 매체가 같은 건이 있으면 그것을 정답으로 보고,
                    # 타 매체는 고신뢰(STRONG_SIM)만 덧붙인다.
                    got |= same_out | strong
                    found = True
                elif other or strong:
                    got |= other | strong
                    found = True
            # [2026-08-11] 정답지 쪽에서 뭔가 걸렸으면 alt 는 보지 않는다.
            #   alt 는 pair() 가 붙여준 '내 기사'인데, 손보정으로 사건일치를
            #   지정하면 짝이 엉뚱하게 붙는 일이 있다(실측 08-11: 정답지
            #   'Intel 유상증자' 에 내 'OpenAI 딘 볼 영입'이 붙었다).
            #   그 tier 까지 합치면 E열이 남의 통로를 적게 된다.
            if found and got:
                break

        # [2026-08-11] 마지막 폴백: tier0 요약과의 완화 매칭.
        #   tier0 요약은 헤드라인이 아니라 사람이 쓴 3~4줄 초록이라
        #   정답지 제목과 어휘가 절반만 겹치는 일이 많다(실측 '리만 가설'
        #   0.31 로 same_event 기본 문턱 0.45 미달). 문턱만 낮추면
        #   오탐이 늘므로 **매체명이 정확히 같을 때만** 0.30 으로 본다.
        #   여기서 나오는 tiers 는 전부 tier0 이라 라벨이 틀릴 여지도 적다.
        # [2026-08-25] 문턱을 0.30 -> 0.25 로 낮추되, 대신 _same_ev 와 같은
        #   '공유 원형단어' 조건을 함께 건다. 0.30 은 정탐을 아깝게 놓쳤다
        #   (실측 08-25: 정답지 'AI 경쟁 기준, 성능에서 가성비로 이동' 과
        #    지면 스크랩 '최고 성능도 비싸면 안 써… 가성비 AI 찾는 기업들'
        #    이 0.267 로 탈락 -> '통로미상'). 문턱만 낮추면 오탐이 들어오므로
        #   (실측: '메타 뮤즈코드 공개' vs '플래티어 엑스젠' 0.250, 공유
        #    단어가 '에이전트' 하나뿐인 무관 쌍) 흔한 말 하나로 붙는 것을 막는다.
        if not found and t_title and t_outlet:
            key = t_outlet.strip().lower()
            ga = bigrams(t_title)
            for summ, tiers, outlet in tier0_rows:
                if outlet != key:
                    continue
                gb = bigrams(summ)
                if not ga or not gb or min(len(ga), len(gb)) < 8:
                    continue
                ov = len(ga & gb) / float(min(len(ga), len(gb)))
                if ov < 0.25:
                    continue
                if ov < 0.30:
                    shared = (_words(t_title) & _words(summ)) - ROUTE_COMMON
                    if not shared:
                        continue
                    if len(shared) < 2 and max(
                            _route_widf.get(x, 9.0) for x in shared) < RARE_IDF:
                        continue
                got |= tiers
                found = True
                break

        if not found:
            return ""
        return "+".join(t for t in TIER_ORDER if t in got) or "통로미상"

    return route

# 사유 -> (표시색, 사람이 읽을 설명). 고쳐야 할 곳을 문장에 그대로 적는다.
REASON_INFO = {
    "수집실패": ("FFC7CE",
              "tier0 슬랙·tier1 RSS·tier2 구글뉴스 어디에도 안 잡힘. "
              "수집 단계 문제 → tier2 쿼리나 RSS 피드를 늘려야 함"),
    "발행일 창 밖 탈락": ("F4B6C2",
                   "tier0/1/2 에는 수집돼 있으나 refine 의 발행일 창"
                   "(datewin: 기본 1일, 전날이 공휴일이면 확대) 밖이라 후보 진입 전 잘림. "
                   "피드를 늘려도 안 잡힘 → datewin 을 손봐야 함"),
    "링크미확보": ("FFD9B3",
               "클러스터는 살았으나 원문 링크를 못 찾아 후보에서 빠짐. "
               "링크 해결 문제 → refine 의 링크 해결(naver/검색)을 손봐야 함"),
    "채점 결과 후보에서 탈락": ("FFF2CC",
                     "수집·클러스터링은 됐으나 기계 점수가 낮아 후보 목록에 못 오름. "
                     "점수·컷 문제 → min-score / max-items 를 조정해야 함"),
    "LLM 선별 후보에서 탈락": ("D9D2E9",
                     "후보 목록에 다 보였는데 최종 30건 선별에서 뺌. "
                     "편집 판단 문제 → brief.py 의 선별 방침을 손봐야 함"),
    "사건일치(수동)": ("C6EFCE",
                 "같은 사건을 다른 매체 기사로 실음. 자동 매칭이 제목 차이로 "
                 "못 잡아 사람이 확인한 건 → 실질은 '일치'"),
}
HDR_FILL = PatternFill("solid", fgColor="1F3864")
HDR_FONT = Font(name=FONT, bold=True, color="FFFFFF", size=10)
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def recalc(path):
    """LibreOffice 로 한 번 열었다 저장해 수식 계산값을 파일에 박는다.

    [왜 필요한가] openpyxl 은 수식을 문자열로만 쓰고 계산값(cached value)을
      남기지 않는다. 엑셀은 열 때 스스로 계산하니 눈으로 보면 멀쩡하지만,
      미리보기·pandas·openpyxl(data_only=True) 로 읽으면 비율 칸이 전부
      빈칸으로 나온다. 실제로 저장 직후 파일을 다시 읽었더니 일치율이
      None 이었다. soffice 가 없으면 조용히 넘어간다(리포트는 그대로 쓸 수 있다).
    """
    if not shutil.which("soffice"):
        return
    # [주의] 같은 폴더로 --convert-to 하면 제자리 덮어쓰기가 되어 실패한다
    #   ("Please verify input parameters... Sfx Class:Write"). 임시 폴더에
    #   내보낸 뒤 원본 자리로 옮긴다.
    tmp = tempfile.mkdtemp(prefix="dtb-recalc-")
    try:
        subprocess.run(
            ["soffice", "--headless", "--norestore",
             "-env:UserInstallation=file:///tmp/.dtb-lo",
             "--convert-to", "xlsx:Calc MS Excel 2007 XML",
             "--outdir", tmp, path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
        made = os.path.join(tmp, os.path.basename(path))
        if os.path.exists(made) and os.path.getsize(made) > 0:
            shutil.copyfile(made, path)
    except Exception:
        pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cell(ws, r, c, v, bold=False, fill=None, wrap=False, size=10,
         align="left", fmt=None):
    x = ws.cell(row=r, column=c, value=v)
    x.font = Font(name=FONT, bold=bold, size=size)
    x.alignment = Alignment(horizontal=align, vertical="center", wrap_text=wrap)
    x.border = BORDER
    if fill:
        x.fill = PatternFill("solid", fgColor=fill)
    if fmt:
        x.number_format = fmt
    return x


def header(ws, row, cols):
    for i, (name, width) in enumerate(cols, 1):
        c = ws.cell(row=row, column=i, value=name)
        c.font = HDR_FONT
        c.fill = HDR_FILL
        c.alignment = Alignment(horizontal="center", vertical="center",
                                wrap_text=True)
        c.border = BORDER
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[row].height = 26


def pair(truth, mine, day, hit_kind_by_title, forced=None):
    """정답지 각 건에 대응되는 내 기사를 찾는다.

    [주의] '일치냐 아니냐'는 여기서 정하지 않는다. scorecard.classify 와
      손보정 결과(hit_kind_by_title)를 그대로 따른다. 판정 로직이 두 벌이면
      요약 시트의 합계와 scorecard 의 수치가 어긋난다(실제로 8 vs 11 로 갈렸다).
      이 함수는 '일치로 판정된 건에 대응되는 내 기사가 무엇인지' 만 찾는다.
      그래서 임계값을 0.30 으로 낮춘다 - 이미 일치가 확정된 뒤이므로
      느슨하게 잡아도 오탐이 생기지 않는다.

    반환: [(truth, mine_or_None, 매칭유형)]"""
    # [2026-08-12] 매칭 로직을 cowork/match.py 한 곳으로 합쳤다.
    #   종전에는 여기(표시용)와 scorecard.classify(판정용)가 서로 다른
    #   알고리즘을 써서, 클러스터로 일치가 잡힌 건의 짝을 여기서 다시
    #   못 찾으면 '일치(대응기사 미상)'이 됐다(실측 08-12: 6건 중 3건).
    #   이제 같은 결과를 쓰므로 두 값이 어긋날 수 없다.
    rf = load_json(os.path.join(OUTDIR, "refined-%s.json" % day)) or {}
    clusters = build_clusters(rf, norm_url)
    idf = build_idf([t.get("title") or "" for t in truth]
                    + [m[1] for m in mine])
    matched = match_all(truth, mine, rf, norm_url, forced)

    res = []
    for mt in matched:
        t = mt["truth"]
        ttl = t.get("title")
        if not hit_kind_by_title.get(ttl):
            res.append((t, None, ""))
            continue
        manual = hit_kind_by_title.get(ttl) == "manual"
        if mt["mine"] is not None:
            kind = mt["kind"]
            if manual and kind == "같은사건":
                kind = "같은사건(수동확인)"
            res.append((t, mt["mine"], kind))
            continue
        # 유사도로는 못 찾았지만 판정은 일치인 경우.
        # 판정이 썼던 클러스터 근거로 짝을 되찾는다. 이렇게 해야
        # '일치인데 대응기사 미상'이라는 모순된 칸이 안 생긴다.
        m2, why = resolve_by_cluster(t, mine, clusters, norm_url,
                                     same_event, idf)
        if m2 is not None:
            kind = "같은사건(수동확인)" if manual else "같은사건"
            if "추정" in why:
                kind = "같은사건(추정-확인필요)"
            res.append((t, m2, kind))
        else:
            # 여기까지 왔으면 판정 근거 자체가 모호한 것이다.
            # pairs 손보정으로 지정하거나 match.ALIAS 를 보강한다.
            res.append((t, None, "일치(대응기사 미상)"))
    return res


def build(day):
    truth = (load_json(os.path.join(BASE, "truth-%s.json" % day)) or {}).get(
        "items") or []
    if not truth:
        print("[report] 정답지 없음: %s" % day)
        return None
    mine = parse_brief(day)
    my_urls = {norm_url(m[3]) for m in mine}
    # false_hits(자동 일치 오탐 취소)는 scorecard 와 같은 값을 써야 한다.
    # 안 그러면 원장 수치와 엑셀 합계가 갈린다.
    rows, _ = classify(day, truth, my_urls, [m[1] for m in mine],
                       load_false_hits(day))
    reason_by_title = {r["title"]: r["reason"] for r in rows}

    # 손보정(사건일치 수동 등)은 원장에 있으므로 덮어쓴다
    led = {r["date"]: r for r in read_ledger()}
    rec = led.get(day, {})
    for m in rec.get("misses") or []:
        reason_by_title[m.get("title")] = m.get("reason")
    detail_by_title = {m.get("title"): m.get("detail")
                       for m in (rec.get("misses") or [])}

    # 일치 판정: classify 의 hit + 손보정으로 '사건일치(수동)'이 붙은 건.
    hit_kind = {}
    for r0 in rows:
        if r0["hit"]:
            hit_kind[r0["title"]] = "auto"
    for ti, rs in reason_by_title.items():
        if rs and rs.startswith("사건일치"):
            hit_kind[ti] = "manual"

    paired = pair(truth, mine, day, hit_kind, rec.get("pairs"))

    # 정답지 각 건이 어느 통로로 수집됐는지. 빈 문자열이면 수집 자체가 안 된 건.
    # [2026-08-10] 요약의 '수집 성공'과 정답지대조 E열이 서로 다른 계산을
    #   쓰고 있었다(요약 7건 vs E열 13건). 여기서 한 번만 구해 양쪽이 쓴다.
    #   짝지어진 내 기사(m)와 사유를 함께 넘겨야 '같은 사건 다른 매체'와
    #   '링크미확보'가 수집실패로 오분류되지 않는다.
    route = build_route(day)
    route_by_title = {}
    for t, m, _kind in paired:
        rt = route(t.get("url"), t.get("title"),
                   m[3] if m else None, m[1] if m else None,
                   t.get("outlet"))
        if not rt and reason_by_title.get(t.get("title")) in (
                "링크미확보", "LLM 선별 후보에서 탈락",
                "채점 결과 후보에서 탈락"):
            rt = "통로미상"
        route_by_title[t.get("title")] = rt
    n_collected = sum(1 for v in route_by_title.values() if v)

    wb = Workbook()

    # ---------------------------------------------------------- 요약
    ws = wb.active
    ws.title = "요약"
    ws["A1"] = "Daily Tech Brief 정답지 대조 — %s" % day
    ws["A1"].font = Font(name=FONT, bold=True, size=15)
    ws.merge_cells("A1:D1")
    ws.row_dimensions[1].height = 30
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 13
    ws.column_dimensions["C"].width = 13
    ws.column_dimensions["D"].width = 62

    # [주의] 일치 여부는 '대응 기사를 찾았는가'가 아니라 '판정이 일치인가'로
    #   센다. 대응 기사를 못 찾아도(kind='일치(대응기사 미상)') 일치는 일치다.
    #   여기를 m 유무로 세면 scorecard 합계와 어긋난다.
    n_hit = sum(1 for _, _, k in paired if k)
    n_url = sum(1 for _, _, k in paired if k == "동일기사")
    r = 3
    cell(ws, r, 1, "항목", bold=True, fill="D9E2F3")
    cell(ws, r, 2, "건수", bold=True, fill="D9E2F3", align="center")
    cell(ws, r, 3, "비율", bold=True, fill="D9E2F3", align="center")
    cell(ws, r, 4, "설명", bold=True, fill="D9E2F3")
    data = [
        ("사람 발송 기사(정답지)", len(truth), None,
         "그날 사람이 실제로 발송한 최종 브리프 건수"),
        ("내가 선별한 기사", len(mine), None,
         "파이프라인이 만든 후보에서 내가 고른 건수"),
        ("일치 — 동일 기사", n_url, "url",
         "정답지와 URL 까지 똑같은 기사를 고른 경우"),
        # 수집과 선별을 갈라 본다. 일치율이 낮을 때 원인이 '못 잡아서'인지
        # '잡았는데 안 골라서'인지가 이 한 줄로 갈린다.
        ("수집 성공 — 후보 풀에 잡힘", n_collected, "col",
         "정답지 기사가 tier0(슬랙·지면)·tier1(RSS)·tier2(구글뉴스) 중 "
         "한 곳이라도 수집된 건수. 선별과 무관한 '수집 자체의 성공률'. "
         "통로별 내역은 '정답지대조' 시트의 수집 통로 열 참조"),
        ("일치 — 같은 사건", n_hit - n_url, "evt",
         "같은 사건을 다른 매체 기사로 실은 경우"),
        ("일치 합계", n_hit, "hit",
         "'동일 기사' + '같은 사건'. 이 브리프의 실질 적중 건수"),
        ("불일치", len(truth) - n_hit, "miss",
         "사람은 실었는데 나는 못 실은 건. 사유는 아래와 '정답지대조' 시트 참조"),
    ]
    for i, (label, val, kind, desc) in enumerate(data):
        rr = r + 1 + i
        cell(ws, rr, 1, label, bold=(kind == "hit"))
        cell(ws, rr, 2, val, align="center", bold=(kind == "hit"))
        if kind:
            # 비율은 수식으로 둔다. 건수를 고치면 비율이 따라 바뀌어야 한다.
            cell(ws, rr, 3, "=IFERROR(B%d/$B$4,0)" % rr, align="center",
                 fmt="0.0%", bold=(kind == "hit"))
        else:
            cell(ws, rr, 3, "-", align="center")
        cell(ws, rr, 4, desc, wrap=True)
    # 강조행은 위치를 세지 말고 kind 로 찾는다. 행을 끼워 넣을 때마다
    # 엉뚱한 줄에 색이 칠해지는 걸 막는다(2026-08-06 '수집 성공' 행 추가).
    for i, (_, _, kind, _) in enumerate(data):
        if kind not in ("hit", "col"):
            continue
        color = "C6EFCE" if kind == "hit" else "DDEBF7"
        for c in (1, 2, 3):
            ws.cell(row=r + 1 + i, column=c).fill = PatternFill(
                "solid", fgColor=color)

    r = r + len(data) + 3
    cell(ws, r, 1, "못 맞춘 사유별 집계", bold=True, size=12)
    r += 1
    cell(ws, r, 1, "사유", bold=True, fill="D9E2F3")
    cell(ws, r, 2, "건수", bold=True, fill="D9E2F3", align="center")
    cell(ws, r, 4, "무슨 뜻이고 어디를 고쳐야 하나", bold=True, fill="D9E2F3")
    # 불일치만 센다. 여기서도 m 유무가 아니라 판정(kind)으로 갈라야
    # '사건일치(수동)'이 불일치 사유 집계에 섞여 들어가지 않는다.
    miss_titles = [t.get("title") for t, _, k in paired if not k]
    cnt = {}
    for ti in miss_titles:
        k = reason_by_title.get(ti) or "판정불가"
        cnt[k] = cnt.get(k, 0) + 1
    for k, v in sorted(cnt.items(), key=lambda x: -x[1]):
        r += 1
        color, desc = REASON_INFO.get(k, ("FFFFFF", "산출물이 없어 판정 불가"))
        cell(ws, r, 1, k, fill=color)
        cell(ws, r, 2, v, align="center")
        cell(ws, r, 4, desc, wrap=True)
        ws.row_dimensions[r].height = 30
    if rec.get("note"):
        r += 2
        cell(ws, r, 1, "비고", bold=True)
        c = cell(ws, r, 2, rec["note"], wrap=True)
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=4)
        ws.row_dimensions[r].height = 46

    # ------------------------------------------------- 정답지대조
    ws = wb.create_sheet("정답지대조")
    header(ws, 1, [("#", 5), ("카테고리", 15), ("사람이 발송한 기사", 52),
                   ("매체", 12), ("수집 통로", 14), ("결과", 10),
                   ("매칭 유형", 11), ("내가 실은 기사", 46), ("사유", 13),
                   ("사유 설명 (어디를 고쳐야 하나)", 60), ("링크", 42)])
    for i, (t, m, kind) in enumerate(paired, 1):
        rr = i + 1
        ok = bool(kind)
        cell(ws, rr, 1, i, align="center")
        cell(ws, rr, 2, t.get("category"), wrap=True)
        cell(ws, rr, 3, t.get("title"), wrap=True)
        cell(ws, rr, 4, t.get("outlet"), wrap=True)
        # 수집 통로. 수집 자체가 안 된 건은 '수집실패'로 적어 빈칸과 구분한다.
        # 위에서 요약과 같은 값으로 한 번만 구해 뒀다(계산 이원화 금지).
        rt = route_by_title.get(t.get("title")) or ""
        # [2026-08-18] 불변식: 내가 실은 기사(=일치)는 '수집실패'일 수 없다.
        #   실었다는 건 어느 통로로든 수집됐다는 뜻이다. 제목 유사도가 낮아
        #   통로를 못 되짚는 경우가 있는데(실측 08-13 '딥시크 V4 Pro' 정답지
        #   제목 vs 내 제목 0.27), 그때 빨간 '수집실패'로 찍히면 멀쩡한
        #   수집 단계를 의심하게 된다. 통로만 모르는 것이므로 그렇게 적는다.
        if not rt and ok:
            rt = "통로미상"
        cell(ws, rr, 5, rt or "수집실패", align="center",
             fill=None if rt else "FFC7CE")
        cell(ws, rr, 6, "일치" if ok else "불일치", align="center", bold=True,
             fill="C6EFCE" if ok else "FFC7CE")
        cell(ws, rr, 7, kind or "-", align="center")
        cell(ws, rr, 8, m[1] if m else "-", wrap=True)
        if ok:
            cell(ws, rr, 9, "-", align="center")
            cell(ws, rr, 10, "-")
        else:
            k = reason_by_title.get(t.get("title")) or "판정불가"
            color, desc = REASON_INFO.get(k, ("FFFFFF", "산출물이 없어 판정 불가"))
            d = detail_by_title.get(t.get("title"))
            cell(ws, rr, 9, k, align="center", fill=color)
            cell(ws, rr, 10, desc + ((" / 유형: %s" % d) if d else ""), wrap=True)
        cell(ws, rr, 11, t.get("url"), wrap=True, size=8)
        ws.row_dimensions[rr].height = 44
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = "A1:K%d" % (len(paired) + 1)

    # ---------------------------------------------------- 내선별
    #   [2026-08-27 사용자 지시] 30건 브리프와 상위 20건을 각각 다른 시트로
    #   나눈다. 30건 시트에는 '상위20' 열을 두어 무엇을 20건에서 뺐는지
    #   한눈에 보이게 한다(20건 시트에는 남긴 것만 있으므로 그 열이 없다).
    mine20 = parse_brief(day, stem="brief20")
    urls20 = {m[3] for m in mine20}

    def _mine_sheet(title, rows, with_top20):
        w = wb.create_sheet(title)
        cols = [("#", 5), ("카테고리", 16), ("헤드라인", 62), ("매체", 14),
                ("수집 통로", 14), ("정답지와 겹침", 13)]
        if with_top20:
            cols.append(("상위20", 8))
        cols.append(("링크", 46))
        header(w, 1, cols)
        for i, m in enumerate(rows, 1):
            rr = i + 1
            ok = any(mm is not None and mm[3] == m[3] for _, mm, _ in paired)
            # [2026-08-19] 내가 고른 기사의 통로. 정답지대조 시트와 같은
            #   route 를 쓰되, 내 기사는 반드시 후보 목록에서 나왔으므로
            #   URL 로 걸린다. 그래도 못 걸리면 '통로미상'이지 '수집실패'가
            #   아니다(불변식).
            rt = route(m[3], m[1]) or "통로미상"
            # 국책과제 섹션은 슬랙 #dtb-tier0 의 사업 공고 점검 결과에서 온다.
            #   기사 수집 파이프라인(tier1/tier2)을 아예 안 타므로 후보
            #   목록에 없고, 그래서 route 가 늘 빈값이다. 통로를 모르는 게
            #   아니라 슬랙 tier0 입력인 것이 확실하므로 그렇게 적는다.
            if rt == "통로미상" and (m[0] or "").strip() == "국책과제":
                rt = "tier0(슬랙 공고)"
            cell(w, rr, 1, i, align="center")
            cell(w, rr, 2, m[0], wrap=True)
            cell(w, rr, 3, m[1], wrap=True)
            cell(w, rr, 4, m[2], wrap=True)
            cell(w, rr, 5, rt, align="center",
                 fill="FFF2CC" if rt.startswith("tier0") else None)
            cell(w, rr, 6, "O" if ok else "", align="center", bold=True,
                 fill="C6EFCE" if ok else None)
            c = 7
            if with_top20:
                keep = m[3] in urls20
                # 20건에서 뺐는데 정답지였던 것(=dropped_in_20)은 붉게.
                cell(w, rr, 7, "O" if keep else "", align="center", bold=True,
                     fill=("C6EFCE" if keep else
                           ("FFC7CE" if ok else None)))
                c = 8
            cell(w, rr, c, m[3], wrap=True, size=8)
            w.row_dimensions[rr].height = 32
        w.freeze_panes = "A2"
        w.auto_filter.ref = "A1:%s%d" % ("H" if with_top20 else "G",
                                         len(rows) + 1)
        return w

    _mine_sheet("내선별(30건)", mine, True)
    _mine_sheet("내선별(20건)", mine20, False)

    # ------------------------------------------------------ 추이
    #   [2026-08-27 사용자 지시] 30건 브리프와 상위 20건을 각각 다른 시트로
    #   나눈다. 종전에는 30건만 있어서 '20건으로 줄일 때 무엇을 잃었나'가
    #   원장(mine20_n/url_hit20/event_hit20/dropped_in_20)에만 남고
    #   엑셀에서는 보이지 않았다.
    led_rows = [x for x in read_ledger() if x.get("date")]

    def _trend(title, n_key, url_key, ev_key, ev_manual_key, with_reasons):
        w = wb.create_sheet(title)
        cols = [("날짜", 13), ("정답지", 9), ("내 선별", 9), ("동일기사", 9),
                ("사건일치", 9), ("일치율", 9)]
        if with_reasons:
            cols += [("수집실패", 10), ("발행일 창\n밖 탈락", 12),
                     ("채점 결과\n후보에서 탈락", 15), ("링크미확보", 11),
                     ("LLM 선별\n후보에서 탈락", 15)]
        else:
            cols += [("30건엔 있었으나\n20건에서 뺀 정답지", 20)]
        header(w, 1, cols)
        for i, x in enumerate(led_rows, 2):
            ev = x.get(ev_manual_key)
            ev = ev if ev is not None else x.get(ev_key)
            cell(w, i, 1, x["date"], align="center")
            cell(w, i, 2, x.get("truth_n"), align="center")
            cell(w, i, 3, x.get(n_key), align="center")
            cell(w, i, 4, x.get(url_key), align="center")
            cell(w, i, 5, ev, align="center")
            # 상위20 채점이 없던 날(2026-08-25 이전)은 0% 로 찍지 않고 비운다.
            if x.get(n_key) is not None:
                cell(w, i, 6, "=IFERROR(E%d/B%d,0)" % (i, i), align="center",
                     fmt="0.0%")
            else:
                cell(w, i, 6, "-", align="center")
            if with_reasons:
                c = {}
                for mm in x.get("misses") or []:
                    k = mm.get("reason") or "?"
                    c[k] = c.get(k, 0) + 1
                for j, k in enumerate(("수집실패", "발행일 창 밖 탈락",
                                       "채점 결과 후보에서 탈락",
                                       "링크미확보",
                                       "LLM 선별 후보에서 탈락"), 7):
                    cell(w, i, j, c.get(k, 0), align="center")
            else:
                dr = x.get("dropped_in_20")
                cell(w, i, 7, len(dr) if isinstance(dr, list) else dr,
                     align="center")
        w.freeze_panes = "A2"
        return w

    _trend("추이(30건)", "mine_n", "url_hit", "event_hit",
           "event_hit_manual", True)
    _trend("추이(20건)", "mine20_n", "url_hit20", "event_hit20",
           "event_hit20_manual", False)

    out = os.path.join(OUTDIR, "검증-%s.xlsx" % day)

    # [2026-08-27] 기존 검증 파일을 조용히 덮지 않는다.
    #   backtest 는 이미 채점한 날에도 다시 돌 수 있고, 그때 report 가
    #   손보정이 반영된 판본을 자동판으로 덮어썼다. 실측 08-26: 마운트의
    #   26,961B 짜리가 22,057B 자동판으로 바뀌어 손으로 되돌려야 했다.
    #   원장(scorecard.jsonl)에 손보정이 남아 있어도, 사용자가 엑셀에서
    #   직접 손본 내용은 원장에 없으므로 복구할 방법이 없다.
    if os.path.exists(out):
        bak = os.path.join(OUTDIR, "검증-%s-이전.xlsx" % day)
        try:
            shutil.copy2(out, bak)
            print("[report] [주의] 기존 검증 파일을 백업했습니다 -> %s"
                  % os.path.basename(bak))
        except Exception as e:
            print("[report] [경고] 기존 파일 백업 실패(%s). 덮어쓰지 않습니다"
                  % type(e).__name__)
            out = os.path.join(OUTDIR, "검증-%s-재생성.xlsx" % day)
        led_day = [x for x in read_ledger() if x.get("date") == day]
        if any(x.get("event_hit_manual") is not None
               or any(m.get("manual_reason") for m in (x.get("misses") or []))
               for x in led_day):
            print("[report] [주의] 이 날짜에는 손보정 기록이 있습니다. "
                  "새 파일에 반영됐는지 대조하세요")

    try:
        wb.save(out)
    except PermissionError:
        # 사용자가 엑셀로 열어 두면 잠긴다(폴더에 ~$검증-*.xlsx 가 보인다).
        out = os.path.join(OUTDIR, "검증-%s-재생성.xlsx" % day)
        wb.save(out)
        print("[report] [주의] 원본이 잠겨 있어 다른 이름으로 저장했습니다")
    recalc(out)
    print("[report] 저장: %s" % out)
    print("[report] 일치 %d/%d (%.0f%%) | 동일기사 %d, 같은사건 %d"
          % (n_hit, len(truth), 100.0 * n_hit / len(truth), n_url,
             n_hit - n_url))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    a = ap.parse_args()
    build(a.date)


if __name__ == "__main__":
    main()
