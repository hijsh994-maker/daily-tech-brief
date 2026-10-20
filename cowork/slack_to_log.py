# -*- coding: utf-8 -*-
"""
slack_to_log.py - 슬랙 원문 덤프 -> ingest_tier0.py 가 읽는 log-YYYY-MM-DD.md

배경:
  기존에는 별도 루틴이 슬랙/텔레그램을 긁어 GitHub(slack-trend-backdata)에
  로그를 올리고, ingest_tier0.py 가 그걸 다시 내려받았다. Cowork 에서는 Slack
  MCP 로 직접 읽을 수 있으므로 GitHub 왕복이 필요 없다. 대신 파서가 기대하는
  마크다운 포맷으로 변환해 주는 단계가 필요하고, 그게 이 스크립트다.

  Slack MCP 는 bash 에서 호출할 수 없다(에이전트 도구다). 그래서 역할을 나눈다:
    1. 에이전트가 slack_read_channel 결과를 raw/ 아래에 그대로 저장
    2. 이 스크립트가 결정론적으로 변환         <- 사람이 검증 가능한 지점
    3. ingest_tier0.py --file 로 소비

입력 (cowork/raw/):
  slack-3trend-YYYY-MM-DD.txt    #3_트렌드 채널 덤프 (detailed 포맷)
  slack-tier0-YYYY-MM-DD.txt     #dtb-tier0 채널 덤프 (detailed 포맷)
                                 (옛 이름 slack-personal-*.txt 도 읽는다)

출력:
  log-YYYY-MM-DD.md  (프로젝트 루트)

[중요] 출력 포맷은 기존 GitHub 로그와 '바이트 단위로 같을' 필요는 없지만,
  파서가 뽑아내는 결과는 같아야 한다. 특히 에이닷 섹션은 parse_adot 이
  제목 줄 끝의 (url) 만 떼어내므로 '[제목](url)' 을 넣으면 제목에 대괄호가
  남는다. 이는 기존 로그도 마찬가지이며, 채점 이력과의 연속성을 위해
  일부러 그대로 재현한다(고치지 않는다).
"""
import argparse
import datetime
import html
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAWDIR = os.path.join(BASE, "cowork", "raw")

# MCP 덤프의 메시지 구분자
MSG_RE = re.compile(
    r"^=== Message from .*? at (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \w+ ===\s*$",
    re.M)

SEC = {
    "slack":   "## 슬랙 #3_트렌드",
    "samsung": "## 텔레그램 - 삼성 글로벌 AI/SW 뉴스",
    "adot":    "## 텔레그램 - 에이닷비즈뉴스레터",
}

# [2026-08-06 중단 -> 2026-08-22 완전 삭제]
#   curated(#개인테스트 'Daily AI Tech Trends' 트렌드 브리핑)는 상류 게시가
#   불안정해 08-06 에 수집을 껐고, 앞으로도 들어오지 않는다는 사용자 확인에
#   따라 파서까지 전부 지웠다(ingest_tier0.py 의 parse_curated/do_curated,
#   _CUR_ITEM/_CUR_CAT, refine.py 의 CURATED_IMPACT 도 함께).
#   과거 로그의 '## 슬랙 #개인테스트' 섹션은 이제 무시된다.


def unslack(t):
    """슬랙 표기를 마크다운으로. <url|제목> -> [제목](url), <url> -> url."""
    t = re.sub(r"<(https?://[^|>]+)\|([^>]*)>", r"[\2](\1)", t)
    t = re.sub(r"<(https?://[^>]+)>", r"\1", t)
    t = re.sub(r"<@[A-Z0-9]+\|([^>]*)>", r"\1", t)   # 멘션은 표시명만
    t = re.sub(r"<@[A-Z0-9]+>", "", t)
    return html.unescape(t)


def split_messages(raw):
    """덤프를 [(datetime, body)] 로 쪼갠다. 파일 앞부분의 헤더는 버린다."""
    parts = MSG_RE.split(raw)
    out = []
    # parts = [머리말, ts1, body1, ts2, body2, ...]
    for i in range(1, len(parts) - 1, 2):
        ts = datetime.datetime.strptime(parts[i], "%Y-%m-%d %H:%M:%S")
        body = parts[i + 1]
        # 'Message TS: ...' 줄 제거
        body = re.sub(r"^Message TS: \S+\s*$", "", body, flags=re.M)
        # 슬랙 앱 서명 꼬리 제거
        body = re.sub(r"^\*다음을 사용하여 보냄\*.*$", "", body, flags=re.M)
        out.append((ts, body.strip("\n")))
    return out


def classify(body):
    """#dtb-tier0 메시지가 어느 섹션인지 판정."""
    head = body[:200]
    if "삼성 글로벌 AI/SW 뉴스" in head:
        return "samsung"
    if "에이닷비즈뉴스레터" in head:
        return "adot"
    if "(이어서" in head:
        return "adot_cont"
    # 그 밖의 메시지(사업 공고 점검 결과 등)는 기사가 아니므로 버린다.
    return None


def strip_marker(body):
    """'_[텔레그램 로그 ...] 제목_' / '_(이어서 2/3)_' 머리표시를 제거."""
    body = re.sub(r"^_\[텔레그램 로그 \d{4}-\d{2}-\d{2}\][^\n]*_\s*\n?", "", body)
    body = re.sub(r"^_\(이어서[^\n]*\)_\s*\n?", "", body)
    return body.lstrip("\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--out")
    a = ap.parse_args()
    day = (datetime.date.fromisoformat(a.date) if a.date
           else datetime.date.today())
    d = day.isoformat()

    p_trend = os.path.join(RAWDIR, "slack-3trend-%s.txt" % d)
    # [2026-08-22] 에이닷·삼성 입력이 #개인테스트 -> #dtb-tier0 로 옮겨졌다.
    #   새 스템(slack-tier0)을 먼저 보고, 없으면 옛 스템(slack-personal)으로
    #   떨어진다. 과거 날짜를 재처리해도 그대로 돌아간다.
    p_pers = os.path.join(RAWDIR, "slack-tier0-%s.txt" % d)
    if not os.path.exists(p_pers):
        p_pers = os.path.join(RAWDIR, "slack-personal-%s.txt" % d)
    missing = [p for p in (p_trend, p_pers) if not os.path.exists(p)]
    if missing:
        print("[오류] 원문 덤프 없음: %s" % ", ".join(missing))
        print("       에이전트가 slack_read_channel 결과를 먼저 저장해야 합니다.")
        sys.exit(1)

    # --- #3_트렌드 : 당일 메시지 전부를 slack 섹션으로 ---
    trend = [(ts, b) for ts, b in split_messages(open(p_trend, encoding="utf-8").read())
             if ts.date() == day]

    # --- #dtb-tier0 : 삼성 / 에이닷 두 갈래로 분류 ---
    pers = [(ts, b) for ts, b in split_messages(open(p_pers, encoding="utf-8").read())
            if ts.date() == day]
    pers.sort(key=lambda x: x[0])          # 이어서 2/3, 3/3 순서 보장
    buckets = {"samsung": [], "adot": []}
    for ts, b in pers:
        k = classify(b)
        if k == "adot_cont":
            k = "adot"                      # 이어서 조각은 에이닷 본문에 이어붙임
        if k:
            buckets[k].append(strip_marker(b))

    # [2026-10-20] 에이닷은 슬랙을 거치지 않고 adot-telegram-fetch 의 latest.txt 를 바로 쓴다
    #   (run.sh adot-latest 가 cowork/raw/adot-latest-<날짜>.txt 로 떨군다). 그 파일이 있으면
    #   슬랙 #dtb-tier0 의 에이닷 메시지보다 우선한다(같은 내용의 두 경로가 겹치지 않게).
    #   없으면 종전대로 슬랙 메시지를 쓴다 — PC 실행·과거 날짜 재처리는 그대로 돈다.
    p_adot = os.path.join(RAWDIR, "adot-latest-%s.txt" % d)
    adot_src = "slack"
    if os.path.exists(p_adot):
        txt = open(p_adot, encoding="utf-8").read().strip("\n")
        if txt:
            buckets["adot"] = [txt]
            adot_src = "latest.txt"

    out = ["# 3_트렌드 채널 로그 - %s" % d, ""]
    out.append(SEC["slack"])
    for ts, b in sorted(trend, key=lambda x: x[0]):
        out.append(unslack(b))
        out.append("")
    for key in ("samsung", "adot"):
        out.append(SEC[key])
        for b in buckets[key]:
            out.append(unslack(b))
            out.append("")

    path = a.out or os.path.join(BASE, "log-%s.md" % d)
    open(path, "w", encoding="utf-8").write("\n".join(out))
    print("[slack_to_log] 저장: %s (%d자)" % (path, sum(len(x) + 1 for x in out)))
    print("[slack_to_log] 섹션별 메시지: 트렌드 %d / 삼성 %d / 에이닷 %d (출처: %s)"
          % (len(trend), len(buckets["samsung"]), len(buckets["adot"]), adot_src))
    for key in ("samsung", "adot"):
        if not buckets[key]:
            print("[경고] '%s' 섹션이 비었습니다. 상류 게시가 늦었을 수 있습니다."
                  % key)


if __name__ == "__main__":
    main()
