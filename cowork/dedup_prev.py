# -*- coding: utf-8 -*-
"""
dedup_prev.py - 직전에 사람이 발송한 브리프에 이미 실린 기사를 오늘 후보에서 뺀다.

## 왜 필요한가
사람 브리프는 같은 사건을 이틀 연속 싣지 않는다. 그런데 구글뉴스·RSS 는
같은 사건을 다음 날에도 계속 물어오기 때문에, 후보 목록 상위가 어제 이미
나간 기사로 채워진다. 2026-08-04 실측에서 내가 손으로 걷어낸 것만 7건이었다
(아스트라, 모두의 AI 성장사다리, 제미나이 스파크, SKT AI 그림일기,
 신한투자증권 AI 에이전트부, 독파모 2차 평가, 구글 클라우드-오라클).
매일 같은 판단을 반복하는 데다, 세션이 바뀌면 어제 뭘 실었는지 알 수도 없다.

## 무엇을 기준으로 빼는가
정답지(truth-YYYY-MM-DD.json)는 사람이 실제 발송한 최종본이다.
가장 최근 정답지 **3개**를 기준으로 삼는다. 월요일이면 금·목·수 것을 본다.

[2026-08-04] 처음엔 1개만 봤다. 새 각도의 후속 보도를 잘못 뺄까 봐서다.
[2026-08-06 사용자 지시] 3개로 늘린다. 08-06 브리프에 실은 '삼성 zHBM
  성능 8배'가 08-04 에 이미 나간 기사였는데, 직전 정답지(08-05)만 보느라
  그대로 통과했다. 같은 발표가 며칠에 걸쳐 재보도되는 일이 잦다.
  잘못 뺄 위험은 임계값(THR 0.55)과 '[전일 발송분으로 제외]' 블록으로
  막는다 - 후보에서 지우는 게 아니라 프롬프트 끝에 남겨 되살릴 수 있다.

## 어떻게 같은 사건으로 보는가
  1) URL 일치 (정규화 후)
  2) refine 이 같은 클러스터로 묶은 경우 - member_urls 로 확인
  3) 제목 문자 2-gram 포함도 >= THR

[주의] 임계값은 scorecard 의 사건 매칭(0.45)보다 높은 0.55 를 쓴다.
  채점은 놓친 걸 찾는 일이라 느슨해도 되지만, 여기서 잘못 걸리면 오늘 실어야
  할 기사가 사라진다. 오탐이 미탐보다 비싸므로 보수적으로 잡는다.
  실측(08-04): 0.45 에서는 '아스트라 성능 공개'(어제)와 '아스트라, 美 정부
  사전 위험 검증'(오늘, 새 각도)이 붙어버렸다. 0.55 에서는 갈린다.

## 어떻게 쓰는가
brief.py / prompt_extend.py 가 import 해서 쓴다. 완전히 지우지 않고
'[전일 발송분으로 제외]' 목록을 프롬프트 끝에 남긴다. 후속 보도를 잘못
뺐을 때 되살릴 길이 있어야 하기 때문이다.
"""
import datetime
import json
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(BASE, "out")
THR = 0.55

_STOP = re.compile(r"[^0-9a-zA-Z가-힣]+")


def norm_url(u):
    u = (u or "").strip().split("#")[0]
    u = re.sub(r"^https?://", "", u, flags=re.I)
    u = re.sub(r"^www\.", "", u, flags=re.I)
    u = re.sub(r"[?&]utm_[^&]*", "", u)
    return u.rstrip("/&?").lower()


# scorecard 와 같은 이유로 짧은 잔여 문자열은 버린다.
# (_STOP 이 일본어·중국어를 지워 숫자만 남으면 아무 제목에나 붙는다)
MIN_GRAMS = 8

# 멤버 '제목'까지 훑을 클러스터 크기 상한. 이보다 크면 오결합이 섞여 있다고
# 보고 대표 제목과 URL 만 본다. hit_item() 주석 참조.
#   [주의] members 리스트 길이가 아니라 it['size'] 를 봐야 한다. refine 이
#   members 를 12개까지만 저장하기 때문에 리스트 길이로는 47짜리 오결합
#   클러스터와 12짜리 정상 클러스터가 구분되지 않는다(2026-08-06 실측).
MAX_CLUSTER_SIZE = 20


# ---------------------------------------------------------------------------
# 조직명 보조 신호 (2026-09-03 신설)
#
# ## 왜 넣었나
# 09-03 에 '맞춤 AI칩도 자사 생태계로…엔비디아, 미디어텍에 4.8조원 베팅'을
# 실었는데, 09-01 정답지의 '엔비디아, 미디어텍에 베팅…업계 표준 노린다'와
# 같은 사건이었다. 창(N_PREV=5) 안에 들어 있었고 비교도 됐는데
# **제목 2-gram 유사도가 0.529 로 THR 0.55 에 0.021 미달**해 통과했다.
#
# ## 임계값을 내리는 것은 답이 아니다 (실측 2026-09-03)
# 최근 26일치 정답지 329건으로 재보니, THR 을 0.45 로 내리면 사람이 실제로
# 실은 기사 **22건이 새로 잘려나간다**(하루 0.85건). 0.45~0.55 구간은
# 영문 금융 기사의 공통 어휘($ billion, valuation, raise)나 같은 주제어
# (휴머노이드·로봇·코딩·에이전트) 때문에 붙은 것이 대부분이라, 제목
# 유사도만으로는 진짜 중복과 구분되지 않는다.
#
# ## 대신 조직명을 본다
# 같은 22건에 '조직명 2개 이상 공유'를 걸면 **오탐 0건**이고 미디어텍
# 건만 잡힌다(공유: 엔비디아 + 미디어텍). 오탐 후보들이 공유하는 것은
# 조직명이 아니라 일반 주제어라 이 조건에 걸리지 않기 때문이다.
#
# ## 하드 컷이 아니다
# SOFT_THR 구간의 매칭은 '[중복의심]' 표시만 붙인다. _is_hard() 는
# 여전히 'URL 동일'만 하드로 보므로 후보에서 지워지지 않는다.
# 판정하는 사람이 blind 목록에서 눈으로 대조하라는 뜻이다.
SOFT_THR = 0.45
MIN_SHARED_ORGS = 2

_BUILTIN_ORGS = """
엔비디아 미디어텍 오픈AI 앤트로픽 앤스로픽 구글 딥마인드 메타 마이크로소프트 애플 아마존 AWS 인텔
삼성전자 삼성SDS SK하이닉스 SK텔레콤 SKT KT LG유플러스 LG전자 LG AI연구원 LG CNS 네이버 네이버클라우드
카카오 TSMC 브로드컴 퀄컴 AMD 테슬라 오라클 허깅페이스 팔란티어 세일즈포스 슬랙 어도비 IBM
알리바바 바이트댄스 텐센트 딥시크 화웨이 샤오미 BYD 소니 키옥시아 마이크론
현대차 현대위아 현대모비스 기아 한화 롯데 CJ대한통운 HD현대 포스코 두산 효성 LS일렉트릭 한전 한국전력
에퀴닉스 코어위브 람다 크라우드스트라이크 팔로알토 아카마이 파수 지니언스
Nvidia NVIDIA MediaTek OpenAI Anthropic Google DeepMind Meta Microsoft Apple Amazon Intel Samsung
Oracle Tesla Broadcom Qualcomm Salesforce Palantir Adobe Alibaba ByteDance Tencent DeepSeek Huawei
Xiaomi Sony Kioxia Micron Equinix CoreWeave Lambda CrowdStrike Akamai Naver Kakao TSMC
"""


def _load_orgs():
    """조직명 사전. 내장 명단 + config/entities.yaml 의 회사명."""
    orgs = {w for w in _BUILTIN_ORGS.split() if len(w) >= 2}
    try:
        import yaml
        p = os.path.join(BASE, "config", "entities.yaml")
        cfg = yaml.safe_load(open(p, encoding="utf-8")) or {}

        def collect(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    if k in ("full", "status", "note", "built", "method"):
                        continue
                    collect(v)
            elif isinstance(node, list):
                for v in node:
                    if isinstance(v, str) and len(v) >= 2:
                        orgs.add(v.strip())
                    else:
                        collect(v)
        collect(cfg.get("govt_projects"))
        collect(cfg.get("startups"))
        collect(cfg.get("skt_portfolio"))
    except Exception:
        # entities.yaml 이 없거나 pyyaml 이 없어도 내장 명단으로 동작한다.
        pass
    # 'AI' 처럼 짧고 아무 데나 붙는 토큰은 조직 신호가 아니다.
    return {o for o in orgs if len(o) >= 2 and o.upper() != "AI"}


_ORGS = None


def _canon(s):
    """[2026-09-07 신설] 한글·영문 회사명을 한 표기로 접는다.

    왜 - '싱킹머신랩'(한글)과 'Thinking Machines Lab'(영문)은 토큰이 하나도
    안 겹쳐 조직명 공유 신호조차 뜨지 않았다. 사전은 match.ALIAS 하나만
    쓴다(정본 한 곳 유지). 하드 컷(_bigrams)은 건드리지 않는다 - 임계값을
    흔들면 정답지가 잘린다는 것이 08-27 회귀검사로 확인돼 있다.
    이 정규화는 **소프트 표시(조직명 공유)에만** 적용한다.
    """
    t = (s or "").lower()
    try:
        from match import ALIAS, _ALIAS_KEYS
    except Exception:
        try:
            from cowork.match import ALIAS, _ALIAS_KEYS
        except Exception:
            return t
    for k in _ALIAS_KEYS:
        if k in t:
            t = t.replace(k, ALIAS[k])
    return t


def _orgs(title):
    """제목에 등장하는 조직명 집합."""
    global _ORGS
    if _ORGS is None:
        _ORGS = _load_orgs()
    t = title or ""
    tc = _canon(t)
    found = {o for o in _ORGS if o in t}
    # 한↔영 표기가 갈린 회사는 접은 표기로 한 번 더 본다.
    found |= {o for o in _ORGS if _canon(o) and _canon(o) in tc}
    return found


# [2026-09-07] 자금조달 어휘. 같은 라운드 재보도를 잡는 데 쓴다.
_FUNDING = re.compile(
    r"(투자|조달|유치|증자|기업가치|밸류에이션|라운드|시리즈\s?[A-E]|"
    r"프리\s?IPO|IPO|상장|인수|합병|매각|"
    r"(?i:funding|raise[sd]?|invest(?:s|ed|ment)?|valuation|round|"
    r"series\s?[A-E]|pre-?IPO|acqui(?:re[sd]?|sition)|stake))")


def _is_funding(t):
    return bool(_FUNDING.search(t or ""))


# 자금조달 라운드 규칙에서 '공유 조직'으로 세지 않는 이름들. 이들은 투자자
# 쪽으로 아무 딜에나 붙어서, 공유돼도 같은 사건이라는 뜻이 되지 않는다.
_MEGACAP = {
    "엔비디아", "Nvidia", "NVIDIA", "구글", "Google", "마이크로소프트",
    "Microsoft", "애플", "Apple", "아마존", "Amazon", "AWS", "메타", "Meta",
    "삼성전자", "Samsung", "TSMC", "인텔", "Intel", "오라클", "Oracle",
    "소프트뱅크", "브로드컴", "Broadcom", "AMD",
}


def _specific(shared):
    """투자자 대기업을 뺀 '그 딜의 주인공' 조직이 남는가."""
    return bool({o for o in shared if o not in _MEGACAP})


def _bigrams(s):
    s = _STOP.sub("", (s or "").lower())
    if len(s) < 2:
        return set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


# 몇 개의 정답지를 거슬러 볼 것인가. 달력 날짜가 아니라 '발송된 브리프 개수'라
# 주말·공휴일이 껴도 영업일 기준으로 동작한다.
#
# [2026-08-27 사용자 지시] 3 -> 5 (= 영업일 1주일).
#   "동일한 내용의 기사가 3일 후에 또 나오는 경우가 있다."
#   3회분이면 창이 사실상 직전 3영업일이라, 같은 발표가 나흘 뒤 다른 매체·
#   다른 앵글로 재보도될 때 [최근 발송분 제목] 목록에서 빠져 걸러지지 않았다.
#   창을 1주일로 늘려 재탕 후보를 눈으로 대조할 수 있게 한다.
N_PREV = 5


# lookback 은 위 n 개를 채우기 위해 거슬러 볼 '달력' 일수다.
#   N_PREV=5 면 정상 주간에도 7일이 필요하고, 연휴가 끼면 12일을 넘길 수 있어
#   종전 14 로는 마지막 회차가 잘릴 수 있다. 21 로 넉넉히 잡는다.
def prev_truths(day, n=N_PREV, lookback=21):
    """day 직전의 정답지를 최신순으로 최대 n 개 찾는다.

    반환: [(날짜, items), ...] (없으면 빈 리스트)
    """
    d0 = datetime.date.fromisoformat(day)
    out = []
    for back in range(1, lookback + 1):
        if len(out) >= n:
            break
        d = (d0 - datetime.timedelta(days=back)).isoformat()
        p = os.path.join(BASE, "truth-%s.json" % d)
        if not os.path.exists(p):
            continue
        try:
            items = json.load(open(p, encoding="utf-8")).get("items") or []
        except Exception:
            items = []
        out.append((d, items))
    return out


def prev_truth(day, lookback=10):
    """직전 정답지 1개. (날짜, items). 예전 호출부 호환용으로 남겨둔다."""
    got = prev_truths(day, 1, lookback)
    return got[0] if got else (None, [])


class PrevFilter(object):
    """직전 발송분과 같은 사건인지 판정한다.

    쓰는 쪽에서 hit(title, url) 한 번만 부르면 된다.
    정답지가 없으면 아무것도 걸러내지 않는다(active=False).
    """

    def __init__(self, day, thr=THR, n=N_PREV):
        self.thr = thr
        got = prev_truths(day, n)
        self.days = [d for d, _ in got]
        # self.day = 가장 최근 정답지 날짜. 로그·프롬프트 표기에 쓴다.
        self.day = self.days[0] if self.days else None
        items = [it for _, its in got for it in its]
        self.active = bool(items)
        self.n_prev = len(items)
        self.urls = {norm_url(t.get("url")) for t in items if t.get("url")}
        # 제목 -> 그 제목이 실린 날짜. 어느 날 나간 건지 프롬프트에 적는다.
        self.grams = []
        self.title_day = {}
        for d, its in got:
            for t in its:
                ttl = t.get("title") or ""
                g = _bigrams(ttl)
                if g:
                    self.grams.append((ttl, g))
                    self.title_day.setdefault(ttl, d)

        # 각 정답지 날짜의 산출물에서 그 기사가 어느 클러스터였는지 펼친다.
        # 오늘 후보가 그 클러스터의 다른 멤버 URL 로 들어오면 URL 도 제목도
        # 안 맞을 수 있는데, 이러면 잡힌다.
        for d in self.days:
            ref = os.path.join(OUTDIR, "refined-%s.json" % d)
            try:
                rf = json.load(open(ref, encoding="utf-8"))
            except Exception:
                continue
            for it in (rf.get("items") or []) + (rf.get("own_track") or []):
                us = {norm_url(u) for u in (it.get("member_urls") or []) if u}
                if it.get("url"):
                    us.add(norm_url(it["url"]))
                if us & self.urls:
                    self.urls |= us

    def hit(self, title, url=None):
        """같은 사건이면 매칭된 어제 헤드라인을, 아니면 None 을 돌려준다."""
        if not self.active:
            return None
        if url and norm_url(url) in self.urls:
            return "URL 동일"
        g = _bigrams(title)
        if len(g) < MIN_GRAMS:
            return None
        best, bt = 0.0, None
        for t, pg in self.grams:
            if len(pg) < MIN_GRAMS:
                continue
            r = len(g & pg) / float(min(len(g), len(pg)))
            if r > best:
                best, bt = r, t
        soft = False
        if best < self.thr:
            # [2026-09-03] 임계값 바로 아래라도 조직명을 2개 이상 공유하면
            # 같은 사건일 공산이 크다. 위 SOFT_THR 주석 참조.
            shared = _orgs(title) & _orgs(bt)
            if best >= SOFT_THR and len(shared) >= MIN_SHARED_ORGS:
                soft = True
            # [2026-09-07 신설] 자금조달 라운드 예외.
            #   같은 회사의 **같은 라운드**는 투자자·금액 표현이 달라져도
            #   같은 사건이다. 이때는 제목이 거의 안 겹치므로(한↔영 표기,
            #   투자자 이름이 주어로 올라옴) 유사도·조직명 2개 조건을
            #   둘 다 통과하지 못한다. 회사 1개 공유 + 양쪽 다 자금조달
            #   어휘면 표시한다.
            #   (실측 09-07: '엔비디아, 싱킹머신랩에 3.4조원 투자 논의'가
            #    09-04 발송 'Thinking Machines Lab, 400억 달러 가치로 10억
            #    달러 조달 추진'과 같은 라운드인데 그대로 통과해 발송됐다.)
            elif (_specific(shared) and _is_funding(title)
                  and _is_funding(bt)):
                soft = True
            else:
                return None
        # 며칠 치를 보므로 어느 날 나간 건지 같이 돌려준다.
        d = self.title_day.get(bt)
        tag = "[%s] %s" % (d, bt) if d else bt
        return ("조직명 공유 " + tag) if soft else tag

    def hit_item(self, it):
        """클러스터 한 덩어리를 통째로 본다.

        [주의] 대표 제목만 보면 안 된다. 같은 사건이라도 매체마다 제목이
        달라서, 대표로 뽑힌 게 하필 은유적인 제목이면 2-gram 이 못 잡는다.
        실측(08-04): '천재수학자 몸값이 290만원?' 은 어제 실린 아스트라 건과
        같은 사건인데 대표 제목만으로는 0.55 를 못 넘었다. 그런데 refine 이
        이미 같은 클러스터로 묶어놨으므로, 멤버 중 하나만 걸리면 다 뺀다.

        [2026-08-06] 단, 멤버 '제목' 매칭은 클러스터가 작을 때만 한다.
          refine 의 클러스터링은 덩치가 커질수록 서로 다른 사건을 함께
          삼킨다. 실측: size 47 짜리 클러스터(대표 'Microsoft AI exec tells
          developers to default to OpenAI's top model')의 멤버였던
          'Blackstone seeks to raise another $36 billion...' 이 08-04 정답지의
          'Horizon3 hits $2 billion valuation with $250M...' 와 0.55 를 넘겨
          붙었다. 둘은 완전히 다른 사건인데 영문 금융 기사의 공통 어휘
          ($ billion, valuation, raise)만으로 매칭된 것이다. 그 바람에
          사람이 08-06 에 실제로 실은 MS 건이 후보에서 통째로 빠졌다.
          멤버 URL 매칭은 정확하므로 크기와 무관하게 그대로 둔다.
        """
        m = self.hit(it.get("title"), it.get("url"))
        if m:
            return m
        for u in (it.get("member_urls") or []):
            if norm_url(u) in self.urls:
                return "URL 동일(클러스터 멤버)"
        members = it.get("members") or []
        if (it.get("size") or len(members)) > MAX_CLUSTER_SIZE:
            return None
        for t in members:
            m = self.hit(t)
            if m:
                return m
        return None

    @staticmethod
    def _is_hard(m):
        """URL 이 실제로 같은 매칭인가(= 같은 기사 그대로 재등장).

        제목 2-gram 유사도 매칭은 여기 해당하지 않는다.

        [2026-09-01] '클러스터 멤버' 매칭도 하드 컷에서 뺐다.
          종전에는 startswith("URL 동일") 이라 `URL 동일(클러스터 멤버)` 까지
          통째로 잘랐다. 그런데 그건 **그 기사 자신의 URL 이 아니라 같은
          클러스터에 묶인 다른 기사의 URL** 이 어제 발송분과 겹친다는 뜻이다.
          refine 의 클러스터링은 같은 사건의 다른 앵글을 자주 한 덩어리로
          삼키므로, 개별 보도가 이미 나간 뒤에 나오는 **종합 앵글 기사**가
          여기서 조용히 사라진다.
          실측 09-01: '금융 AX 힘주는 KT…5대銀 중 4곳 구축'(한국경제, 지면)이
          08-31 발송분 'KT, 우리은행 AI 챗봇·상담봇 재구축 착수' 와 같은
          클러스터라는 이유로 잘렸다. 우리은행 한 곳 vs 5대 은행 중 4곳으로
          범위가 다른 별개 사건이었고, 사람은 09-01 정답지에 실었다.
          이제 이런 건은 `dup_suspect` 로 후보에 남아 LLM 이 판정한다
          (메모 dtb-dedup-hybrid 의 'URL 동일만 기계 컷' 원칙에도 맞다).
        """
        return m == "URL 동일"

    def split(self, items):
        """(남길 것, [(뺀 것, 어제헤드라인), ...]) 로 가른다.

        [2026-08-27 사용자 지시 - 하이브리드로 변경]
          종전에는 제목 2-gram 유사도(THR=0.55)로 걸린 것까지 후보 풀에서
          통째로 뺐다. 그런데 2-gram 은 의미를 모른다. 양방향으로 다 틀린다:
            - 못 잡음(실측 08-04): '3D 메모리로 삼성전자 AI 병목 뚫는다' 와
              '삼성 HBM5 성능 8배 zHBM' 은 같은 발표인데 0.55 를 못 넘었다.
            - 잘못 잡음(실측 08-07): '카카오, AI 인프라 대신 에이전틱 AI로
              승부' 가 '메타, 뮤즈코드 공개' 와 묶여 통째로 빠졌다.
          게다가 잘린 항목은 후보 목록(idx 붙은 JSONL)에서 사라지므로,
          규칙 17 이 '되살려 실으라'고 해도 8단계 검증의 '후보목록 밖 링크'에
          걸리는 모순이 있었다. N_PREV 를 5 로 넓히면서 오탐 기회도 늘었다.

          그래서 **기계는 URL 이 같은 것만 자르고, 제목 유사도로 걸린 것은
          후보에 남기되 `dup_suspect` 플래그만 단다.** 판정은 LLM 이 한다.
        """
        keep, drop = [], []
        for it in items:
            m = self.hit_item(it)
            if not m:
                it.pop("dup_suspect", None)
                keep.append(it)
            elif self._is_hard(m):
                drop.append((it, m))
            else:
                # 후보 목록에 남긴다. 프롬프트에 이 값이 그대로 실린다.
                it["dup_suspect"] = m
                keep.append(it)
        return keep, drop


def render_recent_sent(day, n=N_PREV):
    """프롬프트에 붙일 '[최근 발송분 제목]' 블록.

    기계 dedup 은 제목 2-gram 유사도로 판정하므로, 같은 사건이라도 제목이
    완전히 다르면 못 잡는다. 실측 2026-08-06: 08-04 에 나간
    '3D 메모리로… 삼성전자, AI 병목 뚫는다' 와 오늘 후보
    '삼성이 돌아왔다…HBM5 성능 8배 zHBM' 은 같은 발표인데 0.55 를 못 넘었다.
    그래서 최근 발송 제목을 통째로 보여주고 LLM 이 눈으로 걸러내게 한다.
    """
    got = prev_truths(day, n)
    if not got:
        return []
    L = ["", "[최근 발송분 제목] - 사람이 최근 %d회분 브리프에 실제로 실은" % len(got),
         "헤드라인 전체입니다. **아래와 같은 사건은 오늘 다시 싣지 마세요.**",
         "기계 필터는 제목이 비슷할 때만 걸러내므로, 같은 발표를 다른 각도·다른",
         "표현으로 쓴 기사는 여기에 걸러지지 않고 후보에 남아 있습니다.",
         "직접 대조해서 빼세요. (예: 08-04 '3D 메모리로 삼성전자 AI 병목 뚫는다'",
         "와 오늘 '삼성 HBM5 성능 8배 zHBM 공개' 는 같은 발표입니다)",
         "새로운 사실이 더해진 후속 보도만 예외입니다."]
    for d, items in got:
        L.append("  - %s" % d)
        for t in items:
            L.append("      %s" % (t.get("title") or ""))
    return L


def render_dropped(dropped, prev_day):
    """프롬프트 끝에 붙일 '[최근 발송분으로 제외]' 블록."""
    if not dropped:
        return []
    L = ["", "[최근 발송분으로 제외] - 최근 %d회분(%s 등) 브리프에 사람이 이미"
         % (N_PREV, prev_day or "직전"),
         "발송한 기사와 같은 사건이라 위 후보에서 뺐습니다. 사람 브리프는 같은",
         "사건을 며칠에 걸쳐 반복해 싣지 않습니다.",
         "**다만 오늘 새로운 사실이 더해진 후속 보도라면**",
         "(예: 이틀 전 '출시 예고' -> 오늘 '규제 심사 착수') 되살려 실으세요.",
         "그 경우 그때와 무엇이 달라졌는지가 '주요 내용'에 드러나야 합니다.",
         "각 항목의 '기존' 값 앞에 붙은 [날짜]가 그 기사가 나간 날입니다.",
         "",
         "**[2026-09-01] 되살릴 때 8단계 검증을 겁내지 마세요.**",
         "여기 적힌 항목은 후보 목록(idx JSONL)에서 빠져 있으므로, 되살려 실으면",
         "8단계의 '후보목록 밖 링크'에 잡힙니다. **그래도 빼지 마세요.**",
         "그 검사는 링크를 지어내지 않았는지 보는 장치이고, 위 url 값은 기계가",
         "이미 검증해 둔 값입니다. 허용 예외에 **'[최근 발송분으로 제외]에서",
         "되살린 항목'을 추가**했으니, 지우지 말고 보고에 사유 한 줄만 적으세요.",
         "(실측 09-01: '금융 AX 힘주는 KT…5대銀 중 4곳 구축'을 '되살릴 만하지만",
         " 후보목록 밖 링크가 된다'는 이유로 뺐는데 그날 정답지였습니다.",
         " 08-31 에 나간 건은 우리은행 한 곳, 이건 5대 은행 중 4곳으로 범위가",
         " 다른 별개 사건이었습니다.)"]
    for it, matched in dropped:
        L.append('{"headline": %s, "outlet": %s, "url": %s, "기존": %s}' % (
            json.dumps(it.get("title") or "", ensure_ascii=False),
            json.dumps(it.get("outlet") or "", ensure_ascii=False),
            json.dumps(it.get("url") or "", ensure_ascii=False),
            json.dumps(matched, ensure_ascii=False)))
    return L
