# -*- coding: utf-8 -*-
"""'고' 판정 건의 과거 발송 이력 조회 (2026-09-15 신설)

왜 만들었나
-----------
2026-09-15 편집자가 「앤트로픽, '2조달러 IPO' 나스닥 속도」를 뺐다.

> *"이전부터 IPO 관련 기사들이 계속 나왔었고, 오늘 기사에는 새로운 내용이
>  거의 없어서 내가 빼자고 한거야. 이것도 히스토리를 찾아보면 관련 기사들이
>  있을거야. 그런것들을 니가 파악하고 있었으면 이 기사는 제외했을거야."*

실제로 발송 이력에 **7건**이 있었다(08-21 역대 최대 IPO 추진 / 08-24 2조 달러
IPO / 08-25 스페이스X 기록 / 09-04 마통 20조 확보 / 09-08 10년치 컴퓨팅 /
09-09 투자적격 등급 …). 타임라인에도 다 들어 있었지만 **네 개 줄로 흩어져**
있었다 — `오픈·상장·앤트로픽` / `역대·최대·IPO` / `앤트로픽·클로드·달러` /
그리고 오늘 후보는 `[새] 앤트로픽·나스닥·IPO`.

타임라인은 IDF 특징 낱말 3개로 묶는데, '역대'·'최대'·'상장' 같은 흔한 수식어가
특징 낱말로 뽑히면 한 이야기가 여러 줄로 쪼개진다. **묶기 알고리즘을 또
고치는 것은 09-14 에 이미 실패한 길**이므로(조직명 묶기 → 한 줄에 67건),
여기서는 묶지 않고 **'고' 한 건씩 직접 조회**한다.

무엇을 하나
-----------
`out/verdict-<날짜>.csv` 의 '고' 전량에 대해, 과거 발송분(`truth-*.json` +
`out/brief-*.md`)에서 **주체 토큰과 사건 토큰을 함께 공유하는 헤드라인**을
날짜순으로 보여준다. 묶음이 아니라 건별 조회라 흩어짐의 영향을 안 받는다.

    bash cowork/run.sh hist            # '고' 전량
    bash cowork/run.sh hist 1 19       # 특정 idx 만
    bash cowork/run.sh hist --days 60  # 조회 기간 (기본 30일)

판단은 사람이 한다. 출력이 많다고 자동으로 빼지 않는다 — 후속 보도라도
새 사실이 있으면 싣는다([[dtb-repeat-send-pattern]]).
"""
import argparse
import csv
import datetime
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# 사건을 특정하지 못하는 낱말. 이것만 겹치는 것은 '관련 있음'이 아니다.
STOP = set("""
AI 인공지능 기술 서비스 사업 기업 시장 국내 글로벌 세계 최초 최대 역대 신규
출시 공개 발표 추진 확대 강화 도입 협력 지원 계획 전망 예정 관련 위해 통해
올해 내년 지난 이번 오늘 대한 만에 대해 라며 라고 한다 했다 있다 없다 된다
""".split())

# 한 건을 '같은 이야기'로 묶는 데 실제로 쓰이는 사건어.
# [2026-09-16 확장] 09-16 에 네 건을 전부 놓쳤다. 원인의 절반이 이 목록의
#   좁음이었다 — '해킹'(허깅페이스), '예산'(ICT R&D), '속도조절·안전·표준'
#   (아모데이 vs 젠슨황) 이 없어서 사건어 교집합이 비었다.
EVENT = """IPO 상장 나스닥 인수 합병 투자 조달 유치 감원 해고 출시 공개 계약
수주 소송 규제 법안 시행령 평가 선정 탈락 제휴 파트너십 데이터센터 모델
해킹 침해 유출 사고 보안 예산 R&D 연구개발 과제 공고 표준 안전 속도조절
감속 가이드라인 인증 특허 개인정보 유권해석 구독 요금제 실증 착공 가동""".split()

# 사건어가 아니라 '주체'로 묶이는 이야기가 있다. 인물·기업 고유명사 두 개가
# 겹치면 사건어가 안 맞아도 같은 주제일 확률이 높다. [2026-09-16 신설]
#   실측 09-16: 후보 '아모데이 표준 만들자 / 젠슨황 일률규제 안돼' 와
#   09-14 발송 '올트먼·아모데이, 머스크까지 AI 속도 늦추자 한목소리' 는
#   사건어 교집합이 비어 하드 매칭에서 빠졌다. 주체(아모데이)는 겹쳤다.
#   인물은 조직보다 훨씬 구체적이라 **1명만 겹쳐도** 표시한다.
PEOPLE = """아모데이 올트먼 젠슨황 머스크 트럼프 최태원 유경상 베니오프
저커버그 피차이 황정수 정의선 이재용""".split()
#   조직은 흔해서 **2개 이상** 겹칠 때만 표시한다.
ORGS = """앤트로픽 앤스로픽 오픈ai 구글 메타 엔비디아 마이크로소프트 애플 아마존
네이버 카카오 kt lg skt 삼성 sk하이닉스 팔란티어 세일즈포스 퍼플렉시티
허깅페이스 보스턴다이내믹스 유니트리 리벨리온 퓨리오사 과기정통부 개인정보위
딥시크 유클리드 테슬라 소프트뱅크 크루소 코그니션 지식재산처""".split()
ACTORS = PEOPLE + ORGS

# 같은 사람을 다르게 적는 표기를 하나로 맞춘다. '젠슨황' vs '젠슨 황' 때문에
# 09-16 에 매칭이 통째로 빠졌다. (토큰화가 임계값보다 먼저다 — 09-14 교훈)
ALIAS = {"젠슨": "젠슨황", "황": None, "아모데이": "아모데이", "다리오": "아모데이",
         "샘": None, "일론": "머스크", "사티아": "나델라"}


def toks(s):
    """조사를 떼고 실질 낱말만. (2026-09-14 타임라인 버그와 같은 처리)"""
    s = (s or "").replace("젠슨 황", "젠슨황").replace("다리오 아모데이", "아모데이")
    out = set()
    for w in re.findall(r"[가-힣A-Za-z0-9]+", s):
        w = re.sub(r"(은|는|이|가|을|를|에|의|와|과|도|로|으로|에서|에게|부터|까지)$", "", w)
        if len(w) >= 2 and w not in STOP:
            w = w.lower()
            w = ALIAS.get(w, w)
            if w:
                out.add(w)
    return out


def load_sent(days, today):
    """과거 발송 헤드라인: (날짜, 헤드라인). truth 와 brief 양쪽에서.

    **오늘치는 뺀다** — 안 빼면 오늘 발송본이 자기 자신과 겹쳐 1건으로 잡힌다.
    """
    cut = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    seen, rows = set(), []
    for p in sorted(glob.glob(os.path.join(ROOT, "truth-*.json"))):
        d = os.path.basename(p)[6:16]
        if d < cut or d >= today:
            continue
        try:
            data = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        items = data if isinstance(data, list) else data.get("items", [])
        for it in items:
            h = (it.get("headline") or it.get("title") or "").strip() if isinstance(it, dict) else str(it)
            if h and (d, h) not in seen:
                seen.add((d, h))
                rows.append((d, h))
    for p in sorted(glob.glob(os.path.join(ROOT, "out", "brief-*.md"))):
        d = os.path.basename(p)[6:16]
        if d < cut or d >= today:
            continue
        for line in open(p, encoding="utf-8"):
            m = re.match(r"^\*\*\d+\\?\.\s*(.+?)\*\*\s*\(", line)
            if m and (d, m.group(1)) not in seen:
                seen.add((d, m.group(1)))
                rows.append((d, m.group(1)))
    return sorted(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("idx", nargs="*")
    a = ap.parse_args()

    vp = os.path.join(ROOT, "out", "verdict-%s.csv" % a.date)
    bp = os.path.join(ROOT, "out", "candidates-blind-%s.txt" % a.date)
    if not os.path.exists(vp):
        print("[hist] [실패] %s 없음. 먼저 판정을 끝내세요." % vp)
        sys.exit(2)

    head = {}
    if os.path.exists(bp):
        for line in open(bp, encoding="utf-8"):
            m = re.match(r"^([NL]?\d+) \| .* \| (.+?)(?: \[중복의심.*)?$", line.rstrip())
            if m:
                head[m.group(1)] = m.group(2)

    gos = [r for r in csv.DictReader(open(vp, encoding="utf-8"))
           if (r["grade"] or "").strip() == "고"]
    if a.idx:
        gos = [r for r in gos if r["idx"] in a.idx]
    gos.sort(key=lambda r: int(r["rank"]) if (r.get("rank") or "").strip().isdigit() else 999)

    sent = load_sent(a.days, a.date)
    print("[hist] '고' %d건 x 최근 %d일 발송 %d건 대조" % (len(gos), a.days, len(sent)))
    print("[hist] 물을 것: **이 줄들에 없는 새 사실을 한 문장으로 쓸 수 있는가.**\n")

    flagged = 0
    softn = 0
    sig = {}          # [2026-09-22] 건별 신호를 파일로 남긴다(signals.py 주석 참조)
    for r in gos:
        h = head.get(r["idx"], "")
        t = toks(h)
        ev = t & {e.lower() for e in EVENT}
        people_t = t & {x.lower() for x in PEOPLE}
        orgs_t = t & {x.lower() for x in ORGS}
        hits, soft = [], []
        for d, ph in sent:
            pt = toks(ph)
            shared = t & pt
            # (1) 하드 — 사건어를 공유하고, 그 밖의 실질 낱말도 겹칠 때.
            if ev and (ev & pt) and len(shared - ev) >= 1 and len(shared) >= 2:
                hits.append((d, ph, sorted(shared)[:4]))
                continue
            # (2) 소프트 — 사건어가 안 맞아도 주체가 겹치면 같은 주제일 수 있다.
            #     인물은 1명만 겹쳐도(단 다른 실질 낱말 1개 추가 조건),
            #     조직은 2개 이상일 때. 자동으로 빼지 않고 표시만 한다.
            sp = people_t & pt
            so = orgs_t & pt
            if sp or len(so) >= 2:
                soft.append((d, ph, sorted(sp | so)[:4]))
        sig[r["idx"]] = {"star": len(hits), "overlap": bool(soft)}
        mark = "★" if len(hits) >= 3 else (" " if hits else " ")
        print("%s rank %-3s [%s] %s" % (mark, (r.get("rank") or "-"), r["idx"], h[:62]))
        if not hits and not soft:
            print("      과거 발송 이력 없음 — 처음 나온 이야기\n")
            continue
        flagged += 1 if len(hits) >= 3 else 0
        if hits:
            for d, ph, sh in hits[-6:]:
                print("      %s  %s" % (d, ph[:64]))
            print("      (%d건 / 공유: %s)" % (len(hits), ", ".join(hits[-1][2])))
        if soft:
            softn += 1
            print("      [주체겹침] 사건어는 달라도 같은 주체가 최근 발송에 있었습니다:")
            for d, ph, sa in soft[-4:]:
                print("      · %s  %s   (주체: %s)" % (d, ph[:56], ", ".join(sa)))
        print()

    if flagged:
        print("[hist] ★ %d건은 **3회 이상 나간 이야기**입니다. 새 사실을 못 쓰면 '중' 으로." % flagged)
    if softn:
        print("[hist] [주체겹침] %d건 — 사건어가 달라 하드 매칭에는 안 걸렸지만" % softn)
        print("       같은 주체가 최근 발송에 있었습니다. **이어달리기 기사인지 본문으로 확인.**")
        print("       (2026-09-16: 이 표시가 없어 아모데이·젠슨황 건을 rank 1 로 올렸습니다)")
    try:
        sys.path.insert(0, os.path.join(ROOT, "cowork"))
        import signals
        signals.record(ROOT, a.date, "hist", sig)
        print("[hist] 건별 신호 기록: out/signals-%s.json (★·주체겹침)" % a.date)
    except Exception as e:
        print("[hist] [경고] 신호 기록 실패: %s" % e)
    print("[hist] 자동으로 빼지 않습니다. 후속 보도라도 새 사실·새 앵글이 있으면 싣습니다.")


if __name__ == "__main__":
    main()
