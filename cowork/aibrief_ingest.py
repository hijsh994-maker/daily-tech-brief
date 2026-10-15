# -*- coding: utf-8 -*-
"""aibrief_ingest.py — tier0 소스 'aibrief' 적재.

## 무엇인가 (2026-09-09 사용자 지시)
사용자의 **별도 스케줄 작업(매일 08:00 AI 뉴스 큐레이션)** 결과를 DTB 의
tier0 **봇큐레이션** 입력으로 받는다. 원문 지시:
"앞으로 tier0 뉴스 소스에 우리 루틴에 있는 일일 AI 뉴스 브리핑도
 봇큐레이션 소스로 추가해줘." / "매일 8시에 AI 뉴스 큐레이션 실행하는 게
 있어. 그 결과를 가져오면 돼."

## 왜 파일 경로를 두는가
그 8시 작업은 **다른 Claude 스페이스**에서 돈다. 이 스페이스의
`list_scheduled_tasks` 에도, `list_sessions` 에도 잡히지 않아
DTB 실행 중에 직접 읽을 수가 없다. 그래서 **연결 폴더의 `inbox/`** 를
약속된 전달 지점으로 쓴다. 8시 작업이 결과를 거기 떨어뜨리면
DTB 가 4단계에서 집어 간다. 파일이 없으면 조용히 건너뛴다(에러 아님).

## 입력 형식 — 아무 브리핑 서식이나 받는다
제목과 URL 이 한 항목 안에 있기만 하면 된다. 실측 대응 형식:
  1) `- [제목](URL)`                        마크다운 링크 목록
  2) `• **제목** (매체, [원문보기](URL))`    DTB 발송본 서식
  3) `1. 제목 (매체)` + 다음 줄 `링크: URL`  NewsClips 메일 서식
  4) `제목 — URL`                            제목과 URL 이 같은 줄
섹션 헤더(`## …`, `**■ …**`), 빈 줄, 요약 문단은 그냥 무시한다.
**URL 이 없는 줄은 버린다.** 링크 없는 항목을 tier0 에 넣으면
링크 해결 단계(초당 0.4건)만 잡아먹는다.

## 봇큐레이션인 이유
사람이 고른 것이 아니라 에이전트가 웹서치로 고른 목록이다.
`refine.CURATED_AI` 에 넣어 **가산 +1**(사람큐레이션은 +5)만 받는다.
tier0 이므로 잡음 필터는 면제되고 전량 판정 대상에는 들어간다.

## 사용법
    bash cowork/run.sh aibrief                 # inbox/aibrief-<날짜>.md 를 읽음
    bash cowork/run.sh aibrief <<'RAW'         # 또는 붙여넣기
    <브리핑 원문>
    RAW
    bash cowork/run.sh aibrief dry             # 파싱만, 주입 안 함
"""
import argparse
import datetime
import json
import os
import re
import sys
import urllib.parse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(BASE, "out")
SRC = "aibrief"

# 라벨 길이 하한을 1 로 둔다. '원문보기'(4자)를 못 잡아 DTB 서식이
#   통째로 bare-URL 분기로 새던 버그가 있었다(2026-09-09).
#   짧은 라벨은 NOISE_TITLE 이 걸러 낸다.
RE_MD_LINK = re.compile(r"\[([^\]]{1,200})\]\((https?://[^\s)]+)\)")
RE_BARE_URL = re.compile(r"(https?://[^\s)\]>,]+)")
RE_OUTLET = re.compile(r"[（(]\s*([^()（）]{2,30}?)\s*[,，]?\s*[)）]\s*$")
# 섹션 헤더·구분선·머리말은 항목이 아니다
RE_SKIP = re.compile(r"^\s*(#{1,6}\s|[-=*_]{3,}\s*$|\*\*[■▣]|■\s)")
NOISE_TITLE = re.compile(r"^(원문보기|링크|출처|source|read more|자세히)\s*$", re.I)


def unwrap(url):
    """지메일 google.com/url?q= 래핑을 벗긴다."""
    if "google.com/url" in url:
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("q")
        if q:
            url = q[0]
    return url.rstrip(".,;)]")


def clean_title(t):
    t = re.sub(r"\*\*|__|`", "", t)
    t = re.sub(r"^\s*(?:[-*▪▸·•]|\d+[.)]|\d+\\\.)\s*", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t.strip(" -–—:·")


def parse(text):
    """[(title, url, outlet)] 목록. 링크 없는 항목은 버린다."""
    out, seen = [], set()
    lines = text.split("\n")
    for i, raw in enumerate(lines):
        line = raw.rstrip()
        if not line.strip() or RE_SKIP.match(line):
            continue

        title = url = None
        # (1)(2) 마크다운 링크가 있는 줄
        links = RE_MD_LINK.findall(line)
        if links:
            # '원문보기' 같은 라벨 링크면 제목은 줄 앞부분에서 뽑는다
            cands = [(t, u) for t, u in links if not NOISE_TITLE.match(t.strip())]
            if cands:
                title, url = cands[0]
            else:
                # '원문보기' 같은 라벨 링크뿐인 줄. 제목은 링크 앞에서 뽑는다.
                #   실측: DTB 발송본 서식
                #   `• **제목** (매체, [원문보기](URL))`
                #   -> '[' 앞을 그대로 쓰면 '(매체, ' 까지 딸려온다.
                #      볼드 안이 있으면 그것을, 없으면 첫 '(' 앞을 쓴다.
                url = links[0][1]
                head = line.split("[")[0]
                mb = re.search(r"\*\*(.+?)\*\*", head)
                if mb:
                    title = mb.group(1)
                    mo2 = re.search(r"[（(]\s*([^()（）,，]{2,30})", head[mb.end():])
                    if mo2:
                        outlet_hint = mo2.group(1).strip()
                        if outlet_hint:
                            title = title + "\u0000" + outlet_hint
                else:
                    title = re.split(r"[（(]", head)[0]
                title = clean_title(title)
        else:
            m = RE_BARE_URL.search(line)
            if m:
                # (4) 제목과 URL 이 같은 줄
                url = m.group(1)
                head_txt = clean_title(line[:m.start()])
                # '원문보기: URL' / '링크: URL' 처럼 **라벨만 있는 줄**은 버린다.
                #   위 줄에서 제목을 끌어오면 바로 앞 기사의 제목이 붙어
                #   같은 기사가 두 번 들어간다(2026-09-09 실측).
                if NOISE_TITLE.match(head_txt.rstrip(":： ")):
                    continue
                title = head_txt
                if len(title) < 8:
                    # (3) 'o 링크: URL' 형태 -> 제목은 위 줄에서
                    for j in range(i - 1, max(-1, i - 4), -1):
                        cand = clean_title(lines[j])
                        if len(cand) >= 8 and not RE_SKIP.match(lines[j]) \
                                and not RE_BARE_URL.search(lines[j]):
                            title = cand
                            break
        if not url or not title:
            continue
        title = clean_title(title)
        if len(title) < 8:
            continue
        url = unwrap(url)
        if url in seen:
            continue
        seen.add(url)
        outlet = None
        if "\u0000" in title:                       # 위에서 붙인 매체 힌트
            title, outlet = title.split("\u0000", 1)
            title, outlet = title.strip(), outlet.strip()
        else:
            mo = RE_OUTLET.search(title)
            if mo:
                outlet = mo.group(1).strip()
                title = title[:mo.start()].strip()
        out.append((title, url, outlet))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--mnt", default=None, help="연결 폴더 경로(원문 사본 저장)")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    day = a.date

    # 1) stdin 우선, 없으면 inbox 파일
    text = ""
    if not sys.stdin.isatty():
        text = sys.stdin.read()
    origin = "stdin"
    if not text.strip():
        for d in [os.path.join(BASE, "inbox")] + \
                 ([os.path.join(a.mnt, "inbox")] if a.mnt else []):
            p = os.path.join(d, "aibrief-%s.md" % day)
            if os.path.exists(p):
                text = open(p, encoding="utf-8").read()
                origin = p
                break
    if not text.strip():
        print("[aibrief] 입력 없음 - 건너뜁니다.")
        print("[aibrief]   8시 작업 결과를 inbox/aibrief-%s.md 에 저장하거나," % day)
        print("[aibrief]   `run.sh aibrief <<'RAW' ... RAW` 로 붙여넣으세요.")
        return 0

    items = parse(text)
    print("[aibrief] 입력 %s / 파싱 %d건" % (origin, len(items)))
    if not items:
        print("[aibrief] [경고] 링크가 붙은 항목을 하나도 못 찾았습니다."
              " 서식을 확인하세요.")
        return 0
    for t, u, o in items[:5]:
        print("   - %-52s %s" % (t[:50], (o or "?")))

    # 원문 사본 보관 (slack-save 와 같은 관행)
    for d in [os.path.join(BASE, "cowork", "raw")] + \
             ([os.path.join(a.mnt, "cowork", "raw")] if a.mnt else []):
        try:
            os.makedirs(d, exist_ok=True)
            open(os.path.join(d, "aibrief-%s.txt" % day), "w",
                 encoding="utf-8").write(text)
        except Exception:
            pass

    if a.dry:
        print("[aibrief] dry - 주입하지 않았습니다.")
        return 0

    p = os.path.join(OUTDIR, "tier0-%s.json" % day)
    if not os.path.exists(p):
        print("[aibrief] %s 없음. `run.sh tier0` 를 먼저 돌리세요." % p)
        return 1
    data = json.load(open(p, encoding="utf-8"))
    have_url = {x.get("url") for x in data["items"] if x.get("url")}
    have_title = {x.get("title") for x in data["items"]}

    kept, dup = [], 0
    for t, u, o in items:
        if u in have_url or t in have_title:
            dup += 1
            continue
        kept.append({
            "src": SRC, "title": t, "outlet": o, "url": u,
            "pub_date": day, "seen_in": [SRC], "summary": None, "page": None,
        })
    data["items"].extend(kept)
    data.setdefault("counts", {})[SRC] = len(kept)
    json.dump(data, open(p, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("[aibrief] 주입 %d건 (기존 중복 %d건 건너뜀) -> %s"
          % (len(kept), dup, os.path.basename(p)))
    print("[aibrief] 통로: tier0-봇큐레이션 (AI큐레이션+1)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
