#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
이관 꾸러미 만들기 — 다른 계정/PC 로 옮길 최소 묶음.  [2026-09-14 신설]

[왜 필요한가]
  연결 폴더는 194MB 인데 실제로 옮겨야 하는 것은 18MB 안팎이다.
  나머지는 (가) 재생성되는 색인 sqlite 34MB (나) 그날치 중간 산출물 133MB 다.
  둘 다 옮겨봐야 새 환경에서 다시 만들어진다.

[무엇을 넣고 무엇을 빼나]
  넣는다  cowork/(코드) · config/ · docs/ · 최상위 *.py · .gitignore · .runenv.py
          truth-*.json (발송 이력 52개)
          out/refined-*.json (후보 이력 - 타임라인이 '철 지난 주제'를 볼 때 쓴다)
          out/brief-*.md · out/verdict-*.csv · out/candidates-blind-*.txt
  뺀다    index/ (재생성) · logs/ · __pycache__ · cowork/raw/
          out/ 의 prompt·ranked·tier2·bodycache·preview (그날치 중간물)
  **안 넣는다 (일부러)**
          cowork/secrets.env — 인증정보. 꾸러미에 섞으면 옮기는 경로가
          늘어나 위험하다. 따로 안전하게 옮긴다.

[쓰는 법]
  bash cowork/run.sh export-pack [대상경로]
  기본 대상은 연결 폴더 **안**의 `_이관-꾸러미` 다(밖에 만들면 PC 에서 안 보인다).
"""
import argparse
import glob
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"raw", "__pycache__", ".git"}
SKIP_FILES = {"secrets.env"}


def log(m):
    print("[export-pack] %s" % m)


def copy_tree(src, dst):
    n = 0
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rel = os.path.relpath(root, src)
        out = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(out, exist_ok=True)
        for f in files:
            if f in SKIP_FILES or f.endswith(".pyc"):
                continue
            try:
                shutil.copyfile(os.path.join(root, f), os.path.join(out, f))
                n += 1
            except Exception as e:
                log("[건너뜀] %s (%s)" % (f, e))
    return n


def copy_glob(pat, dst):
    os.makedirs(dst, exist_ok=True)
    n = 0
    for p in glob.glob(os.path.join(BASE, pat)):
        try:
            shutil.copyfile(p, os.path.join(dst, os.path.basename(p)))
            n += 1
        except Exception as e:
            log("[건너뜀] %s (%s)" % (p, e))
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dest", nargs="?", default=os.path.join(BASE, "_이관-꾸러미-v2"))
    # [2026-09-17] 기본 대상에 버전을 붙였다. 09-15 v1 꾸러미가 남아 있으면
    #   "이미 있고 비어 있지 않다"로 멈춰 헷갈린다. 버전별로 갈라 둔다.
    a = ap.parse_args()
    dest = a.dest
    if os.path.abspath(dest) == os.path.abspath(BASE):
        log("대상이 원본과 같습니다. 중단합니다.")
        return 1
    # [2026-09-14 사고 기록] 처음에는 기존 대상을 rmtree 로 지우고 다시 만들었다.
    #   그런데 **마운트(연결 폴더) 위에서는 삭제가 막혀 있어**(Operation not
    #   permitted) 옛 파일이 그대로 남고 새 파일이 덧씌워졌다. 그 결과 앞선
    #   시도에서 딸려 들어간 `cowork/secrets.env` 가 꾸러미에 남았다.
    #   **지우는 것에 기대지 말고, 이미 있으면 만들지 않는다.**
    if os.path.exists(dest) and os.listdir(dest):
        log("대상 폴더가 이미 있고 비어 있지 않습니다: %s" % dest)
        log("**덮어쓰지 않습니다.** 먼저 지우거나 다른 경로를 주세요.")
        log("  (연결 폴더 안에서는 삭제 권한이 필요할 수 있습니다)")
        return 1
    os.makedirs(dest, exist_ok=True)

    tot = 0
    for d in ["cowork", "config", "docs"]:
        c = copy_tree(os.path.join(BASE, d), os.path.join(dest, d))
        log("%s/ %d개" % (d, c))
        tot += c
    for pat, sub in [("*.py", ""), (".gitignore", ""), (".runenv.py", ""),
                     ("truth-*.json", ""),
                     ("out/refined-*.json", "out"), ("out/brief-*.md", "out"),
                     ("out/verdict-*.csv", "out"),
                     ("out/candidates-blind-*.txt", "out"),
                     ("out/timeline-*.md", "out")]:
        c = copy_glob(pat, os.path.join(dest, sub) if sub else dest)
        if c:
            log("%s %d개" % (pat, c))
        tot += c
    os.makedirs(os.path.join(dest, "inbox"), exist_ok=True)

    size = sum(os.path.getsize(os.path.join(r, f))
               for r, _d, fs in os.walk(dest) for f in fs)
    log("완료: %s" % dest)
    log("파일 %d개 / %.1fMB" % (tot, size / 1024.0 / 1024))
    leak = [os.path.join(r, f) for r, _d, fs in os.walk(dest)
            for f in fs if f in SKIP_FILES]
    if leak:
        log("[경고] 인증정보가 섞였습니다: %s" % ", ".join(leak))
        return 1
    # [2026-09-17 신설] 버전 스탬프. 받는 쪽이 "이게 뭔 물건인가"를 먼저 본다.
    try:
        import datetime
        sys.path.insert(0, os.path.join(BASE, "cowork"))
        from version_check import VERSION, VERSION_DATE
        stamp = [
            "DTB 이관 꾸러미",
            "=" * 40,
            "버전      : %s (%s 확정)" % (VERSION, VERSION_DATE),
            "만든 날짜 : %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "파일 수   : %d개 / %.1fMB" % (tot, size / 1024.0 / 1024),
            "",
            "■ 새 계정에서 할 일 — 순서대로",
            "  1. 이 폴더를 원하는 위치로 복사하고 이름을 'DTB' 로 바꾼다",
            "  2. 옛 폴더의 cowork/secrets.env 를 이 폴더의 같은 자리에 복사한다",
            "     (**꾸러미에 일부러 안 넣었다.** 없으면 링크해결·발송이 죽는다)",
            "  3. Claude 에 폴더를 연결하고 아래를 돌린다",
            "         bash cowork/run.sh version",
            "     'v2 온전합니다' 가 나와야 한다",
            "  4. 그다음 docs/이관-가이드.md 를 STEP 7 부터 따라간다",
            "",
            "■ v2 한 줄 정의",
            "  tier0 4개 소스가 전부 슬랙이고(Gmail 불필요), 판정이 5개 게이트로",
            "  막혀 있고, 사후 채점 없이 판정 단계만으로 품질을 지킨다.",
            "",
            "■ 들어 있는 것 / 없는 것",
            "  있다 : 코드(cowork·최상위 py) · config · docs · 발송이력(truth-*.json)",
            "         후보이력(out/refined-*.json) · 발송본 · 판정(verdict-*.csv)",
            "  없다 : cowork/secrets.env (따로 옮긴다)",
            "         index/ · logs/ · 그날치 중간 산출물 (새 환경에서 재생성)",
        ]
        open(os.path.join(dest, "_꾸러미-정보.txt"), "w",
             encoding="utf-8").write("\n".join(stamp) + "\n")
        log("버전 스탬프: _꾸러미-정보.txt (%s)" % VERSION)
    except Exception as e:
        log("[경고] 버전 스탬프 실패(무시): %s" % str(e)[:60])

    log("cowork/secrets.env 는 **들어 있지 않습니다.** 따로 안전하게 옮기세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
