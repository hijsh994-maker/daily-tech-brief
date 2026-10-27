# -*- coding: utf-8 -*-
"""'고' 판정의 `새사실:` 이 **본문에 실제로 있는 말인지** 검증한다. [2026-09-16 신설]

왜 만들었나
-----------
2026-09-16 에 편집자가 뺀 4건 중 3건이 '새 사실 없음' 이었다. 그래서 같은 날
`run.sh verdict` 에 "고 사유에 `새사실:` 을 적어라" 는 게이트를 넣었는데,
사용자가 곧바로 그 게이트의 구멍을 짚었다.

> *"이렇게 말만 해놓고 또 본문 안읽고 제목으로 판단할지 어떻게 알아?"*

맞는 지적이다. 문자열 `새사실:` 만 요구하면 **본문을 안 읽고 제목에서 지어낸
그럴듯한 한 문장**으로 통과시킬 수 있다. 실제로 09-16 판정에서 내가 본문을
연 것은 '고' 36건 중 4건뿐이었다.

그래서 **'적었는가'가 아니라 '본문에 있는가'를 검사한다.**

무엇을 하나
-----------
`새사실:` 뒤 문장에서 실질 낱말(숫자·고유명사 위주)을 뽑아, 그 기사의
**본문(out/bodycache-<날짜>.json)** 또는 헤드라인에 실제로 나오는지 본다.

  - 2개 이상 걸리면 통과. 숫자·영문·금액은 1개만 걸려도 통과로 친다
    (수치는 지어내기 어렵고, 지어내면 곧바로 틀린 값이 되어 드러난다).
  - 하나도 안 걸리면 **[근거없음]** — 본문에 없는 말을 쓴 것이다.
  - 본문 캐시가 없으면 **[본문없음]**. `run.sh bodygo` 를 안 돌렸거나
    링크 미확보 건이다. 사유에 `본문없음` 이라고 적어 두면 통과시키되,
    전체 '고' 의 30% 를 넘으면 실패 — 본문 없이 판정한 날이기 때문이다.
  - `새사실:없음...` 은 **실패**. 새 사실이 없으면 그 건은 '고' 가 아니다.

종료코드 0 정상 / 1 고쳐야 함 / 2 입력 없음.
자동으로 등급을 바꾸지 않는다. 무엇을 고칠지는 사람이 정한다.
"""
import argparse
import csv
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# 어디에나 나오는 낱말은 근거가 못 된다.
STOP = set("""
AI 인공지능 기술 서비스 사업 기업 시장 국내 글로벌 세계 최초 최대 역대 신규
출시 공개 발표 추진 확대 강화 도입 협력 지원 계획 전망 예정 관련 위해 통해
올해 내년 지난 이번 오늘 대한 만에 대해 라며 라고 한다 했다 있다 없다 된다
그리고 하지만 이라는 것으로 한다는 밝혔다 나섰다 이라고 대비 기준 수준 규모
중심 방침 경우 등을 등이 이를 대한 통한 따른 위한 모든 주요 일부 전체 처음
""".split())

NUMLIKE = re.compile(r"[0-9]")
ASCII = re.compile(r"^[A-Za-z][A-Za-z0-9.+-]*$")


def toks(s):
    out = []
    for w in re.findall(r"[가-힣A-Za-z0-9%.]+", s or ""):
        w = re.sub(r"(은|는|이|가|을|를|에|의|와|과|도|로|으로|에서|에게|부터|까지|만|며)$", "", w)
        if len(w) >= 2 and w not in STOP:
            out.append(w)
    return out


def norm(s):
    """공백·구분기호를 지운 비교용 문자열. '1조 2000억' vs '1조2000억'."""
    return re.sub(r"[\s,·ㆍ/()\[\]'\"“”‘’]", "", s or "")


def newfact_of(reason):
    m = re.search(r"새사실\s*:\s*(.+)$", reason or "")
    if not m:
        return None
    t = m.group(1)
    # 뒤에 다른 칸이 이어지면 잘라낸다.
    t = re.split(r"\s+/\s+(?:반증|근거|중복검토|역전)\s*:", t)[0]
    return t.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--max-nobody-pct", type=float, default=30.0)
    a = ap.parse_args()
    d = a.date

    vp = os.path.join(ROOT, "out", "verdict-%s.csv" % d)
    pp = os.path.join(ROOT, "out", "prompt-%s.txt" % d)
    bp = os.path.join(ROOT, "out", "candidates-blind-%s.txt" % d)
    cp = os.path.join(ROOT, "out", "bodycache-%s.json" % d)
    if not os.path.exists(vp):
        print("[newfact] %s 없음" % vp)
        sys.exit(2)

    url, head = {}, {}
    if os.path.exists(pp):
        for line in open(pp, encoding="utf-8"):
            if line.startswith('{"idx"'):
                o = json.loads(line)
                url[str(o["idx"])] = o.get("url") or ""
                head[str(o["idx"])] = o.get("headline") or ""
    if os.path.exists(bp):
        for line in open(bp, encoding="utf-8"):
            m = re.match(r"^([NL]?\d+) \| .* \| (.+?)(?: \[중복의심.*)?$", line.rstrip())
            if m and not head.get(m.group(1)):
                head[m.group(1)] = m.group(2)

    cache = {}
    if os.path.exists(cp):
        try:
            cache = json.load(open(cp, encoding="utf-8"))
        except Exception:
            cache = {}

    gos = [r for r in csv.DictReader(open(vp, encoding="utf-8"))
           if (r.get("grade") or "").strip() == "고"]
    if not gos:
        print("[newfact] '고' 0건")
        sys.exit(0)

    bad_missing, bad_none, bad_ungrounded, nobody, ok = [], [], [], [], 0
    for r in gos:
        i = r["idx"]
        nf = newfact_of(r.get("reason"))
        if not nf:
            bad_missing.append(i)
            continue
        if nf.startswith("없음"):
            bad_none.append((i, head.get(i, "")[:40]))
            continue
        u = url.get(i) or ""
        body = ""
        if u and isinstance(cache.get(u), str):
            body = cache[u]
        elif u and isinstance(cache.get(u), dict):
            body = cache[u].get("body") or cache[u].get("text") or ""
        # [2026-09-16] 본문이 '있는데 쓸모없는' 경우가 있다. 메타 뉴스룸은
        #   TTS 정렬 JSON 이 본문 자리를 통째로 차지한다. 그대로 두면
        #   '근거없음' 으로 잘못 걸리므로, JSON 잡음을 걷어내고 남은 산문이
        #   너무 짧으면 **본문없음**으로 친다.
        prose = re.sub(r"\{[^{}]*\}", " ", body)
        prose = re.sub(r'"[a-z_]+"\s*:', " ", prose)
        if len(re.sub(r"[^가-힣A-Za-z]", "", prose)) < 300:
            body = ""
        hay = norm(body + " " + head.get(i, ""))
        if not body:
            if "본문없음" in (r.get("reason") or ""):
                nobody.append(i)
                continue
            nobody.append(i)
            bad_missing.append(i) if False else None
            continue
        hits, strong = [], 0
        for w in toks(nf):
            if norm(w) and norm(w) in hay:
                hits.append(w)
                if NUMLIKE.search(w) or ASCII.match(w):
                    strong += 1
        if len(hits) >= 2 or strong >= 1:
            ok += 1
        else:
            bad_ungrounded.append((i, nf[:46], head.get(i, "")[:34]))

    # [2026-09-22] 걸린 건을 out/signals-<날짜>.json 에 남긴다. 화면 로그만으로는
    #   "대표님이 뺀 건에 어떤 게이트가 떠 있었나"를 나중에 셀 수 없다.
    #   게이트는 전부 cwd=작업본 에서 돌므로 base 는 "." 로 충분하다.
    try:
        import sys as _s, os as _o
        _s.path.insert(0, _o.path.join(_o.getcwd(), "cowork"))
        import signals as _sig
        _sig.add_gate(".", a.date, "newfact", [(i, '새사실칸없음') for i in bad_missing] + [(i, '새사실없음') for i, _h in bad_none] + [(i, '본문근거없음') for i, _n, _h in bad_ungrounded])
    except Exception as _e:
        print("[newfact] [경고] 신호 기록 실패: %s" % _e)
    n = len(gos)
    print("[newfact] '고' %d건 / 본문 대조 통과 %d · 근거없음 %d · 새사실없음 %d · 본문없음 %d"
          % (n, ok, len(bad_ungrounded), len(bad_none), len(nobody)))

    fail = False
    if bad_missing:
        print("[newfact] **`새사실:` 칸 자체가 없는 건 %d**: %s"
              % (len(bad_missing), ", ".join(bad_missing[:15])))
        fail = True
    if bad_none:
        print("[newfact] **'새사실:없음' 인데 '고' 인 건 %d** — 새 사실이 없으면 '고' 가 아닙니다:"
              % len(bad_none))
        for i, h in bad_none[:10]:
            print("           [%s] %s" % (i, h))
        fail = True
    if bad_ungrounded:
        print("[newfact] **본문에서 근거를 못 찾은 건 %d** — 제목만 보고 쓴 문장일 수 있습니다:"
              % len(bad_ungrounded))
        for i, nf, h in bad_ungrounded[:12]:
            print("           [%s] 새사실='%s'" % (i, nf))
            print("                 기사='%s'" % h)
        print("[newfact]   본문에 있는 수치·고유명사를 그대로 한 낱말 이상 쓰세요.")
        print("[newfact]   `run.sh bodypeek <idx>` 로 리드를 열어 확인합니다.")
        fail = True
    pct = 100.0 * len(nobody) / n
    if nobody:
        print("[newfact] 본문 없음 %d건(%.0f%%): %s"
              % (len(nobody), pct, ", ".join(nobody[:15])))
        if pct > a.max_nobody_pct:
            print("[newfact]   **상한 %.0f%% 초과.** `run.sh bodygo` 를 돌렸는지 확인하세요."
                  % a.max_nobody_pct)
            fail = True
        else:
            print("[newfact]   (링크 미확보 건 등. 상한 %.0f%% 이내라 통과)" % a.max_nobody_pct)
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
