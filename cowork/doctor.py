#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
이관 점검 — 새 환경에서 무엇이 빠졌는지 한 번에 짚는다.  [2026-09-14 신설]

[왜 만들었나]
  다른 Claude 계정으로 옮길 때 빠지기 쉬운 것이 **코드가 아니라 주변부**다.
  시크릿 파일, 스케줄 작업, MCP 연결, 과거 이력 데이터, 메모리 —
  이 중 하나만 빠져도 파이프라인은 **조용히 나쁘게** 돈다.
  실측 사례: 네이버 키가 없으면 에러 없이 링크 해결률만 무너진다
  (newsclips 10/10 -> 1/10). 그런 것을 미리 잡으려고 만들었다.

[쓰는 법]
  bash cowork/run.sh doctor

[읽는 법]
  [OK]   이상 없음
  [경고] 돌기는 하는데 품질이 떨어진다
  [실패] 이 상태로는 그날 브리프가 잘못 나온다
  종료코드: 실패가 하나라도 있으면 1
"""
import datetime
import glob
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OK, WARN, FAIL = "[OK]  ", "[경고]", "[실패]"
_fail = [0]


def say(level, title, detail=""):
    if level is FAIL:
        _fail[0] += 1
    print("%s %s" % (level, title))
    if detail:
        for line in detail.splitlines():
            print("        %s" % line)


def head(t):
    print("")
    print("== %s" % t)


def n_files(pat):
    return len(glob.glob(os.path.join(BASE, pat)))


def main():
    print("[doctor] 점검 폴더: %s" % BASE)

    # 1) 코드
    head("1. 코드")
    need = ["cowork/run.sh", "refine.py", "brief.py", "collect.py",
            "cowork/timeline.py", "cowork/casebook.py", "cowork/blind_list.py",
            "cowork/rankscore.py", "cowork/send_telegram.py", "cowork/linkcheck.py",
            # [2026-09-15 추가] 발송 게이트 둘. catcheck 가 빠지면 run.sh send 가
            #   카테고리 검사에 실패해 발송이 아예 막힌다(조용히 통과가 아니라 중단).
            "cowork/catcheck.py", "cowork/config_sheet.py",
            # [2026-09-15 저녁] 주체 체급·발송 이력 조회
            "cowork/subject.py", "cowork/hist.py", "cowork/rankgrid.py"]
    miss = [p for p in need if not os.path.exists(os.path.join(BASE, p))]
    if miss:
        say(FAIL, "핵심 스크립트 %d개 없음" % len(miss), "\n".join(miss))
    else:
        say(OK, "핵심 스크립트 %d개 확인" % len(need))

    # 2) 설정
    head("2. 설정")
    for f in ["config/keywords.yaml", "config/sources.yaml", "config/entities.yaml"]:
        p = os.path.join(BASE, f)
        say(OK if os.path.exists(p) else FAIL, f)
    rw = os.path.join(BASE, "config", "rankweights.json")
    if os.path.exists(rw):
        try:
            d = json.load(open(rw, encoding="utf-8"))
            say(OK, "config/rankweights.json (학습일 %d일, n=%s)"
                % (len(d.get("trained_on") or []), d.get("n")))
        except Exception as e:
            say(FAIL, "config/rankweights.json 읽기 실패", str(e))
    else:
        say(WARN, "config/rankweights.json 없음",
            "rank 가 학습 가중치 없이 돕니다. 순위 품질이 떨어집니다.")

    # 3) 시크릿 - 값은 절대 찍지 않는다
    head("3. 시크릿 (값은 찍지 않습니다)")
    sp = os.path.join(BASE, "cowork", "secrets.env")
    if not os.path.exists(sp):
        say(FAIL, "cowork/secrets.env 없음",
            "네이버 키가 없으면 링크 해결률이 조용히 무너지고\n"
            "텔레그램 토큰이 없으면 발송이 안 됩니다.")
    else:
        keys = {}
        for line in open(sp, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            keys[k.strip()] = v.strip()
        for k, why in [("NAVER_CLIENT_ID", "링크 해결"),
                       ("NAVER_CLIENT_SECRET", "링크 해결"),
                       ("TELEGRAM_BOT_TOKEN", "발송"),
                       ("TELEGRAM_DRAFT_CHAT_ID", "초안 채널")]:
            if keys.get(k):
                say(OK, "%s 채워져 있음 (%s)" % (k, why))
            else:
                say(FAIL, "%s 비어 있음 (%s)" % (k, why))
        if keys.get("TELEGRAM_FINAL_CHAT_ID"):
            say(WARN, "TELEGRAM_FINAL_CHAT_ID 에 값이 있음",
                "운영 채널로 잘못 나갈 위험. 자동 실행에서는 비워 두는 것이 안전합니다.")

    # 4) 과거 이력 — 타임라인·중복제거가 이걸 먹는다
    head("4. 과거 이력 데이터")
    t = n_files("truth-*.json")
    b = n_files("out/brief-*.md")
    r = n_files("out/refined-*.json")
    say(OK if t + b >= 10 else WARN, "발송 이력 truth %d개 + brief %d개" % (t, b),
        "" if t + b >= 10 else "타임라인의 '진행 중인 이야기'가 얇아집니다.")
    say(OK if r >= 10 else WARN, "후보 이력 refined %d개" % r,
        "" if r >= 10 else "'며칠째 도는 철 지난 주제'를 못 걸러냅니다(09-14 리벨리온 건).")

    # 5) 판정 보조 문서 — 규칙표 대신 읽는 것들
    head("5. 판정 보조 문서")
    for f, why in [("docs/운영지침-변경이력.md", "정본 지침"),
                   ("docs/사업현황.md", "rank 축3 대조표"),
                   ("docs/사례집.md", "편집자 수정 비교쌍")]:
        p = os.path.join(BASE, f)
        if os.path.exists(p):
            say(OK, "%s (%dKB)" % (f, os.path.getsize(p) // 1024))
        else:
            say(WARN, "%s 없음 - %s" % (f, why))

    # 6) 파이썬 의존성
    head("6. 파이썬 패키지")
    for m, why in [("yaml", "설정 읽기"), ("openpyxl", "판정 엑셀"),
                   ("truststore", "사내망 인증서"), ("holidays", "발행일 창")]:
        try:
            __import__(m)
            say(OK, m)
        except Exception:
            say(WARN if m != "yaml" else FAIL, "%s 없음 (%s)" % (m, why),
                "pip install %s --break-system-packages" % m)

    # 7) 사람이 확인할 것 - 기계가 못 보는 영역
    head("7. 기계가 못 보는 것 — 눈으로 확인")
    print("""        (1) MCP 연결: **Slack** 이 새 계정에 붙어 있는가
            [2026-09-17] **Gmail 은 더 이상 필요 없다.** 옮길 계정에 Gmail
            커넥터가 안 붙어서 tier0 4개 소스를 전부 슬랙으로 옮겼다.
        (2) Slack 채널 접근: #3_트렌드(C04KU1PBFEG) · #dtb-tier0(C0BRP6UPGG6)
        (3) #dtb-tier0 에 **채널 이메일 주소**가 살아 있는가
            (뉴스클립·삼성 메일이 이 주소로 전달돼 파일 첨부로 게시된다)
        (4) 그 주소로 오늘치 '조간 뉴스 클리핑' 이 도착했는가
        (5) 스케줄 작업 'daily-tech-brief' 가 평일 09:50 로 등록됐는가
        (6) **옛 계정의 스케줄 작업을 껐는가** (안 끄면 텔레그램에 두 번 나갑니다)
        (7) 텔레그램 봇이 초안 채널에 아직 들어가 있는가""")

    head("결과")
    if _fail[0]:
        print("[doctor] 실패 %d건. 위의 [실패] 를 먼저 해결하세요." % _fail[0])
        return 1
    print("[doctor] 실패 없음. [경고] 는 돌기는 하지만 품질이 떨어지는 항목입니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
