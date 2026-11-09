#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[2026-11-09 사용자 지시] 실행 체크리스트 — 건너뛴 단계가 보고에 그대로 드러나게 한다 (`run.sh checklist`).

[왜] 11-09 사용자: "발송 거부는 너무 심하고, 우리가 정한 프로세스를 그대로 니가 지켜서 진행만 하면 좋겠어. 왜자꾸 건너뛰는게 생기는거야?"
  건너뛴 단계는 전부 '읽어라·확인해라'처럼 흔적이 안 남는 단계였다. 그래서 단계마다 흔적(파일·게이트 결과·확인 메모)을 대조해
  out/checklist-<날짜>.md 를 만들고, **미완 항목을 알림·보고 첫머리에 그대로 붙인다.** 발송은 막지 않는다.

  bash cowork/run.sh checklist            # 표 생성 + 미완 목록
  bash cowork/run.sh checklist --oneline  # 알림용 한 줄
  확인 메모: out/checklist-notes-<날짜>.md 에 `M1: <무엇을 봤나·결과>` 형식으로 적는다(10자 이상).
"""
import argparse, json, os, re, subprocess, sys

def ex(p): return os.path.exists(p)

def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=120).returncode == 0
    except Exception:
        return False

MANUAL = [
    ("M1", "판정 전 기준 읽기", "docs/판정기준-요약.md 와 사례집 최근 10건을 읽고 오늘 판정에 적용한 사례 번호"),
    ("M2", "'고' 리드 직접 읽기", "'고' 전량 리드를 읽고 헤드라인과 다른 이야기였던 건(없으면 '없음' + 읽은 건수)"),
    ("M3", "linkcheck 의심 건 대조", "의심 건마다 같은 기사인지 눈으로 본 결과(없으면 '없음')"),
    ("M4", "deals 빠진 큰 딜 사유", "빠진 1천억원 이상 딜마다 허용 사유(가·나·다)"),
    ("M5", "독립 검토 불일치 처리", "불일치 건과 반영/유지 결정 요약(없으면 '불일치 0')"),
]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--mnt", required=True)
    ap.add_argument("--oneline", action="store_true")
    a = ap.parse_args(); d = a.date; O = lambda f: os.path.join("out", f)
    py = sys.executable; M = a.mnt
    prompt = O("prompt-%s.txt" % d)
    ext_ok = ex(prompt) and open(prompt, encoding="utf-8").read().count("[링크 미확보 후보]") >= 1
    auto = [
        ("A1", "3단계 슬랙 tier0 저장", ex("cowork/raw/slack-3trend-%s.txt" % d) and ex("cowork/raw/slack-tier0-%s.txt" % d)),
        ("A2", "log·tier0", ex("log-%s.md" % d) and ex(O("tier0-%s.json" % d))),
        ("A3", "NewsClips·삼성 슬랙 주입", ex("cowork/raw/newsclips-mail-%s.txt" % d) and ex("cowork/raw/samsung-mail-%s.txt" % d)),
        ("A4", "tier2·community·buzz", ex(O("tier2-%s.json" % d)) and ex(O("community-%s.json" % d)) and ex(O("buzz-%s.json" % d))),
        ("A5", "brief·extend(링크 미확보 블록)", ext_ok),
        ("A6", "blind·timeline", ex(O("candidates-blind-%s.txt" % d)) and ex(O("timeline-%s.md" % d))),
        ("A7", "pagedate·prejudge(옛 기사·자동 '저'·focus 목록)", ex(O("pagedate-%s.json" % d)) and ex(O("candidates-focus-%s.txt" % d))),
        ("A8", "facts 표", ex(O("facts-%s.csv" % d))),
        ("A9", "'고' 확인표 gocheck 통과", run([py, os.path.join(M, "cowork/go_check.py"), "--date", d, "--mnt", M])),
        ("A10", "hist·bodypeek 기록", ex(O("signals-%s.json" % d)) and ex(O("peeked-%s.json" % d))),
        ("A11", "handrank", ex(O("handrank-%s.csv" % d))),
        ("A12", "배치표 + cats 통과", ex(O("placement-%s.csv" % d)) and run([py, "cowork/catcheck.py", "--date", d])),
        ("A13", "독립 검토 + 불일치 처리 통과", run([py, os.path.join(M, "cowork/indep_review.py"), "--date", d, "--mnt", M])),
        ("A14", "verdict 게이트 통과", run(["bash", os.path.join(M, "cowork/run.sh"), "verdict"])),
        ("A15", "판정 엑셀", ex(O("판정-%s.xlsx" % d))),
        ("A16", "발송 기록", ex(O("sent-%s-draft.json" % d)) or ex(os.path.join(M, "out", "sent-%s-draft.json" % d))),
    ]
    notes = {}
    np_ = O("checklist-notes-%s.md" % d)
    if ex(np_):
        for line in open(np_, encoding="utf-8"):
            m = re.match(r"^\s*(M\d+)\s*:\s*(.+)$", line)
            if m: notes[m.group(1)] = m.group(2).strip()
    # 메모가 '미실시'·'일부'·'미완' 으로 시작하면 한 것이 아니다 — 사실대로 적고 미완으로 둔다
    done = lambda n: len(n) >= 10 and not re.match(r"^(미실시|일부|미완)", n)
    man = [(k, t, h, notes.get(k, ""), done(notes.get(k, ""))) for k, t, h in MANUAL]
    miss = [k + " " + t for k, t, ok in auto if not ok] + [k + " " + t for k, t, h, n, ok in man if not ok]
    total = len(auto) + len(man)
    L = ["# 실행 체크리스트 %s — %d/%d 완료" % (d, total - len(miss), total), "",
         "| | 단계 | 흔적 |", "|---|---|---|"]
    L += ["| %s | %s %s | %s |" % ("✅" if ok else "❌", k, t, "자동 확인") for k, t, ok in auto]
    L += ["| %s | %s %s | %s |" % ("✅" if ok else "❌", k, t, n or ("(메모 없음 — %s)" % h)) for k, t, h, n, ok in man]
    open(O("checklist-%s.md" % d), "w", encoding="utf-8").write("\n".join(L) + "\n")
    line = "체크리스트 %d/%d 완료%s" % (total - len(miss), total, (" / 미완: " + ", ".join(miss)) if miss else "")
    if a.oneline:
        print(line); return 0
    print("\n".join(L)); print(); print("[checklist] " + line)
    return 0

if __name__ == "__main__":
    sys.exit(main())
