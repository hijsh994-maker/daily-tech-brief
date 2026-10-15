# -*- coding: utf-8 -*-
"""
match.py - 정답지 N건과 내 브리프 M건을 한 번에 대조하는 단일 매처.

## 왜 만들었나 (2026-08-12 사용자 지적)
종전에는 매칭 로직이 두 벌이었다.
  - scorecard.classify() : 일치 여부 판정. 클러스터 확장 + 제목유사도 0.45
  - report.pair()        : 엑셀 '내가 실은 기사' 칸 채우기. 제목유사도 0.30
클러스터 경로로 일치를 잡으면 **어느 기사 때문에 일치인지를 기록하지 않아서**,
표시 단계에서 처음부터 다시 찾다가 실패하면 '일치(대응기사 미상)'이 됐다.
실측 08-12: 6건 중 3건이 그랬다. 총계는 맞는데 엑셀만 보면 누락처럼 보인다.

여기서는 정답지 x 내기사 전조합을 한 번만 비교하고, 그 결과 하나를
일치 판정과 표시에 함께 쓴다. 두 값이 어긋날 수 없다.

## 판정 순서 (앞선 것이 이긴다)
  1) forced   : 사람이 pairs 로 직접 지정
  2) 동일기사  : URL 정규화 후 완전 일치
  3) 같은사건(클러스터) : 정답지 URL/제목이 내 기사가 속한 refine 클러스터 안에 있음
  4) 같은사건(제목)     : 정규화 제목 유사도가 임계값 이상
  어느 것도 아니면 (None, "", 0.0)

## 제목 정규화가 핵심이다
'OpenAI, GPT-5.6-Cyber 공개하며 보안 이니셔티브 Daybreak 확대' 와
'오픈AI, 사이버보안 특화 AI 모델 GPT-5.6 사이버 출시' 는 같은 사건인데
글자 bigram 으로는 거의 안 겹친다. 국문/영문 표기와 하이픈이 갈리기 때문이다.
그래서 회사·제품명을 한 표기로 접고(ALIAS), 구두점을 없앤 뒤 토큰으로 비교한다.
"""
import re
import unicodedata

# 국문/영문 표기가 갈리는 고유명사를 한쪽으로 접는다.
# 왼쪽(표기 변형) -> 오른쪽(대표형). 긴 것부터 치환해야 부분 치환이 안 생긴다.
ALIAS = {
    "오픈ai": "openai", "오픈에이아이": "openai",
    "앤트로픽": "anthropic", "앤스로픽": "anthropic", "앤쓰로픽": "anthropic",
    "엔비디아": "nvidia", "엔디비아": "nvidia",
    "마이크로소프트": "microsoft", "ms": "microsoft",
    "구글": "google", "알파벳": "alphabet",
    "메타": "meta", "페이스북": "meta",
    "아마존": "amazon", "아마존웹서비스": "aws",
    "애플": "apple", "인텔": "intel", "오라클": "oracle",
    "삼성전자": "samsung", "삼성": "samsung",
    "테슬라": "tesla", "스페이스x": "spacex", "스페이스엑스": "spacex",
    "딥시크": "deepseek", "네이버": "naver", "카카오": "kakao",
    "퀀티넘": "quantinuum", "코어위브": "coreweave",
    "라이엇": "riot", "맥쿼리": "macquarie",
    "투게더": "together", "마누스": "manus", "매너스": "manus",
    "네모트론": "nemotron", "제미나이": "gemini", "젬니": "gemini",
    "챗gpt": "chatgpt", "클로드": "claude",
    "sk텔레콤": "skt", "에스케이텔레콤": "skt",
    "엘지유플러스": "lgu", "lg유플러스": "lgu",
    "사이버": "cyber", "데이터센터": "datacenter", "파운데이션": "foundation",
    "파라미터": "param", "매개변수": "param",
    "휴머노이드": "humanoid", "로봇": "robot",
    "달러": "usd", "억달러": "usd", "조원": "krw",
    # [2026-09-07 보강] 한글 표기만 있거나 영문 표기만 있어 서로 다른
    #   토큰으로 갈리던 회사들. 09-07 에 '엔비디아, 싱킹머신랩에 3.4조원
    #   투자 논의'가 09-04 발송 'Thinking Machines Lab, 400억 달러 가치로
    #   10억 달러 조달 추진'과 같은 라운드인데 토큰이 하나도 안 겹쳐
    #   중복 판정을 통과했다. 아래는 그때 없던 이름들이다.
    "싱킹머신랩": "thinkingmachines", "싱킹머신스랩": "thinkingmachines",
    "싱킹머신즈": "thinkingmachines", "thinking machines lab": "thinkingmachines",
    "thinking machines": "thinkingmachines", "tml": "thinkingmachines",
    "엔스케일": "nscale", "nscale": "nscale",
    "문샷": "moonshot", "문샷ai": "moonshot", "moonshot ai": "moonshot",
    "허깅페이스": "huggingface", "hugging face": "huggingface",
    "크루소": "crusoe", "플루이드스택": "fluidstack",
    "지믈렛랩스": "gimletlabs", "gimlet labs": "gimletlabs",
    "미스트랄": "mistral", "코히어": "cohere", "퍼플렉시티": "perplexity",
    "일레븐랩스": "elevenlabs", "eleven labs": "elevenlabs",
    "월드랩스": "worldlabs", "world labs": "worldlabs",
    "피규어": "figure", "figure ai": "figure",
    "유니트리": "unitree", "애지봇": "agibot", "휴메인": "humain",
    "라이드플럭스": "rideflux", "베어로보틱스": "bearrobotics",
    "업스테이지": "upstage", "모티프테크놀로지스": "motif",
    "모티프": "motif", "코난테크놀로지": "konan", "리벨리온": "rebellions",
    "퓨리오사ai": "furiosa", "퓨리오사": "furiosa",
    "세레브라스": "cerebras", "그록": "groq", "groq": "groq",
    "커서": "cursor", "cursor": "cursor",
    "미디어텍": "mediatek", "소프트뱅크": "softbank",
    "매트엑스": "matx", "코투": "coatue",
}
_ALIAS_KEYS = sorted(ALIAS, key=len, reverse=True)

# 제목에 흔해서 변별력이 없는 말. 토큰에서 뺀다.
STOP = {
    "ai", "인공지능", "출시", "공개", "발표", "개발", "관련", "위한", "통해",
    "대한", "이번", "지난", "국내", "해외", "기업", "시장", "사업", "기술",
    "모델", "서비스", "확대", "추진", "계획", "전망", "단독", "종합", "속보",
    "the", "for", "and", "with", "new", "its", "has", "will", "says",
}


def _fold(s):
    """소문자화 + 유니코드 정규화 + 별칭 접기 + 구두점 제거."""
    s = unicodedata.normalize("NFKC", s or "").lower()
    for k in _ALIAS_KEYS:
        if k in s:
            s = s.replace(k, ALIAS[k])
    # 버전 표기의 구분자를 없앤다: gpt-5.6 -> gpt56, b-300 -> b300
    s = re.sub(r"(?<=[a-z0-9])[.\-](?=[0-9])", "", s)
    # 금액은 띄어쓰기가 제각각이라 붙인다: '2.4억 달러' == '24억달러'
    s = re.sub(r"(\d)\s*(억|조|만)\s*(달러|원|엔)", r"\1\2\3", s)
    s = re.sub(r"[^0-9a-z가-힣]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def tokens(s):
    """변별력 있는 토큰 집합. 라틴/숫자 덩어리와 2글자 이상 한글."""
    f = _fold(s)
    out = set()
    for w in f.split():
        if w in STOP or len(w) < 2:
            continue
        if re.fullmatch(r"[0-9a-z]+", w):
            out.add(w)
        else:
            out.add(w)
            # 한글은 합성어가 많아 2-gram 도 같이 넣는다(붙여쓰기 차이 흡수)
            if len(w) >= 4:
                out.update(w[i:i + 2] for i in range(len(w) - 1))
    return out


def title_sim(a, b, w=None):
    """0.0 ~ 1.0. 작은 쪽 기준 겹침 비율(포함 관계를 놓치지 않는다).

    w 를 주면 토큰별 가중치를 쓴다. 단순 개수 비율로는 못 가르는 쌍이 있다.
      실측 08-11: '라이엇, 앤트로픽과 91억달러 계약' vs '네이버, 앤트로픽에
      첫 투자' 가 0.53 인데, 정탐인 'IBM·Together 2.4억달러' 쌍은 0.33 이었다.
      그날 '앤트로픽'이 온 지면에 깔려 변별력이 없었던 탓이다.
      그래서 그날 제목 뭉치에서 흔한 말은 가중치를 낮춘다(IDF).
    """
    ta, tb = tokens(a), tokens(b)
    if len(ta) < 3 or len(tb) < 3:
        return 0.0
    if not w:
        return len(ta & tb) / float(min(len(ta), len(tb)))

    def mass(s):
        return sum(w.get(x, 1.0) for x in s)
    d = min(mass(ta), mass(tb))
    return (mass(ta & tb) / d) if d else 0.0


def build_idf(titles):
    """그날 제목 뭉치로 토큰 가중치를 만든다. 흔할수록 낮다."""
    import math
    n = max(1, len(titles))
    df = {}
    for t in titles:
        for tok in tokens(t):
            df[tok] = df.get(tok, 0) + 1
    return {k: math.log(1.0 + n / float(v)) for k, v in df.items()}


def build_clusters(refined, norm_url):
    """refine 클러스터를 (URL집합, 제목목록) 목록으로."""
    out = []
    for it in (refined.get("items") or []) + (refined.get("own_track") or []):
        us = {norm_url(u) for u in (it.get("member_urls") or []) if u}
        if it.get("url"):
            us.add(norm_url(it["url"]))
        titles = [it.get("title") or ""] + list(it.get("members") or [])
        out.append((us, [t for t in titles if t]))
    return out


def match_all(truth, mine, refined, norm_url, forced=None, thr=0.28):
    """정답지 x 내기사 전조합 대조.

    truth : [{title,url,...}]
    mine  : [(cat, title, outlet, url)]  - scorecard.parse_brief 형식
    반환  : [{truth, mine, kind, score, why}]  (truth 순서 유지)
      kind = 동일기사 | 같은사건 | 같은사건(수동확인) | ""   ("" 이면 미일치)
    """
    clusters = build_clusters(refined or {}, norm_url)
    # 그날 제목 뭉치(정답지 + 내 브리프 + 클러스터 멤버)로 토큰 가중치를 만든다.
    corpus = [t.get("title") or "" for t in truth] + [m[1] for m in mine]
    for _, tls in clusters:
        corpus.extend(tls)
    idf = build_idf([c for c in corpus if c])

    out = [{"truth": t, "mine": None, "kind": "", "score": 0.0, "why": ""}
           for t in truth]
    used = set()        # 이미 짝지어진 내 기사 인덱스

    # --- 1) 사람이 지정한 짝이 최우선 ---
    for i, t in enumerate(truth):
        ttl = t.get("title") or ""
        for tfrag, mfrag in (forced or {}).items():
            if not (tfrag and tfrag in ttl):
                continue
            j = next((k for k, x in enumerate(mine) if mfrag in x[1]), None)
            if j is not None:
                out[i] = {"truth": t, "mine": mine[j],
                          "kind": "같은사건(수동확인)", "score": 1.0,
                          "why": "pairs 손보정"}
                used.add(j)
            break

    # --- 2) URL 완전 일치는 무조건 확정 ---
    for i, t in enumerate(truth):
        if out[i]["mine"] is not None:
            continue
        tu = norm_url(t.get("url"))
        if not tu:
            continue
        j = next((k for k, m in enumerate(mine)
                  if norm_url(m[3]) == tu and k not in used), None)
        if j is not None:
            out[i] = {"truth": t, "mine": mine[j], "kind": "동일기사",
                      "score": 1.0, "why": "URL 일치"}
            used.add(j)

    # --- 3) 나머지는 점수를 전부 구해 높은 것부터 1:1 로 배정 ---
    # 1:1 이 중요하다. 그리디로 하지 않으면 내 기사 하나가 정답지 여러 건에
    # 붙는다(실측 08-11: '네이버, 앤트로픽에 첫 투자'가 정답지의
    # '네이버 간접투자'와 '라이엇-앤트로픽 91억달러 계약' 양쪽에 붙어
    # 일치가 1건 부풀었다. 둘은 앤트로픽만 겹치는 다른 사건이다).
    cand = []
    for i, t in enumerate(truth):
        if out[i]["mine"] is not None:
            continue
        ttl = t.get("title") or ""
        tu = norm_url(t.get("url"))
        for j, m in enumerate(mine):
            if j in used:
                continue
            mu = norm_url(m[3])
            # 클러스터는 **정답지 URL 이 실제로 그 안에 있을 때만** 인정한다.
            # 제목이 닮은 멤버가 있다는 이유로 0.95 를 주면, refine 이 서로
            # 다른 사건을 한 덩어리로 묶었을 때 그대로 오탐이 된다
            # (실측 08-11: 앤트로픽이 걸린 '라이엇 91억달러 계약'과
            #  '네이버 간접투자'가 한 클러스터라, 내 네이버 기사가 라이엇
            #  건에 붙어 일치가 1건 부풀었다). 제목만 닮은 경우는 아래
            #  title_sim 이 알아서 점수를 매기므로 따로 가산하지 않는다.
            cl = 0.95 if (tu and any(mu in us and tu in us
                                     for us, _ in clusters)) else 0.0
            s = title_sim(ttl, m[1], idf)
            score, why = (cl, "클러스터 동일") if cl > s else (s, "제목 유사")
            if score >= thr:
                cand.append((score, i, j, why))
    cand.sort(key=lambda x: -x[0])
    done_t = set()
    owner = {}          # 내 기사 j -> 그것을 가져간 정답지 i
    for score, i, j, why in cand:
        if i in done_t or j in used:
            continue
        out[i] = {"truth": truth[i], "mine": mine[j], "kind": "같은사건",
                  "score": score, "why": why}
        done_t.add(i)
        used.add(j)
        owner[j] = i

    # [2026-08-12] 재사용 허용 2차 배정은 넣었다가 뺐다. 정답지가 한 사건을
    #   두 기사로 실은 날의 미상은 못 줄이면서, '앤트로픽만 겹치는 다른 사건'
    #   오탐(라이엇 91억달러 <-> 네이버 간접투자)을 되살렸다. 1:1 을 지킨다.
    #   짝이 안 잡힌 '일치' 행은 report 쪽 resolve_display 가 채운다.
    return out


def resolve_by_cluster(t, mine, clusters, norm_url, same_event, idf=None):
    """이미 '일치'로 판정된 건의 짝을 클러스터 근거로 되찾는다.

    판정(scorecard.classify)은 클러스터 확장으로 일치를 선언할 수 있는데,
    그때 '내 어느 기사 때문인지'를 남기지 않아 엑셀이 '대응기사 미상'이 됐다.
    여기서는 판정이 썼던 것과 **같은 근거**로 짝을 되돌려준다.
    판정과 표시가 같은 증거를 쓰므로 둘이 어긋나지 않는다.
    """
    tu = norm_url(t.get("url"))
    ttl = t.get("title") or ""
    # (1) 정답지 URL 이 들어 있는 클러스터를 찾고, 그 클러스터에 속한 내 기사
    for us, tls in clusters:
        if tu and tu in us:
            for m in mine:
                if norm_url(m[3]) in us:
                    return m, "클러스터 동일(판정 근거)"
    # (2) 클러스터 멤버 제목이 정답지와 같은 사건으로 잡힌 경우
    for us, tls in clusters:
        if not any(same_event(ttl, x) for x in tls):
            continue
        for m in mine:
            if norm_url(m[3]) in us:
                return m, "클러스터 제목(판정 근거)"
    # (3) 내 기사 제목이 정답지와 직접 같은 사건으로 잡히는 경우
    for m in mine:
        if same_event(ttl, m[1]):
            return m, "제목 동일사건(판정 근거)"
    # (4) 표시 전용 완화 매칭.
    #   여기 오는 행은 **이미 일치로 판정된** 것이라 짝이 반드시 있어야 한다.
    #   판정 임계값보다 느슨하게 잡아 가장 닮은 기사를 붙이되, 호출부에서
    #   '추정'으로 표시해 사람이 눈으로 확인할 수 있게 한다.
    #   빈칸으로 두면 '일치인데 대응기사 미상'이라는 모순만 남는다.
    if idf is not None:
        best, bs = None, 0.0
        for m in mine:
            s = title_sim(ttl, m[1], idf)
            if s > bs:
                best, bs = m, s
        if best is not None and bs >= 0.15:
            return best, "제목 근접(추정 %.2f)" % bs
    return None, ""
