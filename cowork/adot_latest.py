# -*- coding: utf-8 -*-
"""adot_latest.py - adot-telegram-fetch 의 out/latest.txt -> 에이닷 tier0 원문  [2026-10-20 신설]

왜: 세 스케줄 작업을 하나로 합치면서(사용자 지시) 에이닷비즈뉴스레터는 슬랙에 올리지 않고
    GitHub 저장소 `hijsh994-maker/adot-telegram-fetch` 의 `out/latest.txt` 를 **바로 tier0 로** 넣는다.
    종전에는 '텔레그램 로그' 작업이 latest.txt 를 슬랙 #dtb-tier0 에 게시하고 DTB 가 그 슬랙
    메시지를 slack-save -> slack_to_log 로 읽었다. 이 스크립트가 그 슬랙 왕복을 대신한다.

입력 (latest.txt):
    DATE=YYYY-MM-DD / SOURCE=… / FETCHED_AT=… / COUNT=N   (헤더 4줄)
    =====MSG=====
    TIME=HH:MM
    <본문 여러 줄>
    =====MSG=====
    …
    =====END=====
    COUNT=0 이면 본문 자리에 NO_MESSAGES.

출력 (cowork/raw/adot-latest-<날짜>.txt):
    [HH:MM]
    <본문>            <- slack_to_log.py 가 '## 텔레그램 - 에이닷비즈뉴스레터' 섹션으로 그대로 옮긴다.
                         본문의 슬랙식 <url|제목> 은 [제목](url) 로, 그 밖은 손대지 않는다.

쓰는 법:
    bash cowork/run.sh adot-latest <latest.txt 경로>
종료코드: 0 정상 / 1 파일·형식 오류 / 2 날짜 불일치 또는 COUNT=0 (저장하지 않는다)
"""
import argparse, datetime, os, re, sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAWDIR = os.path.join(BASE, "cowork", "raw")


def unslack(t):
    """슬랙식 링크는 마크다운으로, 텔레그램식 **굵게** 는 슬랙 경유 때와 같은 *굵게* 로.
    (ingest_tier0.parse_adot 은 '[제목](url)' 끝의 (url) 만 떼고, 토픽 줄은 길이 8 이하면 무시한다 —
     종전 슬랙 경유 로그와 같은 모양을 만들어 채점 이력의 연속성을 지킨다.)"""
    t = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r"[\2](\1)", t)
    t = re.sub(r"<(https?://[^>]+)>", r"\1", t)
    t = re.sub(r"\*\*([^*\n]+)\*\*", r"*\1*", t)
    return t


def parse(text):
    lines = text.splitlines()
    hdr = {}
    for ln in lines[:4]:
        if "=" in ln:
            k, _, v = ln.partition("=")
            hdr[k.strip()] = v.strip()
    msgs, cur, intime = [], None, False
    for ln in lines[4:]:
        if ln.strip() == "=====MSG=====":
            if cur is not None:
                msgs.append(cur)
            cur = {"time": "", "body": []}
            intime = True
            continue
        if ln.strip() == "=====END=====":
            break
        if cur is None:
            continue
        if intime and ln.startswith("TIME="):
            cur["time"] = ln[5:].strip(); intime = False
            continue
        cur["body"].append(ln)
    if cur is not None:
        msgs.append(cur)
    out = []
    for m in msgs:
        body = "\n".join(m["body"]).strip("\n")
        if not body or body.strip() == "NO_MESSAGES":
            continue
        out.append((m["time"], body))
    return hdr, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--file", required=True, help="adot-telegram-fetch 의 out/latest.txt")
    ap.add_argument("--mnt", help="마운트(저장소) 경로 — 그쪽 cowork/raw 에 저장")
    ap.add_argument("--work", help="작업본 경로 — 그쪽 cowork/raw 에도 저장 (run.sh log 가 읽는 쪽)")
    a = ap.parse_args()
    if not os.path.exists(a.file):
        print("[adot-latest] 파일 없음: %s" % a.file); sys.exit(1)
    text = open(a.file, encoding="utf-8", errors="replace").read()
    hdr, msgs = parse(text)
    if "DATE" not in hdr or "COUNT" not in hdr:
        print("[adot-latest] 헤더(DATE/COUNT)를 못 읽었습니다: %s" % a.file); sys.exit(1)
    print("[adot-latest] DATE=%s COUNT=%s FETCHED_AT=%s / 메시지 %d건"
          % (hdr.get("DATE"), hdr.get("COUNT"), hdr.get("FETCHED_AT", "?"), len(msgs)))
    if hdr["DATE"] != a.date:
        print("[adot-latest] [중단] latest.txt 날짜(%s)가 오늘(%s)과 다릅니다 — 수집이 아직 안 됐거나 실패"
              % (hdr["DATE"], a.date)); sys.exit(2)
    if not msgs:
        print("[adot-latest] [중단] 오늘자 메시지가 없습니다(COUNT=%s)" % hdr["COUNT"]); sys.exit(2)
    blocks = []
    for t, body in msgs:
        blocks.append(("[%s]\n" % t if t else "") + unslack(body))
    content = "\n\n".join(blocks) + "\n"
    links = len(re.findall(r"https?://", content))
    roots = [r for r in (a.mnt, a.work) if r] or [BASE]
    targets = []
    for r in roots:
        p = os.path.join(os.path.abspath(r), "cowork", "raw", "adot-latest-%s.txt" % a.date)
        if p not in targets:
            targets.append(p)
    for p in targets:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w", encoding="utf-8").write(content)
        print("[adot-latest] 저장: %s (%d자)" % (p, len(content)))
    print("[adot-latest] 기사 링크: %d건 -> 다음은 bash cowork/run.sh log (슬랙 에이닷 메시지 대신 이 파일을 씁니다)" % links)


if __name__ == "__main__":
    main()
