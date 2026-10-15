# -*- coding: utf-8 -*-
"""
backtest.py - 사람이 실제 발송한 브리프를 정답지로 받아 자동 채점한다.

## 왜 필요한가
2026-08-01 기준, 개선안을 검증할 깨끗한 시험대가 없었다.
  - 파이프라인이 정상인 날(07-27~31)은 이미 정답을 본 뒤라 오염됐다.
  - 정답을 안 본 날(07-20/21)은 파이프라인이 초기 상태라(tier0 없음, 입력량 1/3)
    가시성이 17~27%밖에 안 나와 '편집 판단'을 재는 시험대가 못 된다.
사후에 맞춰보는 방식으로는 이 문제를 벗어날 수 없다. 그래서 앞으로 나오는
브리프를 자동으로 채점해 쌓는다. 전향 검증만이 오염되지 않는다.

## 무엇을 재는가
두 가지를 나눠서 잰다. 오늘 실측에서 이 둘의 성적이 크게 달랐기 때문이다
(07-31: 파이프라인 가시성 92% vs 최종 선별 일치 50%).

  파이프라인 가시성   사람이 실은 기사가 '후보로 보여지긴 했나'  <- compare.py
  선별 일치          그중 내가 실제로 '골랐나'                 <- 이 스크립트

## 정답지 출처
  https://github.com/hijsh994-maker/slack-trend-backdata/tree/main/brief_final
  파일명 YYYYMMDD.md. 사람이 최종 발송한 브리프가 하루 뒤쯤 올라온다.

사용:
  python3 cowork/backtest.py --date 2026-07-31        # 정답지 받아 채점
  python3 cowork/backtest.py --latest                 # 가장 최근 것 자동 선택
"""
import argparse
import datetime
import json
import os
import re
import sys
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(BASE, "out")
API = ("https://api.github.com/repos/hijsh994-maker/"
       "slack-trend-backdata/contents/brief_final")
RAW = ("https://raw.githubusercontent.com/hijsh994-maker/"
       "slack-trend-backdata/main/brief_final/%s.md")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "TIR"})
    return urllib.request.urlopen(req, timeout=20).read().decode("utf-8")


def available():
    try:
        return sorted(x["name"][:-3] for x in json.loads(fetch(API))
                      if x["name"].endswith(".md"))
    except Exception as e:
        print("[backtest] 목록 조회 실패: %s" % str(e)[:60])
        return []


def parse_truth(md):
    """brief_final 원문에서 기사를 뽑는다.

    [2026-08-12] 제목 줄이 두 형식으로 섞여 온다. 사람이 손으로 편집하면서
    볼드 시작 위치가 흔들린 것이다.
        (A) **1. 제목** (매체, [원문보기](URL))     <- 번호가 볼드 안
        (B) 1**. 제목** (매체, [원문보기](URL))     <- 번호가 볼드 밖
    종전에는 (A)만 잡아 **정답지를 절반 넘게 놓쳤다**. 실측 08-12: 실제 14건
    중 8건만 파싱돼 채점이 통째로 틀린 값 위에서 돌았다. 놓친 6건에는 내가
    실제로 맞힌 기사(IBM·Together, 구글 MAU 추월, A.X K2)가 들어 있어
    점수가 실제보다 낮게 나왔다.
    카테고리 헤더 끝에 'ㅤ'(U+3164)가 붙어 오는 경우도 있어 같이 털어낸다.

    [2026-08-18] 세 번째 형식이 나왔다. 꼬리의 '원문보기'가 **링크 없이
    평문**으로만 오는 줄이다.
        (C) **1. 제목** (매체, 원문보기)            <- URL 자체가 없음
    사람이 텔레그램에서 옮겨 붙일 때 하이퍼링크가 풀린 것으로 보인다.
    URL 을 필수로 요구하던 종전 정규식은 이 줄을 통째로 버려서 **17건짜리
    정답지를 13건으로 읽었다**. 버려진 4건 중 3건은 내가 실제로 실은
    기사여서(韓 1200조 데이터센터, Meta 보험 공백, 삼성 피지컬AI 전담조직)
    일치율이 7/13(54%)로 나왔지만 실제로는 10/17(59%)이었다.
    URL 은 optional 로 두고, 없으면 빈 문자열로 채운다. URL 이 빈 항목은
    URL 대조에서는 무조건 미일치가 되고 제목 기반 사건 대조로만 잡힌다.

    [2026-08-31] 네 번째 형식. **여는 볼드 마커가 통째로 빠진** 줄이다.
        (D) 1. 제목** (매체, [원문보기](URL))       <- 앞의 ** 가 없음
    닫는 '**' 는 남아 있어 눈으로는 티가 안 난다. 실측 08-31: 20건짜리
    정답지를 19건으로 읽었고, 빠진 'KT, 우리은행 AI 챗봇·상담봇 재구축
    착수'는 내가 실제로 URL 까지 맞힌 기사여서 점수가 6/19 로 낮게 나왔다
    (실제 7/20). 여는 마커를 optional 로 바꾼다. 번호 뒤 '.' 와 닫는 '**',
    그리고 꼬리 '(매체, 원문보기)' 가 앵커라 오탐 위험은 낮다.
    """
    out, cat = [], "기타"
    # 번호 부분만 (A)|(B) 두 형식을 허용하고 나머지는 동일하게 처리한다.
    # 꼬리는 (C) 를 위해 링크 없는 '원문보기' 도 받는다.
    # 매체명은 쉼표를 포함할 수 있다(공고문 등). 종전 [^,]+ 는 08-13 의
    # '(과기정통부 공고 제2026-0877호, 2026.8.12, [원문보기](...))' 를 놓쳐
    # 정답지를 13건이 아닌 12건으로 읽었다. 꼬리가 앵커라 greedy 로 둔다.
    ART = re.compile(
        r"\s*(?:\*\*(\d+)\.|(\d+)\*\*\.|(?:\*\*)?(\d+)\.)\s*(.+?)\*\*\s*"
        r"\((.+),\s*(?:\[원문(?:기사)?보기\]\((https?://[^)]+)\)"
        r"|원문(?:기사)?보기)\s*\)")
    for line in md.split("\n"):
        cm = re.match(r"\s*\*\*■\s*(.+?)\*\*[\sㅤ]*$", line) or \
            re.match(r"\s*■\s*(.+?)[\sㅤ]*$", line)
        if cm:
            cat = cm.group(1).strip().rstrip("ㅤ").strip()
            continue
        m = ART.match(line)
        if m:
            out.append({"title": m.group(4).strip(),
                        "outlet": m.group(5).strip(),
                        "url": (m.group(6) or "").strip(), "category": cat})
    nolink = sum(1 for t in out if not t["url"])
    if nolink:
        print("[backtest] [주의] 정답지 %d건 중 %d건이 링크 없는 '원문보기' "
              "형식입니다. URL 대조 불가하니 제목으로 사건 대조하세요."
              % (len(out), nolink))
    return out


def norm_url(u):
    u = (u or "").strip().split("#")[0]
    u = re.sub(r"^https?://", "", u, flags=re.I)
    u = re.sub(r"^www\.", "", u, flags=re.I)
    u = re.sub(r"[?&]utm_[^&]*", "", u)
    return u.rstrip("/&?").lower()


def excluded_days():
    """cowork/truth_exclude.txt - 채점에서 뺄 날짜.

    [2026-08-12] brief_final 저장소에는 Brian 외의 사람이 올린 브리프도 섞인다.
    남이 고른 기사로 채점하면 persona 통계와 편집자 루브릭이 남의 취향으로
    오염되므로, 본인 작성분이 아닌 날은 여기 적어 통째로 건너뛴다.
    """
    p = os.path.join(BASE, "cowork", "truth_exclude.txt")
    days = set()
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.split("#")[0].strip()
            if line:
                days.add(line)
    return days


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--latest", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="제외 목록에 있어도 강제로 채점한다")
    a = ap.parse_args()

    skip = set() if a.force else excluded_days()

    if a.latest:
        av = available()
        if not av:
            sys.exit(1)
        # 제외 날짜는 건너뛰고 그 앞으로 거슬러 올라간다.
        cand = ["%s-%s-%s" % (x[:4], x[4:6], x[6:]) for x in av]
        cand = [d for d in cand if d not in skip]
        if not cand:
            print("[backtest] 받을 수 있는 정답지가 없습니다(전부 제외 목록).")
            sys.exit(2)
        day = cand[-1]
        print("[backtest] 가장 최근 정답지: %s" % day)
    else:
        day = (a.date or datetime.date.today().isoformat())

    if day in skip:
        print("[backtest] %s 는 제외 목록(cowork/truth_exclude.txt)에 있어 "
              "채점하지 않습니다. 강제로 하려면 --force." % day)
        sys.exit(0)

    key = day.replace("-", "")
    try:
        md = fetch(RAW % key)
    except Exception as e:
        print("[backtest] %s 정답지 없음 (%s). 보통 하루 뒤에 올라옵니다."
              % (day, str(e)[:40]))
        sys.exit(2)

    truth = parse_truth(md)
    if not truth:
        print("[backtest] 정답지 파싱 실패. 포맷이 바뀌었을 수 있습니다.")
        sys.exit(1)
    tp = os.path.join(BASE, "truth-%s.json" % day)
    json.dump({"date": day, "items": truth}, open(tp, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    open(os.path.join(BASE, "brief-%s.txt" % day), "w", encoding="utf-8").write(
        "\n".join(x["title"] for x in truth))
    print("[backtest] 정답지 %d건 저장: %s" % (len(truth), tp))

    # --- 선별 일치: 내가 만든 브리프와 대조 ---
    mp = os.path.join(OUTDIR, "brief-%s.md" % day)
    if not os.path.exists(mp):
        print("[backtest] %s 없음 - 파이프라인 가시성만 재려면 compare.py 를 쓰세요." % mp)
        return
    mine = open(mp, encoding="utf-8").read()
    murls = {norm_url(u) for u in re.findall(r"\]\((https?://[^)]+)\)", mine)}
    hit = [t for t in truth if norm_url(t["url"]) in murls]
    miss = [t for t in truth if norm_url(t["url"]) not in murls]

    print()
    print("=== 선별 일치 (URL 기준) %d/%d ===" % (len(hit), len(truth)))
    for t in hit:
        print("  O [%s] %s" % (t["category"], t["title"][:56]))
    print()
    print("=== 불일치 %d건 - 같은 사건을 다른 기사로 실었는지 눈으로 확인 필요 ==="
          % len(miss))
    for t in miss:
        print("  ? [%s] %s" % (t["category"], t["title"][:56]))
        print("      %s" % t["url"][:78])
    print()
    # [2026-08-26] 상위 20건 추림본이 있으면 같은 정답지로 한 번 더 잰다.
    #   30건(텔레그램 발송분)과 20건(채팅 보고분)을 나란히 놓고 봐야
    #   '더 좁게 골랐을 때 무엇을 잃는가' 가 보인다.
    p20 = os.path.join(OUTDIR, "brief20-%s.md" % day)
    if os.path.exists(p20):
        m20 = open(p20, encoding="utf-8").read()
        u20 = {norm_url(u) for u in re.findall(r"\]\((https?://[^)]+)\)", m20)}
        hit20 = [t for t in truth if norm_url(t["url"]) in u20]
        lost = [t for t in hit if norm_url(t["url"]) not in u20]
        print("=== 상위20 선별 일치 (URL 기준) %d/%d ===" % (len(hit20), len(truth)))
        if lost:
            print("--- 30건에는 있었는데 20건에서 뺀 정답지 %d건 ---" % len(lost))
            for t in lost:
                print("  X [%s] %s" % (t["category"], t["title"][:56]))
        else:
            print("  (30->20 에서 잃은 정답지 없음)")
        print()

    print("[backtest] URL 일치는 하한값입니다. 같은 사건을 다른 매체 기사로 실은 경우가")
    print("           많으므로(실측 07-31: URL 1/12 이나 사건 기준 6/12), 위 '?' 항목을")
    print("           내 브리프와 대조해 사건 단위로 다시 세십시오.")


if __name__ == "__main__":
    main()
