# -*- coding: utf-8 -*-
"""신규성 감점 게이트 — '후보로 여러 번 봤다' ≠ '독자가 이미 알고 있다' (2026-09-30 신설)

왜 만들었나
-----------
09-29 LLM 재킹, 09-30 인스팅트 사용기를 내가 '이미 알려진 패턴'·'중복' 으로 내렸고
사용자가 둘 다 되살렸다. 둘 다 **실제로 발송된 적이 없는 이야기**였다.
사용자: *"최근에 LLM 재킹 관련 기사를 올린 적이 없으면 넣어야 한다"*
       *"'후보로 여러 번 봤다'를 '이미 독자에게 알려졌다'로 착각을 없앨 수 있도록
        선별 알고리즘을 업데이트 해줘. 오늘도 같은 실수를 반복했잖아."*

독자가 아는 것은 **발송본(out/brief-*.md·truth-*.json)** 뿐이다. 후보 목록·타임라인의
'·'(미발송)은 내가 본 것이지 독자가 본 것이 아니다.

무엇을 하나
-----------
verdict.csv 의 '중'·'저' 중 **신규성류 사유**(G축·신규성·반복·알려진·철 지난·
'발송본에 실림' 류 중복)로 내린 행마다, 그 헤드라인을 최근 30일 발송본과
사용자 제외 이력(config/rejected.jsonl)에 대조한다(hist.py 와 같은 매칭).
  - 발송 이력도 제외 이력도 **0건**이면 → 걸림. 독자는 이 이야기를 본 적이 없다.
  - 사유가 'MM-DD 발송본에 실림' 이라고 주장하는데 매칭이 0건이면 → 걸림(주장 검증).
통과시키려면 사유에 한 줄을 붙인다:
  `발송근거:<MM-DD 실제 발송 헤드라인>`  — 매칭이 못 잡은 실제 발송분이 있을 때
  `2차검토:<이유>`                        — 신규성이 아니라 다른 이유로도 중·저일 때
근거를 못 대면 그 감점은 무효다 — 신규성 말고 다른 축으로 다시 판정한다.
같은 날 후보끼리의 중복('대표 N', '같은 발표', '흡수')은 대상이 아니다.

    bash cowork/run.sh novelty           # verdict 가 자동 호출
"""
import csv, datetime, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.getcwd() if os.path.isdir(os.path.join(os.getcwd(), "out")) else os.path.dirname(HERE)
sys.path.insert(0, HERE)
import hist  # toks / load_sent / EVENT / PEOPLE / ORGS 재사용

NOVEL = re.compile(r"G축|신규성|반복|알려진|철 지난|이어달리기|발송본에 실림|발송본에 있|발송분")
SAMEDAY = re.compile(r"대표\s*[NL]?\d|같은 발표|같은 오찬|흡수|같은 사건 기사|같은 출시")
OK = re.compile(r"발송근거:|2차검토:|재심:|\d{2}-\d{2} 발표")


import glob
import repeat_gate as rg

_AL = rg.load_aliases(ROOT)
_ENTS = {e.lower() for e in rg.load_entities(ROOT)}


def load_sent_items(day, days=30):
    """발송본을 '항목 단위'로 읽는다: (날짜, 헤드라인, 헤드라인+주요내용+시사점 토큰).
    헤드라인만 대조하면 국문/영문 표기 차이로 놓친다(AMD 'World Labs' vs '월드랩스')."""
    floor = (datetime.date.fromisoformat(day) - datetime.timedelta(days=days)).isoformat()
    out = []
    for p in sorted(glob.glob(os.path.join(ROOT, "out", "brief-*.md"))):
        d = os.path.basename(p)[6:16]
        if d >= day or d < floor:
            continue
        blocks = re.split(r"\n(?=\*\*\d+\\?\.)", open(p, encoding="utf-8").read())
        for b in blocks:
            m = re.match(r"\*\*\d+\\?\.\s*(.+?)\*\*", b)
            if m:
                out.append((d, m.group(1), rg.toks(b, _AL)))
    return out


def _match(title, items, loose=False):
    T = rg.toks(title, _AL)
    hits = []
    for d, h, B in items:
        sh = (T & B) - rg.GENERIC - rg.AGENDA_WORDS
        ent = {x for x in sh if x in _ENTS}
        if (ent and len(sh) >= 2) or len(sh) >= 3 or (loose and (ent or len(sh) >= 2 or any(len(x) >= 3 for x in sh))):
            hits.append((d, h))
    return hits


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().isoformat()
    vp = os.path.join(ROOT, "out", "verdict-%s.csv" % day)
    bp = os.path.join(ROOT, "out", "candidates-blind-%s.txt" % day)
    if not (os.path.exists(vp) and os.path.exists(bp)):
        print("[novelty] 판정/blind 파일 없음 — 건너뜀"); return 0
    head = {}
    for line in open(bp, encoding="utf-8"):
        m = re.match(r"^([NL]?\d+) \| .* \| (?:⟨[^⟩]+⟩ )?(.+?)(?: \[(?:중복의심|반향|링크없음|기자재).*)?(?:\s+\[(?:반복후보|후보만)[^\]]*\])?$",
                     line.rstrip())
        if m:
            head[m.group(1)] = re.sub(r"^\[|\]$", "", m.group(2).strip())
    sent = load_sent_items(day)
    rej = []
    rp = os.path.join(ROOT, "config", "rejected.jsonl")
    if os.path.exists(rp):
        for l in open(rp, encoding="utf-8"):
            try:
                j = json.loads(l); rej.append((j.get("date", ""), j.get("headline", ""), rg.toks(j.get("headline", ""), _AL)))
            except Exception:
                pass
    rows = [r for r in csv.DictReader(open(vp, encoding="utf-8")) if r.get("idx")]
    bad, checked = [], 0
    for r in rows:
        g = (r.get("grade") or "").strip(); why = r.get("reason") or ""
        if g not in ("중", "저") or not NOVEL.search(why):
            continue
        if SAMEDAY.search(why) and not re.search(r"발송본|발송분", why):
            continue
        checked += 1
        if OK.search(why):
            continue
        title = head.get(r["idx"], "")
        # 사유가 특정 날짜 발송을 주장하면 그 날 발송본만 느슨하게 대조한다(주장 검증)
        md = re.search(r"(\d{2})-(\d{2}) 발송", why)
        if md:
            dd = "%s-%s-%s" % (day[:4], md.group(1), md.group(2))
            s_hit = _match(title, [x for x in sent if x[0] == dd], loose=True)
        else:
            s_hit = _match(title, sent)
        r_hit = _match(title, rej)
        if not s_hit and not r_hit:
            bad.append((r["idx"], g, title[:50], why[:60]))
    print("[novelty] 신규성류 사유로 내린 중·저 %d건 점검 (발송 %d건·사용자제외 %d건 대조)"
          % (checked, len(sent), len(rej)))
    if not bad:
        print("[novelty] 걸린 항목 없음"); return 0
    print("[novelty] **발송 이력 0건인데 신규성으로 내린 %d건** — 독자는 이 이야기를 본 적이 없습니다" % len(bad))
    for i, g, h, w in bad[:30]:
        print("   %-5s %s  %s\n          사유: %s" % (i, g, h, w))
    print("[novelty] 후보로 여러 번 본 것은 감점 근거가 아닙니다(2026-09-30 사용자 지시).")
    print("[novelty] 신규성 말고 다른 축으로 다시 판정하거나, 사유에 `발송근거:<MM-DD 발송 헤드라인>`")
    print("[novelty] (매칭이 못 잡은 실제 발송분) 또는 `2차검토:<다른 이유>` 를 붙이세요.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
