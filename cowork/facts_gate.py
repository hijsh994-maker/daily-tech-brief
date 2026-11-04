#!/usr/bin/env python3
"""[2026-11-04 신설] 사실 먼저, 등급은 나중 — facts 게이트 (`run.sh facts`)

[왜] 11-04 사용자 지적. '고' 4건 중 3건(메타 회사채 완료·구글 학교 정책·챗GPT 옛 보안 우려)을
  사용자가 뺐고, '중'으로 내린 독파모 블라인드·국정원 긴급점검·애플 로봇 지연은 '고'감이었다.
  원인은 기준 부족이 아니라 **방식**이었다.
    1) 제목으로 등급을 먼저 정하고 본문은 '고'만 나중에 읽는다 → '중'으로 내린 건은 아무도 다시 안 본다.
    2) 880줄 기준·85개 사례를 낱말 방아쇠로 쓴다('검토'·'착수'·'지연' → C축 하향, '청소년' → E-c 상향).
    3) 게이트가 '해명 문구가 있는가'만 봐서 '인사이트:'·'중복검토:' 한 줄로 통과된다.
    4) '언제 일어난 일인가'와 '이미 실은 흐름의 몇 번째인가'를 강제하는 곳이 없다.
  사용자: *"사례집으로 추가한들 동일한 실수를 반복할 가능성이 높다 … 근본적인 해결 방안"*

[무엇을] 판정 대상 중 **범위 집합**(아래)은 본문에서 닫힌 칸을 먼저 채운다(out/facts-<날짜>.csv).
  이 게이트는 자유 서술을 믿지 않는다. 칸 값과 본문 인용, 그리고 **코드가 스스로 찾은 사실**
  (본문 속 날짜·재조명 표현, 과거 발송본과의 주체+사건종류 겹침)으로만 등급과 맞는지 본다.

  범위 집합(코드가 정한다 — 내가 고르지 않는다):
    - '고' 전부
    - '중'·'저' 중 최우선 어젠다(독파모·모두의 AI·소버린·AIDC·국가 AI 컴퓨팅) / 경쟁사 / 빅테크+(로봇·모델·인수·투자)
      / 정부+(보안·해킹) 에 걸리는 건, 또는 본문 리드에 '실행된 일' 동사(착수·체결·출시했·연기·중단…)가 있는 건.
      단 사유가 '중복'·'시황실적'·'AI무관'·'배제'·'운영공지'·'사설칼럼'으로 시작하는 '저'는 뺀다.

  facts CSV 칸 (헤더 포함):
    idx,사건일,근거,유형,단계,흐름,변화,비고
      사건일 : YYYY-MM-DD — 본문 기준 '그 일이 일어난 날'(기사 날짜가 아님)
      근거   : 본문 인용(그대로 복사). 본문이 없으면 'find:<질의> 0건' 또는 '본문없음'
      유형   : 출시 | 투자·조달 | 인수·합병 | 계약·제휴 | 정부조치실행 | 정책논의 | 일정변경 | 사고·공격 | 인사 | 분석·재조명 | 반응·논평 | 실적
      단계   : 실행 | 논의
      흐름   : 신규 | MM-DD(이미 실은 같은 흐름의 발송일)
      변화   : (흐름이 날짜면) 새결정 | 새주체 | 규모변화 | 신제품 | 완료 | 반응·논평 | 재확인
      비고   : 실행된 사건을 '중'·'저'로 둘 때만, 닫힌 코드 하나:
               체급미달 | F1미달 | 배제<번호> | 지엽 | 해외규제 | F6학계 | F2중국 | F7분석 | SKT주어 | 중복흐름

  규칙(종료코드 1):
    R1 범위 집합인데 facts 행이 없다
    R2 근거 인용이 본문에 없다(본문이 있을 때)
    R3 '고'인데 사건일이 창(직전 영업일~오늘) 밖이다            ← 챗GPT 옛 보안 우려
    R4 '고'인데 유형이 분석·재조명 / 반응·논평 / 실적이다
    R5 '고'인데 코드가 과거 발송본에서 같은 주체+같은 사건종류를 찾았고 흐름=신규다   ← 메타 회사채
    R3b '고'인데 본문 리드의 날짜 표현이 모두 창 밖이다(코드가 직접 찾음)           ← 챗GPT 옛 보안 우려
    R6 '고'인데 흐름이 날짜이고 변화가 완료 / 반응·논평 / 재확인이다
    R7 최우선 어젠다·경쟁사·빅테크·정부보안 건이 단계=실행 + 유형(출시·투자·인수·계약·정부조치실행·일정변경·사고)인데
       '중'·'저'이고 비고 코드가 없다                          ← 국정원 긴급점검·애플 로봇 지연
  경고(종료코드 0):
    W1 본문 리드의 날짜 표현이 창 밖인데 사건일은 창 안으로 적었다(코드가 찍은 날짜를 눈으로 확인)
    W2 본문에 재조명 표현('전부터'·'당시'·'과거'·'지난해')이 있는데 유형이 재조명이 아니다
    W3 '고'인데 단계=논의(투자·조달 제외) — 독립 검토 대상
    W4 본문 리드에 실행 동사(착수·돌입·미뤄졌…)가 있는데 단계=논의로 적었다

  bash cowork/run.sh facts                 # 오늘 판정 점검
  bash cowork/run.sh facts --scope         # 범위 집합만 찍기(facts 를 채우기 전에)
  bash cowork/run.sh facts --template      # 범위 집합의 빈 facts 행 + 코드가 찾은 단서를 out/facts-<날짜>.todo.csv 로
"""
import argparse, csv, datetime, glob, json, os, re, sys

TYPES = "출시 투자·조달 인수·합병 계약·제휴 정부조치실행 정책논의 일정변경 사고·공격 인사 분석·재조명 반응·논평 실적".split()
EXEC_TYPES = set("출시 투자·조달 인수·합병 계약·제휴 정부조치실행 일정변경 사고·공격".split())
STALE_TYPES = set("분석·재조명 반응·논평 실적".split())
DELTAS = "새결정 새주체 규모변화 신제품 완료 반응·논평 재확인".split()
WEAK_DELTAS = set("완료 반응·논평 재확인".split())
NOTE_CODES = re.compile(r"^(체급미달|F1미달|배제\d+|지엽|해외규제|F6학계|F2중국|F7분석|SKT주어|중복흐름)")
SKIP_LOW = re.compile(r"^(중복|시황실적|AI무관|배제|운영공지|사설칼럼)")

PRIORITY = re.compile(r"독파모|독자\s?AI|파운데이션 모델 사업|모두의\s?AI|소버린|AIDC|AI\s?데이터센터|국가\s?AI\s?컴퓨팅|국가AI컴퓨팅")
COMPETITOR = re.compile(r"(?<![A-Za-z가-힣])KT(?![A-Za-z&X])|케이티|네이버|Naver|카카오|LG\s?유플러스|LGU\+|유플러스|LG\s?CNS|LG\s?AI연구원|엑사원")
BIGTECH = re.compile(r"오픈AI|OpenAI|앤트로픽|앤스로픽|Anthropic|구글|Google|알파벳|딥마인드|메타|Meta|마이크로소프트|Microsoft|\bMS\b|엔비디아|Nvidia|NVIDIA|애플|Apple|아마존|Amazon|AWS|xAI|테슬라|Tesla", re.I)
BIGTECH_TOPIC = re.compile(r"로봇|휴머노이드|robot|humanoid|모델|model|인수|acqui|투자|funding|raise|출시|launch|공개|unveil|데이터센터|data center", re.I)
GOV = re.compile(r"국정원|정부|과기정통부|과학기술정보통신부|행안부|국방부|금융위|방통위|백악관|상무부")
SEC = re.compile(r"보안|해킹|사이버|취약점|security|hack", re.I)
EXEC_VERB = re.compile(r"착수|돌입|체결|출시했|출시한다고|공개했|도입했|가동|개소|인수했|인수한다|확정|선정됐|미뤄졌|미뤘|연기됐|연기했|중단했|중단됐|차단했|금지")
RETRO = re.compile(r"출시 전부터|전부터 |당시 |과거 |지난해|몇 년 전|수년 전|재조명|돌이켜")

# 주체(정규형: 표기들) — 과거 발송본과 '같은 흐름'을 찾을 때만 쓴다.
ENT = {
    "메타": r"메타(?!버스)|Meta\b", "오픈AI": r"오픈AI|OpenAI", "앤트로픽": r"앤트로픽|앤스로픽|Anthropic",
    "구글": r"구글|Google|알파벳|Alphabet|딥마인드|DeepMind", "MS": r"마이크로소프트|Microsoft|\bMS\b",
    "엔비디아": r"엔비디아|Nvidia|NVIDIA", "애플": r"애플|Apple\b", "아마존": r"아마존|Amazon|AWS",
    "오라클": r"오라클|Oracle", "xAI": r"xAI|그록|Grok", "테슬라": r"테슬라|Tesla", "코어위브": r"코어위브|CoreWeave",
    "크라우드스트라이크": r"크라우드스트라이크|CrowdStrike", "KT": r"(?<![A-Za-z가-힣])KT(?![A-Za-z&X])|케이티",
    "네이버": r"네이버|Naver", "카카오": r"카카오", "LGU+": r"LG\s?유플러스|LGU\+|유플러스", "LG CNS": r"LG\s?CNS",
    "삼성": r"삼성", "SK하이닉스": r"SK\s?하이닉스|하이닉스", "마벨": r"마벨|Marvell", "바이낸스": r"바이낸스|Binance",
    "현대차": r"현대차|현대자동차", "업스테이지": r"업스테이지", "NC AI": r"NC\s?AI|엔씨", "독파모": r"독파모|독자\s?AI",
}
BUCKET = {
    "조달": r"채권|회사채|bond|조달|증자|공모|대출|차입|보증|IPO|상장|주식 발행|stock sale",
    "투자": r"투자 유치|라운드|raises|funding|기업가치|밸류|valuation|투자금|지분",
    "인수": r"인수|acqui|합병|M&A",
    "모델": r"모델|model|LLM|파운데이션",
    "로봇": r"로봇|휴머노이드|robot|humanoid",
    "DC": r"데이터센터|data center|AIDC|기가와트|\dGW|GPU",
    "보안": r"보안|해킹|security|취약점|사이버|hack|탈취",
    "인사": r"영입|CEO|임원|퇴사|appoint|hire|책임자",
    "고객센터": r"AICC|고객센터|상담|콜봇",
    "평가": r"평가|심사|선정",
}

NARROW = {"조달", "투자", "인수", "고객센터", "평가"}
_GEN = set("""투자 출시 공개 시장 사업 개발 확대 추진 도입 진출 강화 구축 협력 제휴 참여 합류 선정 계약 발표 검토 논의
공급 수주 생산 운영 활용 지원 확보 규모 기업 회사 국내 해외 글로벌 최대 최초 모델 플랫폼 서비스 기술 솔루션 인프라
데이터센터 데이터 반도체 로봇 에이전트 보안 달러 조원 억원 단독 속보 the and for with new ai 인공지능 오늘""".split())

def content_toks(s):
    s = s or ""
    for p in ENT.values():
        s = re.sub(p, " ", s)
    out = set()
    for t in re.split(r"[^0-9A-Za-z가-힣]+", s.lower()):
        if len(t) >= 2 and t not in _GEN and not t.isdigit():
            out.add(t)
    return out

def ents_of(s):
    return {k for k, p in ENT.items() if re.search(p, s or "")}

def buckets_of(s):
    return {k for k, p in BUCKET.items() if re.search(p, s or "", re.I)}

def prev_business_day(day):
    try:
        import holidays
        kr = holidays.KR(years=[day.year, day.year - 1])
    except Exception:
        kr = {}
    d = day - datetime.timedelta(days=1)
    while d.weekday() >= 5 or d in kr:
        d -= datetime.timedelta(days=1)
    return d

def norm(s):
    s = re.sub(r"<[^>]+>", "", s or "")
    s = s.replace(" ", " ")
    s = re.sub(r"[\s\"'“”‘’·…\.]+", "", s)
    return s

def load_cand(work, day):
    cand = {}
    p = os.path.join(work, "out", "prompt-%s.txt" % day)
    for line in open(p, encoding="utf-8"):
        if line.startswith('{"idx"'):
            try: o = json.loads(line); cand[str(o["idx"])] = o
            except Exception: pass
        elif line.startswith('{"nolink_idx"'):
            try: o = json.loads(line); cand["N%s" % o["nolink_idx"]] = o
            except Exception: pass
    return cand

def load_verdict(work, day):
    rows = {}
    with open(os.path.join(work, "out", "verdict-%s.csv" % day), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows[str(r["idx"]).strip()] = r
    return rows

def load_sent(mnt, day, days=30):
    out = []
    floor = (datetime.date.fromisoformat(day) - datetime.timedelta(days=days)).isoformat()
    for p in sorted(glob.glob(os.path.join(mnt, "out", "brief-*.md"))):
        m = re.search(r"brief-(\d{4}-\d{2}-\d{2})\.md$", os.path.basename(p))
        if not m or m.group(1) >= day or m.group(1) < floor:
            continue
        txt = open(p, encoding="utf-8").read()
        items = re.split(r"(?m)^(?=\*\*\d+\\?\.)", txt)
        for it in items:
            mm = re.match(r"\*\*\d+\\?\.\s*(.+?)\*\*", it)
            if not mm:
                continue
            head = mm.group(1)
            body = " ".join(re.findall(r"주요 내용:\*\*\s*(.+)", it))
            out.append((m.group(1), head, head + " " + body))
    return out

def body_of(cache, cand_row, idx_url_override=None):
    for u in [idx_url_override, (cand_row or {}).get("url")]:
        if u and cache.get(u):
            return cache[u]
    return ""

def lead_dates(body, day):
    """본문 앞 700자에서 날짜 표현을 찾아 (표현, 날짜) 목록."""
    out = []
    lead = (body or "")[:700]
    for m in re.finditer(r"(\d{1,2})월\s?(\d{1,2})일", lead):
        try: out.append((m.group(0), datetime.date(day.year, int(m.group(1)), int(m.group(2)))))
        except ValueError: pass
    for m in re.finditer(r"(?<![월\d])(\d{1,2})일\s?\((?:현지\s?시간|현지시각)\)|(?<![월\d])(\d{1,2})일\s(?:밝혔|발표|공개|보도|전했)", lead):
        n = int(m.group(1) or m.group(2))
        d = day.replace(day=1)
        try:
            cand = day.replace(day=n)
        except ValueError:
            continue
        if cand > day:  # 지난달
            prev = (day.replace(day=1) - datetime.timedelta(days=1))
            try: cand = prev.replace(day=n)
            except ValueError: continue
        out.append((m.group(0), cand))
    for m in re.finditer(r"지난\s?(\d{1,2})월", lead):
        try:
            mo = int(m.group(1)); yr = day.year if mo < day.month else day.year - 1
            out.append((m.group(0), datetime.date(yr, mo, 1)))
        except ValueError: pass
    return out

def in_scope(idx, v, cand, body):
    g = v.get("grade", "")
    if g == "고":
        return "고"
    reason = v.get("reason", "")
    if g == "저" and SKIP_LOW.match(reason):
        return ""
    head = (cand.get(idx) or {}).get("headline", "") or ""
    text = head + " " + (body or "")[:400]
    tags = []
    if PRIORITY.search(text): tags.append("최우선")
    if COMPETITOR.search(head): tags.append("경쟁사")
    if BIGTECH.search(head) and BIGTECH_TOPIC.search(head): tags.append("빅테크")
    if GOV.search(text) and SEC.search(head): tags.append("정부보안")
    if not tags and g == "중" and EXEC_VERB.search((body or "")[:400]):
        tags.append("실행동사")
    return "+".join(tags)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--mnt", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--scope", action="store_true")
    ap.add_argument("--template", action="store_true")
    ap.add_argument("--facts", default=None, help="facts CSV 경로(기본 out/facts-<날짜>.csv)")
    ap.add_argument("--verdict", default=None, help="verdict CSV 경로(검증용 대체)")
    a = ap.parse_args()
    day = datetime.date.fromisoformat(a.date)
    # 현지시간 발표가 한국 기사로 하루 늦게 오는 것을 감안해 직전 영업일 하루 전까지 창으로 본다
    win = prev_business_day(day) - datetime.timedelta(days=1)
    cand = load_cand(a.work, a.date)
    verdict = load_verdict(a.work, a.date) if not a.verdict else {
        str(r["idx"]).strip(): r for r in csv.DictReader(open(a.verdict, encoding="utf-8"))}
    cp = os.path.join(a.work, "out", "bodycache-%s.json" % a.date)
    cache = json.load(open(cp, encoding="utf-8")) if os.path.exists(cp) else {}
    sent = load_sent(a.mnt, a.date)

    scope = {}
    for idx, v in verdict.items():
        body = body_of(cache, cand.get(idx))
        t = in_scope(idx, v, cand, body)
        if t:
            scope[idx] = t

    def auto_clues(idx):
        c = cand.get(idx) or {}
        head = c.get("headline", "")
        body = body_of(cache, c)
        e, b = ents_of(head), buckets_of(head)
        flows = []
        for d, h, txt in sent:
            se, sb = ents_of(h), buckets_of(txt)
            shared_b = b & sb
            if not (e & se) or not shared_b:
                continue
            # 넓은 사건종류(모델·DC·보안·로봇·인사)만 겹치면 같은 흐름이라 하기엔 약하다 —
            #   주체 말고도 구체 낱말이 하나 더 겹쳐야 한다(11-04 시험: 오픈AI+보안이 아무 보안 기사와 묶였다).
            if not (shared_b & NARROW) and not (content_toks(head) & content_toks(h)):
                continue
            if True:
                flows.append("%s %s" % (d[5:], h[:40]))
        dates = [(s, d) for s, d in lead_dates(body, day)]
        retro = RETRO.search(body[:900] or "") if body else None
        return head, body, flows, dates, retro

    if a.scope or a.template:
        print("[facts] %s 범위 집합 %d건 (창: %s ~ %s)" % (a.date, len(scope), win, day))
        rows = []
        for idx, t in scope.items():
            head, body, flows, dates, retro = auto_clues(idx)
            print("  %-5s %s %-12s %s" % (idx, verdict[idx]["grade"], t, head[:60]))
            if flows: print("        흐름후보: " + " / ".join(flows[:3]))
            if dates: print("        본문날짜: " + ", ".join("%s→%s" % (s, d) for s, d in dates[:4]))
            if retro: print("        재조명표현: " + retro.group(0))
            if not body: print("        (본문 없음)")
            rows.append([idx, "", "", "", "", "", "", ""])
        if a.template:
            tp = os.path.join(a.work, "out", "facts-%s.todo.csv" % a.date)
            with open(tp, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f); w.writerow("idx 사건일 근거 유형 단계 흐름 변화 비고".split()); w.writerows(rows)
            print("[facts] 빈 행 저장: %s — 본문을 읽고 채워 out/facts-%s.csv 로 저장" % (tp, a.date))
        return 0

    fp = a.facts or os.path.join(a.work, "out", "facts-%s.csv" % a.date)
    if not os.path.exists(fp):
        print("[facts] facts 파일 없음: %s — `run.sh facts --template` 로 범위 집합을 받아 채우세요" % fp)
        return 2
    facts = {}
    for r in csv.DictReader(open(fp, encoding="utf-8")):
        facts[str(r["idx"]).strip()] = {k: (v or "").strip() for k, v in r.items()}

    fails, warns = [], []
    for idx, t in scope.items():
        g = verdict[idx]["grade"]
        head, body, flows, dates, retro = auto_clues(idx)
        tag = "%s %s(%s) %s" % (idx, g, t, head[:44])
        f = facts.get(idx)
        if not f:
            fails.append(("R1", tag, "facts 행 없음 — 본문에서 사실을 먼저 채울 것")); continue
        bad = [k for k, ok in (("유형", f.get("유형") in TYPES), ("단계", f.get("단계") in ("실행", "논의"))) if not ok]
        if f.get("흐름") not in ("신규",) and not re.match(r"^\d{2}-\d{2}$", f.get("흐름", "")): bad.append("흐름")
        if re.match(r"^\d{2}-\d{2}$", f.get("흐름", "")) and f.get("변화") not in DELTAS: bad.append("변화")
        try: ev = datetime.date.fromisoformat(f.get("사건일", ""))
        except ValueError: ev = None; bad.append("사건일")
        if bad:
            fails.append(("R0", tag, "닫힌 칸 값 오류: " + ",".join(bad))); continue
        q = f.get("근거", "")
        if body and not (q.startswith("find:") or q == "본문없음"):
            if not q or norm(q) not in norm(body):
                fails.append(("R2", tag, "근거 인용이 본문에 없음: " + q[:40]))
        if g == "고" and ev < win:
            fails.append(("R3", tag, "사건일 %s 이 창(%s~) 밖 — 오늘 생긴 일이 아님" % (ev, win)))
        if g == "고" and dates and max(d for _, d in dates) < win:
            fails.append(("R3b", tag, "본문 리드의 날짜가 모두 창 밖(%s) — 코드가 찾은 사실" % ", ".join("%s→%s" % (s_, d) for s_, d in dates[:3])))
        if g == "고" and f["유형"] in STALE_TYPES:
            fails.append(("R4", tag, "유형 '%s' 는 '고' 불가" % f["유형"]))
        # R5 는 '고'에만 — '중'·'저'에 걸면 게이트가 소음이 된다(11-04 시험: 79건 중 8건이 넓은 겹침)
        if g == "고" and flows and f["흐름"] == "신규":
            fails.append(("R5", tag, "코드가 찾은 같은 흐름 발송분: " + flows[0] + " — 흐름=신규 불가(발송일과 변화를 적을 것)"))
        if g == "고" and re.match(r"^\d{2}-\d{2}$", f["흐름"]) and f["변화"] in WEAK_DELTAS:
            fails.append(("R6", tag, "이미 실은 흐름(%s)의 '%s' 는 '고' 불가" % (f["흐름"], f["변화"])))
        if g in ("중", "저") and t != "실행동사" and f["단계"] == "실행" and f["유형"] in EXEC_TYPES \
                and not NOTE_CODES.match(f.get("비고", "")):
            fails.append(("R7", tag, "실행된 %s(%s)를 %s 로 둔 닫힌 사유(비고 코드)가 없음" % (f["유형"], t, g)))
        for s, d in dates:
            if d < win and ev >= win:
                warns.append(("W1", tag, "본문 리드 '%s'(→%s) 인데 사건일 %s" % (s, d, ev))); break
        if retro and f["유형"] != "분석·재조명":
            warns.append(("W2", tag, "본문 재조명 표현 '%s' — 유형 확인" % retro.group(0)))
        if body and f["단계"] == "논의" and EXEC_VERB.search(body[:400]):
            warns.append(("W4", tag, "본문 리드에 실행 동사 '%s' 가 있는데 단계=논의 — 다시 확인" % EXEC_VERB.search(body[:400]).group(0)))
        if g == "고" and f["단계"] == "논의" and f["유형"] != "투자·조달":
            warns.append(("W3", tag, "논의 단계 '고' — 독립 검토 대상"))

    print("[facts] %s 범위 집합 %d건 / facts %d행 / 창 %s~%s" % (a.date, len(scope), len(facts), win, day))
    for code, tag, msg in fails:
        print("  [실패 %s] %s\n           %s" % (code, tag, msg))
    for code, tag, msg in warns:
        print("  [경고 %s] %s\n           %s" % (code, tag, msg))
    if fails:
        print("[facts] 실패 %d건 — 등급을 facts 에 맞추거나(사실이 맞으면), facts 를 본문으로 다시 확인할 것." % len(fails))
        print("        **문구를 덧붙여 통과시키는 길은 없다.** 칸은 닫힌 값만 받는다.")
        return 1
    print("[facts] 통과 (경고 %d건)" % len(warns))
    return 0

if __name__ == "__main__":
    sys.exit(main())
