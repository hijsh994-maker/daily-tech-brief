#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[2026-09-17 신설] 버전 표식 + 이관 무결성 자가검사.

새 계정에서 꾸러미를 푼 뒤 **가장 먼저** 돌린다.
`docs/버전.md` 에 적힌 v2 구성이 실제로 다 있는지 기계로 확인한다.
사람이 목록을 눈으로 대조하면 반드시 빠뜨린다.

    bash cowork/run.sh version

종료코드 0 = v2 온전 / 1 = 빠진 것 있음
"""
import ast
import glob
import os
import re
import subprocess
import sys

VERSION = "v2"
VERSION_DATE = "2026-09-17"
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --- v2 를 이루는 것. docs/버전.md 와 같은 목록이다.
GATES = {
    "새사실 본문대조": "cowork/newfact_check.py",
    "사례집 무결성": "cowork/casebook.py",
    "어젠다(기자재·물량·동일주체)": "cowork/agenda_gate.py",
    "카테고리": "cowork/catcheck.py",
}
TOOLS = {
    "슬랙 이메일 본문 추출": "cowork/slack_email_body.py",
    "이슈 타임라인": "cowork/timeline.py",
    "과거 발송 이력": "cowork/hist.py",
    "네 축 비교표": "cowork/rankgrid.py",
    "주체 체급": "cowork/subject.py",
    "설정값 한 장": "cowork/config_sheet.py",
    "본문 리드": "cowork/bodypeek.py",
}
DOCS = {
    "운영지침(최우선 정본)": "docs/운영지침-변경이력.md",
    "사례집": "docs/사례집.md",
    "사업현황": "docs/사업현황.md",
    "버전": "docs/버전.md",
    "이관 가이드": "docs/이관-가이드.md",
}
CONFIGS = {
    "채점 사전": "config/keywords.yaml",
    "주체 명단·체급": "config/entities.yaml",
    "순위 가중치(고정)": "config/rankweights.json",
}
# run.sh 에 반드시 있어야 하는 서브커맨드
SUBCMDS = ["sync", "collect", "tier0", "newsclips-slack", "samsung-slack",
           "linkcheck", "tier2", "refine", "links", "body", "brief", "extend",
           "blind", "timeline", "verdict", "agenda", "newfact", "case",
           "bodygo", "bodypeek", "hist", "grid", "cats", "config", "insight",
           "deals", "send", "push", "version", "doctor", "export-pack"]

_fail = [0]
_warn = [0]


def mark(ok, label, detail="", warn_only=False):
    if ok:
        print("  [OK]   %-32s %s" % (label, detail))
        return True
    if warn_only:
        _warn[0] += 1
        print("  [경고] %-32s %s" % (label, detail))
    else:
        _fail[0] += 1
        print("  [실패] %-32s %s" % (label, detail))
    return False


def head(t):
    print("\n== %s" % t)


def exists(rel):
    return os.path.exists(os.path.join(BASE, rel))


def main():
    print("=" * 66)
    print(" DTB 파이프라인 **%s** (%s 확정)" % (VERSION, VERSION_DATE))
    print(" 폴더: %s" % BASE)
    print("=" * 66)
    print(" v2 한 줄 정의 — tier0 4개 소스가 전부 슬랙이고(Gmail 불필요),")
    print("   판정이 5개 게이트로 막혀 있고, 사후 채점 없이 판정만으로 품질을 지킨다.")

    head("1. 판정 게이트 5종 — 이게 v2 의 핵심이다")
    for k, v in GATES.items():
        mark(exists(v), k, v)
    # 게이트가 run.sh 에 실제로 물려 있는가 (파일만 있고 안 불리면 무의미)
    rs = open(os.path.join(BASE, "cowork/run.sh"), encoding="utf-8").read()
    for name, pat in [("verdict 가 newfact 호출", "newfact_check.py"),
                      ("verdict 가 case 호출", "casebook.py --check"),
                      ("verdict 가 agenda 호출", "agenda_gate.py"),
                      ("send 가 cats 호출", "catcheck.py")]:
        mark(pat in rs, name, pat)

    head("2. 판정 보조 도구")
    for k, v in TOOLS.items():
        mark(exists(v), k, v, warn_only=True)

    head("3. 수집 경로 — 전부 슬랙 (Gmail 불필요)")
    mark("newsclips-slack)" in rs, "뉴스클립 슬랙 경로", "run.sh newsclips-slack")
    mark("samsung-slack)" in rs, "삼성 슬랙 경로", "run.sh samsung-slack")
    try:
        nm = open(os.path.join(BASE, "cowork/newsclips_mail.py"), encoding="utf-8").read()
        mark("RE_BARE_URL" in nm, "줄바꿈 URL 대응",
             "없으면 뉴스클립 26건 중 3건이 통째로 탈락한다")
    except Exception as e:
        mark(False, "newsclips_mail.py", str(e)[:40])

    head("4. 정본 문서")
    for k, v in DOCS.items():
        n = 0
        if exists(v):
            n = sum(1 for _ in open(os.path.join(BASE, v), encoding="utf-8"))
        mark(exists(v), k, "%s (%d줄)" % (v, n))

    head("5. 설정 파일")
    for k, v in CONFIGS.items():
        mark(exists(v), k, v)
    # secrets.env 는 꾸러미에 **일부러** 안 들어간다. 없다고 '꾸러미가 깨졌다'고
    #   읽히면 안 되므로 따로 센다(2026-09-17).
    _has_secrets = exists("cowork/secrets.env")
    if _has_secrets:
        mark(True, "인증정보 secrets.env", "cowork/secrets.env")
    else:
        print("  [미완] %-32s %s" % ("인증정보 secrets.env",
              "꾸러미에 **일부러** 없다 → 이관 가이드 STEP 4"))

    head("6. run.sh 서브커맨드")
    have = set(re.findall(r"^([a-z0-9_|-]+)\)", rs, re.M))
    have = {x for grp in have for x in grp.split("|")}
    miss = [c for c in SUBCMDS if c not in have]
    mark(not miss, "필수 %d개" % len(SUBCMDS),
         "빠짐: %s" % ", ".join(miss) if miss else "전부 있음")

    head("7. 과거 이력 (판정 품질에 직접 쓰인다)")
    for label, pat, why in [
            ("발송 이력 truth-*.json", "truth-*.json", "중복 제외·hist 가 쓴다"),
            ("후보 이력 out/refined-*.json", "out/refined-*.json", "timeline 의 '철 지난 주제'"),
            ("발송본 out/brief-*.md", "out/brief-*.md", "hist·backtest"),
            ("판정 out/verdict-*.csv", "out/verdict-*.csv", "판정 재현")]:
        n = len(glob.glob(os.path.join(BASE, pat)))
        mark(n > 0, label, "%d개 — %s" % (n, why), warn_only=(n > 0))

    head("8. 파이썬 구문검사")
    files = glob.glob(os.path.join(BASE, "*.py")) + glob.glob(os.path.join(BASE, "cowork/*.py"))
    bad = []
    for f in files:
        try:
            ast.parse(open(f, encoding="utf-8").read(), f)
        except Exception as e:
            bad.append("%s: %s" % (os.path.basename(f), str(e)[:40]))
    mark(not bad, "%d개 파일" % len(files), "; ".join(bad[:3]) if bad else "오류 없음")

    head("9. 사례집 무결성")
    try:
        r = subprocess.run([sys.executable, "cowork/casebook.py", "--check"],
                           cwd=BASE, capture_output=True, text=True,
                           env=dict(os.environ, DTB_DOCS=os.path.join(BASE, "docs")))
        mark(r.returncode == 0, "casebook --check",
             (r.stdout or r.stderr).strip().splitlines()[-1][:60] if (r.stdout or r.stderr) else "")
    except Exception as e:
        mark(False, "casebook --check", str(e)[:40])

    head("결과")
    if not _fail[0] and not _has_secrets:
        print("[version] **%s 꾸러미는 온전합니다.** (경고 %d건)" % (VERSION, _warn[0]))
        print("[version] 다만 **cowork/secrets.env 가 없습니다.** 꾸러미에 일부러")
        print("          넣지 않은 파일입니다. 옛 폴더에서 따로 복사하세요.")
        print("          → docs/이관-가이드.md **STEP 4**")
        print("[version] 그것 없이는 링크해결(네이버)·텔레그램 발송이 죽습니다.")
        return 1
    if _fail[0]:
        print("[version] **%s 아님 — 실패 %d건.** 위 [실패] 를 해결해야 합니다."
              % (VERSION, _fail[0]))
        print("[version] 대개 꾸러미를 덜 풀었거나 폴더를 잘못 지정한 것입니다.")
        return 1
    print("[version] **%s 온전합니다.** (경고 %d건 — 돌기는 합니다)" % (VERSION, _warn[0]))
    print("[version] 다음: docs/이관-가이드.md 의 2부(새 계정에서 할 일)를 이어서 봅니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
