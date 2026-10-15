# -*- coding: utf-8 -*-
"""
newsclips.py - tier0 소스 'SKT NewsClips' 적재.

## 무엇인가
GitHub slack-trend-backdata 저장소의 logs/YYYY-MM-DD.md 에 매일 올라오는
'종이 신문 지면 스크랩 -> AI 관련만 필터링한 요약' 문서다.
지면 기사라 원문 URL 이 문서에 없고, 출처가 '매체 / 발행일 / 면번호' 로만 적힌다.

## 왜 필요한가 (2026-08-05 실측)
그날 채점에서 놓친 6건 중 4건이 이 문서 안에 있었다.
  #2 엔비디아 맞먹는 구글 HBM 수요 (이데일리)   <- 수집실패
  #3 AX 핵심은 '업무 재설계' 삼성전자 (이데일리)  <- 수집실패
  #6 3대 메가 프로젝트 신속 이행 (세계일보)      <- 수집실패
  #9 카카오 음성 AI, 사투리까지 표현 (매일경제)   <- LLM 선별 탈락
지면 기사는 RSS(tier1)에 늦게 오거나 아예 안 오고, 구글뉴스(tier2)에서도
지면 제목과 웹 제목이 달라 잘 안 걸린다. 사람은 지면을 보고 고르므로
이 소스가 사람 브리프와 가장 가깝다.

## 포맷
상류 요약 프롬프트가 바뀌면 문서 포맷도 바뀐다. 실제로 2026-08-06 에
'### N. [제목] / **핵심 내용**: / **기사 출처**:' 에서
'**N. [제목]** / - 요약 / - 지면 기사 (웹 링크 없음) / 출처: 매체 면번호'
로 바뀌어 파서가 0건을 냈고, 그날 12건이 전량 유실될 뻔했다.
parse() 는 알려진 포맷을 모두 읽는다(아래 RE_HEAD 주석 참조).
새 포맷이 또 나오면 0건 경고가 뜨므로 RE_HEAD/RE_SRC_* 를 늘리면 된다.

## 링크
문서에 URL 이 없으므로 제목+매체로 resolve_link 를 돌려 붙인다.
지면 제목은 실재하는 헤드라인이라 삼성 섹션(요약문만 있음)보다 훨씬 잘 붙는다.
  실측 2026-08-05: 10/10 (100%), 26초.
  [주의] 반드시 run.sh 를 통해 실행할 것. 직접 python3 로 부르면
         cowork/secrets.env 의 네이버 키가 안 실려 1/10 으로 떨어진다.

## 사용
  bash cowork/run.sh newsclips          # tier0 뒤에 실행
"""
import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import samsung_resolve as SR   # noqa: E402

OUTDIR = os.path.join(BASE, "out")
SRC = "newsclips"
LABEL = "SKT NewsClips"
RAW = ("https://raw.githubusercontent.com/hijsh994-maker/"
       "slack-trend-backdata/main/logs/%s.md")

# 지면 스크랩 포맷은 상류(사람이 돌리는 요약 프롬프트)가 바뀌면 같이 바뀐다.
# 파서가 못 읽으면 조용히 0건이 되고 그날 tier0 의 가장 정확한 축이 통째로
# 빠지므로(2026-08-06 실측: 12건 전량 유실), 알려진 포맷을 모두 지원하고
# 어느 것도 안 맞으면 경고를 낸다.
#
# [포맷 A] ~2026-08-05
#   ### 1. [제목]
#    - **핵심 내용**: ...
#    - **기사 출처**: 전자신문 / 2026년 8월 5일 수요일 005면 (박준호 기자, ...)
#
# [포맷 B] 2026-08-06~
#   **1. [제목]**
#    - 본문 요약 문장 ...
#    - 지면 기사 (웹 링크 없음) / 출처: 매일경제 A17면 (동일 내용: 한국경제 A14면)
#
# 공통점은 '번호 + 대괄호 제목' 한 줄과 그 아래 불릿 몇 줄뿐이라, 제목 줄을
# 앵커로 잡고 블록을 자른 뒤 불릿에서 요약·출처를 뽑는 구조로 통일했다.

# 제목 줄. 포맷 A(### 1. [제목]), B(**1. [제목]**), C(1. **제목**)를 한 번에 잡는다.
#   [포맷 C] 2026-08-13~  대괄호가 사라지고 제목이 볼드로만 온다.
#     1. **"AI DC 골든타임…계열사 시너지로 경쟁력 확보"**
#      - 요약 문장
#      - 서울경제, 파이낸셜뉴스        <- '출처:' 라벨 없이 매체명만
RE_HEAD = re.compile(
    r"^\s*(?:#{1,6}\s*|\*\*\s*)?(\d{1,2})\s*[.)]\s*"
    r"(?:\*{0,2}\s*\[(?P<b>.+?)\]\s*\*{0,2}|\*\*(?P<c>.+?)\*\*)\s*$",
    re.M)

# 출처 줄. '**기사 출처**: 전자신문 / ...' 과 '... / 출처: 매일경제 A17면' 둘 다.
RE_SRC_A = re.compile(r"\*\*기사\s*출처\*\*\s*:\s*([^/\n]+?)\s*/\s*([^(\n]*)")
RE_SRC_B = re.compile(r"출처\s*:\s*\*{0,2}\s*([^\s*/(,]+)\s*([A-Za-z]?\d+\s*면)?")
# 포맷 C 의 출처 줄. 라벨이 없어 '짧고 한글 매체명만 있는 불릿'으로 판별한다.
# 예: '- 서울경제, 파이낸셜뉴스' / '- Maeil경제 (매일경제)' / '- 동아일보'
RE_SRC_C = re.compile(
    r"^[-*•]\s*([가-힣A-Za-z][가-힣A-Za-z0-9]{1,9}(?:경제|일보|신문|타임스|뉴스|투데이|TV|미디어)?)"
    r"\s*(?:[,(].*)?$")

# 요약 줄. 포맷 A 는 '**핵심 내용**:' 라벨이 있고, B 는 첫 불릿이 곧 요약이다.
RE_BODY_A = re.compile(r"\*\*핵심\s*내용\*\*\s*:\s*(.+?)(?:\n\s*-|\Z)", re.S)

# 출처 줄로 판정할 단서. 이 단서가 있으면 요약 후보에서 제외한다.
SRC_HINTS = ("출처", "지면 기사", "웹 링크 없음")


def log(m):
    print("[newsclips] %s" % m)


def _clean(s):
    return re.sub(r"\s+", " ", re.sub(r"\*\*", "", s or "")).strip()


def parse(md):
    """지면 스크랩 md -> [{title, outlet, page, summary}]

    포맷 A/B 를 모두 읽는다. 인식된 항목이 없으면 빈 리스트를 돌려주고,
    호출부가 '0건 파싱' 경고를 내도록 둔다.
    """
    lines = md.splitlines()
    heads = []
    for i, line in enumerate(lines):
        m = RE_HEAD.match(line)
        if m:
            t = (m.group("b") or m.group("c") or "").strip()
            # 포맷 C 는 제목이 볼드로만 오므로 '**핵심 내용**:' 같은 라벨 줄이
            # 제목으로 잡히지 않게 거른다.
            if t and not t.endswith(":"):
                heads.append((i, t.strip("*").strip()))
    if not heads:
        # [포맷 D] 2026-08-25~  번호·볼드·대괄호가 모두 사라졌다.
        #   제목 (매체)
        #   원문 링크: (제공되지 않음)
        # 요약이 없고 제목 줄 하나 + '원문 링크:' 줄 하나가 한 항목이다.
        out_d = []
        for n, raw in enumerate(lines):
            s = raw.strip()
            if not s or s.startswith(("#", "-", "*", "•")):
                continue
            if s.startswith("원문 링크"):
                continue
            nxt = lines[n + 1].strip() if n + 1 < len(lines) else ""
            if not nxt.startswith("원문 링크"):
                continue
            md_ = re.match(r"^(?P<t>.+?)\s*\((?P<o>[^()]{2,20})\)\s*$", s)
            if md_:
                out_d.append({"title": _clean(md_.group("t")),
                              "outlet": _clean(md_.group("o")),
                              "page": None, "summary": None})
            else:
                out_d.append({"title": _clean(s), "outlet": None,
                              "page": None, "summary": None})
        return out_d

    out = []
    for n, (i, title) in enumerate(heads):
        end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
        block = "\n".join(lines[i + 1:end])

        outlet = page = summary = None

        # --- 출처 ---
        sm = RE_SRC_A.search(block)
        if sm:                                   # 포맷 A
            outlet, page = _clean(sm.group(1)), _clean(sm.group(2))
        else:                                    # 포맷 B
            sm = RE_SRC_B.search(block)
            if sm:
                outlet = _clean(sm.group(1)).rstrip(",")
                page = _clean(sm.group(2)) or None
            else:                                # 포맷 C: 마지막 불릿이 매체명
                for raw in reversed(block.splitlines()):
                    s = raw.strip()
                    if not s.startswith(("-", "*", "•")):
                        continue
                    cm = RE_SRC_C.match(s)
                    if cm:
                        outlet = _clean(cm.group(1))
                    break

        # --- 요약 ---
        bm = RE_BODY_A.search(block)
        if bm:                                   # 포맷 A
            summary = _clean(bm.group(1))
        else:                                    # 포맷 B: 출처 줄이 아닌 첫 불릿
            for raw in block.splitlines():
                s = raw.strip()
                if not s.startswith(("-", "*", "•")):
                    continue
                s = re.sub(r"^[-*•]\s*", "", s)
                if any(h in s for h in SRC_HINTS):
                    continue
                if s:
                    summary = _clean(s)
                    break

        out.append({"title": title, "outlet": outlet or None,
                    "page": page or None, "summary": summary or None})
    return out


def usable(url):
    """섹션·홈 URL 을 기사 URL 로 오인하지 않게 거른다.

    2026-08-05 실측: 이데일리 건이 'edaily.co.kr/?cm=news_headline'(섹션 홈)
    으로 붙었다. 링크가 있는 것처럼 보여 더 나쁘다 - 사람이 눌러야 안다.
    기사 URL 은 경로나 질의에 숫자 id 가 있다는 점으로 판별한다.
    """
    if not url:
        return False
    tail = url.split("://", 1)[-1]
    path = tail[tail.find("/"):] if "/" in tail else ""
    if not path or path == "/":
        return False
    return bool(re.search(r"\d{4,}", path))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--deadline", type=float, default=32.0,
                    help="링크 해결 제한(초). 넘으면 남은 건 미해결로 둔다.")
    a = ap.parse_args()

    day = datetime.date.fromisoformat(a.date)
    p = os.path.join(OUTDIR, "tier0-%s.json" % day.isoformat())
    if not os.path.exists(p):
        log("%s 없음. tier0 를 먼저 실행" % p)
        return 1
    data = json.load(open(p, encoding="utf-8"))

    url = RAW % day.isoformat()
    try:
        md = urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": "TIR"}),
            timeout=20).read().decode("utf-8")
    except Exception as e:
        # 상류가 아직 안 올라온 날은 조용히 넘어간다(파이프라인을 세우지 않는다).
        log("수신 실패(%s) - 건너뜀: %s" % (str(e)[:40], url))
        return 0

    ents = parse(md)
    if not ents:
        log("0건 파싱. 지면 스크랩 포맷이 바뀌었을 수 있음 - 파서 점검 필요")
        return 0
    log("%s %d건 파싱" % (LABEL, len(ents)))

    have = {x.get("title") for x in data["items"] if x.get("src") == SRC}
    lo, hi = day - datetime.timedelta(days=1), day
    t0, added, linked = time.time(), 0, 0
    for e in ents:
        if e["title"] in have:          # 재실행 시 중복 주입 방지
            continue
        link = None
        if time.time() - t0 < a.deadline:
            try:
                r = SR.resolve_link(e["title"], e["outlet"], lo, hi)
                if r and usable(r.get("url")):
                    link = r
            except Exception:
                link = None
        if link:
            linked += 1
        data["items"].append({
            "src": SRC, "title": e["title"], "outlet": e["outlet"],
            "pub_date": day.isoformat(), "cluster": 1, "topic": None,
            "url": link["url"] if link else None,
            "url_via": "resolve" if link else None,
            "confidence": "auto" if link else "unresolved",
            "seen_in": [SRC], "summary": e["summary"], "page": e["page"],
        })
        added += 1

    data.setdefault("counts", {})[SRC] = len(ents)
    json.dump(data, open(p, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    log("주입 %d건 | 링크 %d (%.0f%%) | %.0f초"
        % (added, linked, 100.0 * linked / max(added, 1), time.time() - t0))
    if added and linked < added:
        log("미해결 %d건은 refine 뒤 'run.sh links' 가 이어서 채운다" % (added - linked))
    return 0


if __name__ == "__main__":
    sys.exit(main())
