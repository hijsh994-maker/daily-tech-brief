# -*- coding: utf-8 -*-
"""rank 비교표 — 다른 사건 유형끼리 무엇으로 줄 세우나 (2026-09-15 신설)

왜 만들었나
-----------
사용자 질문: *"그럼 다른 사건 유형일 때는 어떻게 비교해서 랭킹을 매길 거야?"*

그때까지 **교차 비교 기준이 없었다.** 매번 감으로 줄 세웠다. 근거:
- 학습 모델(`config/rankweights.json`)이 보는 것은 `route`(통로)와
  `spread`(보도수)가 거의 전부다. 이건 *사람이 골랐는가*의 대리 지표지
  *중요한가*의 지표가 아니다.
- 주체 체급 축이 없다 (`t_프런티어` +0.02 ≒ 0).
- `t_F1`(1천억↑) **-0.67**, `t_지정학` **-0.48** 로 **부호가 뒤집혀** 있다
  (6일 257표본 과적합. [[dtb-rank-learned-model]] 에 기록된 그대로).
- SKILL 의 (나) `파급·확정성·SKT 관련성` 은 **세 낱말이 순서가 없어서**
  교차 비교에 쓸 수 없었다.

네 축과 그 순서
---------------
2026-09-15 사용자 확인.

  1. **사업적용** — 우리 사업에 무엇을 바꾸나 (`docs/사업현황.md` 대조)
       최우선3 > 하는사업 > 검토중 > 경쟁사 > 간접 > 없음
  2. **확정성**   — 일어남 > 확정계획 > 추진·협상 > 전망·관측
  3. **파급**     — 산업구조 > 시장판도 > 개별기업
  4. **체급**     — 1~3 이 비슷할 때 갈린다 (`cowork/subject.py`)

**역전을 허용한다(사용자 선택 B).** 1축이 낮아도 **파급이 '산업구조' 급이면**
상단으로 올린다. 우리 사업에 안 걸리는 빅테크 지각변동이 구조적으로 밀리는
것을 막기 위함이다. 다만 **역전할 때는 `verdict.csv` 사유에 `역전:` 을 적는다.**
안 적으면 `run.sh verdict` 가 종료코드 1 을 낸다 — 역전이 만능 열쇠가 되면
축이 있으나 마나이기 때문이다.

**점수를 합산하지 않는다.** 2026-09-14 지시: *"사람은 나란히 놓고 고른다.
한 건씩 등급 찍기는 트레이드오프를 숨긴다."* 이 스크립트는 축 값을 나란히
찍어 줄 뿐이고, 정렬은 사람이 한다. 기본 정렬(1축→2축)은 **출발점**이지 답이 아니다.

쓰는 법
-------
    bash cowork/run.sh grid          # 비교표
    bash cowork/run.sh grid --csv    # out/rankgrid-<날짜>.csv 로도 저장

`bodygo`·`hist` 뒤, rank 매기기 **전**에 본다.
"""
import argparse
import csv
import datetime
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

# ── 1축 사업적용 ─────────────────────────────────────────────
#   docs/사업현황.md 의 절 이름을 그대로 쓴다. 키워드는 그 문서에서 왔다.
BIZ = [
    ("최우선3", 1, ["모두의 ai", "모두의ai", "독파모", "독자 ai 파운데이션",
                    "ai 데이터센터", "aidc", "ai dc", "ai 팩토리", "ai factory",
                    "데이터센터 특별법", "전력계통"]),
    ("하는사업", 2, ["에이닷", "a.x", "aicc", "콜봇", "챗봇", "stt", "tts",
                     "gpuaas", "파운데이션 모델", "국방 ai", "디지털트윈",
                     "ai 에이전트", "에이전틱"]),
    # [2026-09-15 정정] 처음에 검토중(3) > 경쟁사(4) 로 뒀는데 **틀렸다.**
    #   그날 편집자가 승인한 17건의 배열이 반증이다 —
    #   경쟁사 3건이 rank 7·8·11, 검토중 3건이 rank 15·16·17 이었다.
    #   09-14 피드백("경쟁사 대응이 기대 쓰임이므로 비중을 올려도 된다")과도 맞다.
    ("경쟁사", 3, ["kt", "네이버", "카카오", "lg", "lgu+", "유플러스", "네카오"]),
    ("검토중", 4, ["휴머노이드", "로봇 파운데이션", "rfm", "월드모델",
                   "로봇 데이터", "로봇 학습"]),
]
BIZ_LABEL = {1: "최우선3", 2: "하는사업", 3: "경쟁사", 4: "검토중",
             5: "간접", 6: "없음"}

# ── 2축 확정성 ───────────────────────────────────────────────
FIX = [
    ("일어남", 1, ["출시", "공개", "체결", "완료", "확정", "인수", "선정",
                   "발효", "시행", "상장", "승인", "수주", "개시", "배포",
                   "내놨다", "밝혔다", "확보", "달성", "돌파", "급락", "감원",
                   "조달", "발행", "제정", "반박", "일축", "맹비난", "통화",
                   "맡겼다", "품어", "적발", "드러나"]),
    ("확정계획", 2, ["예정", "계획", "로드맵", "내년", "목표", "까지",
                     "공급", "출범", "청사진"]),
    ("추진", 3, ["추진", "검토", "협상", "논의", "나선다", "착수", "제안",
                 "모색", "총력", "막아야", "과제로", "윤곽", "경쟁"]),
    ("전망", 4, ["전망", "관측", "예상", "가능성", "할 듯", "될 듯", "우려",
                 "분석", "기대", "의구심"]),
]
FIX_LABEL = {1: "일어남", 2: "확정계획", 3: "추진", 4: "전망", 5: "?"}


def _norm(s):
    """공백·구두점을 지운다.

    실측 09-15 버그: 헤드라인 'GS, 동해 AI데이터센터 15조 조달계획' 이
    키워드 'ai 데이터센터'(공백 있음)와 안 맞아 1축이 '간접' 으로 떨어졌다.
    최우선3(AI DC) 이 맞다.
    """
    return re.sub(r"[\s·,'\"\u2018\u2019\u201c\u201d\[\]()]+", "", (s or "")).lower()


def _axis(head, table, default):
    h = _norm(head)
    for label, val, kws in table:
        for k in kws:
            if _norm(k) in h:
                return val
    return default


def biz_of(head):
    v = _axis(head, BIZ, None)
    if v:
        return v
    # 회사·정책이 주어지만 위 키워드에 안 걸리면 '간접'. '없음'은 사람이 정한다.
    return 5


def fix_of(head):
    return _axis(head, FIX, 5)


def tier_of(head):
    try:
        from subject import tag
        return tag(head)
    except Exception:
        return "체급?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--csv", action="store_true")
    a = ap.parse_args()

    vp = os.path.join(ROOT, "out", "verdict-%s.csv" % a.date)
    bp = os.path.join(ROOT, "out", "candidates-blind-%s.txt" % a.date)
    if not os.path.exists(vp):
        print("[grid] [실패] %s 없음. 먼저 판정을 끝내세요." % vp)
        sys.exit(2)

    meta = {}
    for line in open(bp, encoding="utf-8") if os.path.exists(bp) else []:
        m = re.match(r"^([NLC]?\d+) \| \S+ \| (.+?) \| (\S*) \| (\S+) \| (?:⟨([^⟩]+)⟩ )?(.+?)(?: \[중복의심.*)?$",
                     line.rstrip())
        if m:
            meta[m.group(1)] = dict(outlet=m.group(2).strip(), spread=m.group(4),
                                    tier=m.group(5) or "체급?", head=m.group(6))

    rows = []
    for r in csv.DictReader(open(vp, encoding="utf-8")):
        if (r["grade"] or "").strip() != "고":
            continue
        d = meta.get(r["idx"], {})
        h = d.get("head", "")
        rows.append(dict(idx=r["idx"], rank=(r.get("rank") or "").strip(),
                         head=h, tier=d.get("tier") or tier_of(h),
                         spread=d.get("spread", "?"),
                         biz=biz_of(h), fix=fix_of(h),
                         reason=(r.get("reason") or "")))

    rows.sort(key=lambda x: (x["biz"], x["fix"]))

    print("[grid] '고' %d건. **기본 정렬은 1축(사업적용)→2축(확정성) 입니다.**" % len(rows))
    print("[grid] 이건 출발점이지 답이 아닙니다. 점수를 합산하지 않습니다.")
    print("[grid] **파급이 '산업구조' 급이면 1축을 역전해 위로 올릴 수 있습니다(B).**")
    print("[grid]   역전하면 verdict 사유에 `역전:<파급 근거>` 를 적으세요.\n")
    print("  %-4s %-8s %-8s %-6s %-4s %-4s %s"
          % ("idx", "1사업적용", "2확정성", "4체급", "보도", "現순위", "헤드라인"))
    print("  " + "-" * 104)
    prev = None
    for x in rows:
        if prev is not None and x["biz"] != prev:
            print("  " + "·" * 104)
        prev = x["biz"]
        print("  %-4s %-8s %-8s %-6s %-4s %-4s %s"
              % (x["idx"], BIZ_LABEL[x["biz"]], FIX_LABEL[x["fix"]],
                 x["tier"], x["spread"], x["rank"] or "-", x["head"][:52]))

    print("\n[grid] **1축 자동값은 '주제 매칭' 이라 손으로 고쳐야 하는 것이 있습니다.**")
    print("[grid]   묻는 것은 '이 주제인가' 가 아니라 **'이 기사가 우리 사업을 바꾸나'** 입니다.")
    print("[grid]   실측 09-15: 한컴 노마디안이 'AI 에이전트' 라는 낱말 때문에 '하는사업'")
    print("[grid]   으로 잡혔지만 실제로는 '없음' 입니다(편집자가 뺀 건).")
    print("\n[grid] 3축(파급)은 기계가 못 매깁니다. 본문을 보고 사람이 정합니다.")
    print("[grid]   산업구조 = 경쟁 지형·규칙이 바뀐다 / 시장판도 = 점유·가격이 바뀐다")
    print("[grid]   개별기업 = 그 회사 일로 끝난다")
    print("[grid] 보도수(다수/소수/단독)는 파급의 **힌트일 뿐** 근거가 아닙니다.")
    print("[grid]   단독이어도 산업구조를 바꾸는 건이 있습니다(09-15 AIDC 시행령).")

    if a.csv:
        p = os.path.join(ROOT, "out", "rankgrid-%s.csv" % a.date)
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["idx", "사업적용", "확정성", "파급(손기입)", "체급",
                        "보도", "현재순위", "헤드라인", "판정사유"])
            for x in rows:
                w.writerow([x["idx"], BIZ_LABEL[x["biz"]], FIX_LABEL[x["fix"]], "",
                            x["tier"], x["spread"], x["rank"], x["head"], x["reason"]])
        print("\n[grid] 저장: %s" % p)


if __name__ == "__main__":
    main()
