#!/usr/bin/env bash
# run.sh - Cowork 샌드박스용 파이프라인 러너 (단계 분할 실행)
#
# 왜 단계를 나누는가:
#   Cowork bash 호출은 1건당 45초 제한이고, 호출이 끝나면 자식 프로세스도
#   함께 종료된다(백그라운드 실행 불가). 그래서 각 단계를 45초 안에 끝나도록
#   쪼개고, 상태는 작업 디렉터리(/tmp/dtb-<세션이름>)에 남긴다.
#
# 왜 /tmp 로 복사해서 도는가:
#   마운트된 Windows 폴더에서는 SQLite 가 'disk I/O error' 를 낸다(FUSE 잠금
#   미지원). 로컬 디스크에 작업 사본을 두고, 산출물만 마운트로 되돌린다.
#
# 사용:
#   bash cowork/run.sh sync      # 마운트 -> 작업사본
#   bash cowork/run.sh collect   # tier1 RSS 적재
#   bash cowork/run.sh tier0     # GitHub 큐레이션 로그
#   bash cowork/run.sh newsclips # SKT NewsClips(지면 스크랩) tier0 적재
#   bash cowork/run.sh tier2     # 구글뉴스(병렬)
#   bash cowork/run.sh refine    # 병합·클러스터링·채점·링크해결
#   bash cowork/run.sh brief     # LLM 프롬프트 생성
#   bash cowork/run.sh push      # 작업사본 -> 마운트 (산출물 반영)

set -uo pipefail

# MNT - 프로젝트 폴더(= 이 스크립트가 놓인 cowork/ 의 상위).
#   [주의] 마운트 경로의 세션 이름(/sessions/<세션>/mnt/...)은 Cowork 세션마다
#   바뀐다. 여기에 경로를 하드코딩하면 다음 세션에서 sync 의 cp 가 전부
#   조용히 실패하고(2>/dev/null 로 삼켜진다) '[sync] 완료' 만 찍힌 채
#   빈 작업본으로 파이프라인이 돈다. 그래서 스크립트 위치에서 역산하고,
#   그래도 못 찾으면 마운트를 glob 으로 훑는다.
MNT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)"
if [ ! -f "$MNT/refine.py" ]; then
  MNT="$(ls -d /sessions/*/mnt/"Daily Tech Brief" 2>/dev/null | head -1)"
fi
if [ ! -f "${MNT:-}/refine.py" ]; then
  echo "[오류] 프로젝트 폴더를 찾지 못했습니다 (MNT='${MNT:-}')." >&2
  echo "       ls -d /sessions/*/mnt/ 로 마운트 경로를 확인하세요." >&2
  exit 1
fi

# W - 작업 사본. [주의] /tmp 는 세션이 끝나도 남고, 남은 디렉터리는 소유자가
#   달라(nobody, 0700) 읽지도 지우지도 못한다. 실제로 rm/cp 가 전부
#   'Permission denied' 로 막혀 backtest 부터 죽은 적이 있다. 세션별로 가른다.
W="/tmp/dtb-$(basename "$(dirname "$(dirname "$MNT")")")"
DATE="${DATE:-$(TZ=Asia/Seoul date +%F)}"
export PYTHONIOENCODING=utf-8
export TZ=Asia/Seoul

# 네이버 검색 API 키. 링크 해결(naver_api.py)에 쓰인다. 없으면 해결률이 떨어질
# 뿐 파이프라인은 계속 돈다. 키는 cowork/secrets.env 에만 두고 커밋하지 않는다.
#
# [주의] 그냥 `source` 하면 안 된다. 윈도우 메모장으로 만든 파일은 줄끝이 CRLF라
#   값 끝에 캐리지리턴(\r)이 붙고, 그대로 HTTP 헤더에 들어가 인증이 조용히 실패한다.
#   (키가 틀렸다는 메시지도 안 나오고 링크 해결률만 떨어져서 찾기 어렵다.)
#   그래서 한 줄씩 읽어 \r 과 따옴표·공백을 벗겨낸 뒤 export 한다.
# [2026-10-15 클라우드 이관] 비밀키 파일은 코드 저장소에 없다. 저장소 옆에 받은
#   dtb-secrets/secrets.env (클라우드) 를 먼저 찾고, 없으면 종전 경로를 본다.
SECRETS_ENV="$MNT/cowork/secrets.env"
[ -f "$SECRETS_ENV" ] || SECRETS_ENV="$MNT/../dtb-secrets/secrets.env"
if [ -f "$SECRETS_ENV" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"                       # CRLF 제거
    case "$line" in ''|'#'*) continue ;; esac  # 빈 줄·주석 건너뜀
    case "$line" in *=*) ;; *) continue ;; esac
    k="${line%%=*}"; v="${line#*=}"
    # 공백을 먼저 없앤 뒤 따옴표를 벗긴다. 순서를 바꾸면 ` "abc"` 처럼 따옴표
    # 앞에 공백이 있을 때 따옴표가 그대로 값에 남는다.
    k="$(printf '%s' "$k" | tr -d '[:space:]')"
    v="$(printf '%s' "$v" | tr -d '[:space:]')"
    v="${v#\"}"; v="${v%\"}"; v="${v#\'}"; v="${v%\'}"
    [ -n "$k" ] && export "$k=$v"
  done < "$SECRETS_ENV"
fi

# Cowork 는 자체 SQLite 인덱스를 쓴다. 사용자의 윈도우 예약작업(collect.py 45분
# 주기)이 쓰는 index/articles.sqlite 와 같은 파일을 건드리면 서로 덮어쓴다.
COWORK_DB="index/cowork-articles.sqlite"

# ---------------------------------------------------------------------------
# [2026-09-25 신설] 영업일 가드
#
# 왜: 09-24(추석 전날)·09-25(추석)에 파이프라인이 그대로 돌아 초안 채널까지
#   발송됐다. 사용자 지적 — "한국 공휴일에는 안해도 되는데 왜 자꾸 하는거야?"
#   holidays 패키지는 그때도 깔려 있었지만 datewin(발행일 창 확대)에만 쓰였고,
#   '오늘 돌려야 하는 날인가' 를 묻는 곳이 파이프라인 어디에도 없었다.
#   스케줄은 날짜를 가리지 않고 발화하므로, 판단은 여기서 한다.
#
# 규칙: 토·일 또는 한국 법정 공휴일(대체공휴일 포함)이면 sync 와 send 가
#   **종료코드 3** 으로 즉시 멈춘다. 수집·판정·발송 전부 하지 않는다.
#   holidays 패키지가 없으면 주말만 보고 공휴일은 통과시킨다(조용한 오판 방지를
#   위해 경고를 찍는다).
#
# 예외: 지난 날짜를 손으로 되짚을 때만 DTB_FORCE=1 을 붙인다.
#   예) DTB_FORCE=1 DATE=2026-09-22 bash cowork/run.sh sync
# ---------------------------------------------------------------------------
dtb_daykind() {
  python3 - "$1" <<'PYDAY'
import sys, datetime
d = datetime.date.fromisoformat(sys.argv[1])
if d.weekday() >= 5:
    print("HOLIDAY|%s" % ("토요일" if d.weekday() == 5 else "일요일")); raise SystemExit
try:
    import holidays
except ImportError:
    print("UNKNOWN|holidays 패키지 없음(주말만 확인함)"); raise SystemExit
kr = holidays.KR(years=d.year, language="ko")
if d not in kr:
    print("WORKDAY|평일"); raise SystemExit
name = kr[d]
print("HOLIDAY|%s" % name)
PYDAY
}

dtb_require_workday() {
  local res kind label
  res="$(dtb_daykind "$DATE")"
  kind="${res%%|*}"; label="${res#*|}"
  if [ "$kind" = "UNKNOWN" ]; then
    echo "[영업일] 경고: $label — 공휴일 판정을 못 했습니다. 날짜를 직접 확인하세요."
    return 0
  fi
  [ "$kind" = "WORKDAY" ] && return 0
  if [ "${DTB_FORCE:-}" = "1" ]; then
    echo "[영업일] $DATE 은 휴일($label)이지만 DTB_FORCE=1 이라 계속합니다."
    return 0
  fi
  echo ""
  echo "=============================================================="
  echo " ■ $DATE 은 영업일이 아닙니다 — $label"
  echo ""
  echo "   Daily Tech Brief 는 한국 영업일에만 돕니다."
  echo "   수집·판정·발송을 하지 않고 여기서 끝냅니다. (2026-09-25 사용자 지시)"
  echo ""
  echo "   할 일: 아무것도 하지 마세요. 다음 영업일에 스케줄이 다시 돕니다."
  echo "          보고가 필요하면 '휴일이라 실행하지 않았다' 한 줄이면 됩니다."
  echo "          지난 날짜를 되짚는 경우에만 DTB_FORCE=1 을 붙이세요."
  echo "=============================================================="
  echo ""
  return 3
}

step="${1:-}"

case "$step" in

workday)
  # 오늘(또는 DATE=)이 영업일인지만 알려준다. 종료코드 0 평일 / 3 휴일.
  if dtb_require_workday; then
    [ "${DTB_FORCE:-}" = "1" ] || echo "[영업일] $DATE 은 영업일입니다. 파이프라인을 돌립니다."
  else
    exit 3
  fi
  ;;

sync)
  dtb_require_workday || exit 3
  mkdir -p "$W"
  # [2026-08-18] datewin 의 공휴일 보정은 holidays 패키지가 있어야
  #   음력(설·추석·부처님오신날)과 대체공휴일을 맞게 계산한다. 없으면
  #   양력 고정일 표로만 판정해 연휴 다음날 창이 조용히 좁아진다.
  #   샌드박스는 세션마다 초기화되므로 sync 에서 한 번 확인한다.
  python3 -c "import holidays" 2>/dev/null || {
    echo "[sync] holidays 패키지 설치"
    pip install holidays --break-system-packages -q 2>&1 | tail -1
  }
  # 코드·설정은 항상 마운트 기준으로 최신화
  rm -rf "$W"/*.py "$W"/config "$W"/cowork "$W"/docs
  cp "$MNT"/*.py "$W"/ 2>/dev/null
  # [2026-09-22] docs 추가. 판정 프롬프트가 docs/사례집.md 를 읽어 I 절을 만든다.
  #   그 전에는 사례집이 작업본에 없어 프롬프트에 한 줄도 안 들어갔다.
  cp -r "$MNT/config" "$MNT/cowork" "$MNT/docs" "$W"/ 2>/dev/null
  # compare.py 가 쓰는 정답지(truth-*.json)와 과거 브리프 원문
  cp "$MNT"/truth-*.json "$MNT"/brief-*.txt "$W"/ 2>/dev/null
  mkdir -p "$W/out" "$W/logs" "$W/index"
  # 산출물·로그는 마운트 것을 가져와 이어쓴다.
  # [-u 필수] 그냥 cp 하면 파이프라인 도중 sync 를 다시 돌렸을 때 작업본의 최신
  #   결과가 마운트의 예전 파일로 덮여 사라진다. 실제로 verify 로 오매칭을 걷어낸
  #   tier0 가 통째로 되돌아간 적이 있다. 더 새 파일만 가져온다.
  cp -u "$MNT/out/"* "$W/out/" 2>/dev/null
  cp -u "$MNT/logs/"* "$W/logs/" 2>/dev/null
  cp -u "$MNT"/log-*.md "$W"/ 2>/dev/null
  # Cowork 전용 DB 만 가져온다. 없으면 사용자 DB 를 1회 복제해 씨앗으로 쓴다.
  if [ -f "$MNT/$COWORK_DB" ]; then
    cp "$MNT/$COWORK_DB" "$W/index/articles.sqlite"
    echo "[sync] Cowork DB 복원"
  elif [ -f "$MNT/index/articles.sqlite" ]; then
    cp "$MNT/index/articles.sqlite" "$W/index/articles.sqlite"
    echo "[sync] 사용자 DB 를 씨앗으로 복제(최초 1회)"
  else
    echo "[sync] DB 없음. collect 가 새로 만든다"
  fi
  rm -rf "$W/__pycache__"
  # [2026-09-21] 직전 실행에서 사용자가 뺀 건을 기록했는지 여기서 확인한다.
  #   09-18 에 기록을 빼먹어 09-21 에 같은 기사 3건이 후보로 되살아났다.
  python3 "$MNT/cowork/repeat_gate.py" --audit --mnt "$MNT" || true
  echo "[sync] 완료 DATE=$DATE"
  # [2026-09-22] 스케줄 프롬프트 교체가 기기 승인에 막혀 낡은 채로 남아 있다.
  #   프롬프트 첫 문단과 09-18 블록은 아직 '텔레그램으로 보내지 않는다' 라고 적혀 있다.
  #   그 지시는 **폐기됐다.** 1단계에서 반드시 지나가는 이 자리에 배너로 못박는다.
  echo ""
  echo "=============================================================="
  echo " ★ 2026-09-22 개정 — 자동 발송 복귀 (프롬프트보다 이 배너가 우선)"
  echo "   사용자 지시: '내일부터는 판정한 결과로 텔레그램 발송까지해. 내가 검토 없어'"
  echo "   - 9단계에서 send dry -> verdict-xlsx -> push -> **run.sh send** 까지 간다."
  echo "   - 사용자 확인을 기다리지 않는다. send 잠금은 제거됐다."
  echo "   - 발송 채널은 **초안 = DTB 업로드(2026-10-07 전환)**. send final(운영)은 절대 쓰지 말 것."
  echo "   - 게이트는 전부 돌리되 발송을 막지 않는다. 실패하면 이름·사유를"
  echo "     10단계 보고와 PushNotification 에 반드시 적을 것."
  echo " ★ 2026-09-23 개정 — 반향(버즈) 통로 신설 (임원 피드백)
   - 순서: tier2-done -> **run.sh community** -> **run.sh buzz** -> refine
     (둘 다 45초 안. 빠뜨리면 refine 이 조용히 예전처럼 돈다.)
   - blind 맨 위 '=== 반향후보 ===' 와 '=== 커뮤니티 ===' 그룹, [반향: …] 표시.
   - 판정은 E축 예외 (f) 커뮤니티 반향. 사유에 '반향:<무엇이 반응했나>'.
   ※ 프롬프트의 '판정 엑셀까지 만들고 멈춘다'(09-18)와 발송 잠금(09-21)은"
  echo "     폐기됐다. 절차서는 docs/실행지침-DTB.md, 정본은 docs/운영지침-변경이력.md"
  echo " ★ 2026-10-08 신설 — 소스 정리 섀도 실험 (~10-22, 사용자 지시)"
  echo "   - send 뒤에 **run.sh shadow** 를 돌린다(5초). 실제 발송에는 영향 없음."
  echo "   - RSS 전체 + 구글 쿼리 9개를 뺀 후보를 따로 만들어 그날 발송본·'고'가 남는지 대조."
  echo "   - 10단계 보고에 shadow 한 줄. 사라진 건이 있으면 헤드라인을 적을 것."
  echo "   - 10-22 실행에서는 run.sh shadow-report 로 2주 결과를 보고·알림(제외 확정 여부는 사용자 결정)."
  echo " ★ 2026-09-30 개정 — '고' 기준 완화 (사용자: '고 선정 기준이 너무 까다로운 건 아닌지')"
  echo "   - 투자·자금조달은 '추진'이어도 새 숫자(규모·가치·주도 투자자)면 고."
  echo "   - E축 예외 (g) 에이전트 실행 인프라(MCP·커넥터) / G축: 같은 주체라도 앵글이 다르면 새 사건."
  echo "   - '고' 12건 미만이면 run.sh verdict 가 2차 검토를 요구(사유에 '2차검토:')."
  echo "   - 신규성·중복 감점은 실제 발송분·사용자 제외분으로만. 후보로 여러 번 본 것은 근거 아님"
  echo "     (run.sh novelty 가 검사, 사유에 '발송근거:<MM-DD 발송 헤드라인>')."
  echo "=============================================================="
  echo ""
  ;;

collect)
  # [주의] 이 단계는 45초 안에 안 끝난다. 피드 28개를 도는 데 실측 87초다
  #   (08-21: 10:09:39 시작 -> 10:11:06 종료). 재개형이 아니라서 중간에
  #   죽으면 그 회차 작업이 통째로 날아간다. **timeout 120 으로 호출할 것.**
  #   (08-21 에 timeout 43 으로 불러 43초를 버리고 처음부터 다시 돌렸다.)
  cd "$W" && python3 collect.py 2>&1 | tail -6 ;;

slack-save)
  # [2026-08-21 신설] Slack MCP 응답을 히어독으로 받아 raw 파일로 저장한다.
  #   종전에는 에이전트가 응답을 눈으로 읽고 14KB 텍스트를 손으로 다시 써서
  #   마운트에 Write 하고, 작업본에 cp 하고, log 를 돌렸다(3~4 호출, 2분 20초).
  #   여기서 이스케이프 해제 + 양쪽 저장 + 건수 점검을 한 번에 한다.
  #   URL 이스케이프(\/)를 손으로 되돌리다 빠뜨려 채널이 통째로 사라지던
  #   08-12 사고를 구조적으로 막는 것이 본래 목적이다.
  #
  #   사용법:  bash cowork/run.sh slack-save 3trend <<'RAW'
  #            ...MCP 응답 원문 붙여넣기...
  #            RAW
  #   별칭: 3trend(#3_트렌드) | tier0(#dtb-tier0)
  #   [2026-08-22] 에이닷·삼성·사업공고가 #개인테스트 -> #dtb-tier0 로 옮겨졌다.
  #     옛 별칭 'personal' 도 계속 받지만 저장 스템은 slack-tier0 이다.
  python3 "$MNT/cowork/slack_save.py" --channel "${2:?3trend|tier0}" \
          --date "$DATE" --mnt "$MNT" --work "$W" ;;

wait-until)
  # [2026-10-20 신설] 통합 스케줄 작업의 시각 맞추기. KST HH:MM 까지 잔다.
  #     bash cowork/run.sh wait-until 09:30     (Bash timeout 600000 으로 부른다)
  #   한 번에 최대 540초만 자고, 더 남았으면 종료코드 10 으로 돌아온다 -> 같은 명령을 다시 부른다.
  #   이미 지난 시각이면 바로 0 으로 끝난다(늦게 시작한 날은 기다리지 않고 바로 다음 단계).
  python3 - "${2:?HH:MM}" <<'EOF'
import sys, time, datetime
from zoneinfo import ZoneInfo
hh, mm = map(int, sys.argv[1].split(":"))
now = datetime.datetime.now(ZoneInfo("Asia/Seoul"))
target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
left = (target - now).total_seconds()
if left <= 0:
    print("[wait-until] %s KST 는 이미 지났습니다(지금 %s). 바로 진행" % (sys.argv[1], now.strftime("%H:%M:%S"))); sys.exit(0)
chunk = min(left, 540)
print("[wait-until] 지금 %s KST, %s 까지 %d초 남음 -> %d초 잠" % (now.strftime("%H:%M:%S"), sys.argv[1], left, chunk), flush=True)
time.sleep(chunk)
if left - chunk > 1:
    print("[wait-until] 아직 %d초 남음 -> 같은 명령을 다시 부르세요" % (left - chunk)); sys.exit(10)
print("[wait-until] %s KST 도달" % sys.argv[1])
EOF
  ;;

adot-latest)
  # [2026-10-20 신설] 에이닷비즈뉴스레터를 슬랙을 거치지 않고 바로 tier0 로 넣는다.
  #   세 스케줄 작업(국책과제·텔레그램 로그·DTB)을 하나로 합치면서, 에이닷은
  #   [10-22] 입력 파일은 run.sh adot-bot 이 만든다(종전 adot-telegram-fetch/out/latest.txt 와 같은 형식).
  #     bash cowork/run.sh adot-latest "$HOME/adot-$DATE/out/latest.txt"
  #   -> cowork/raw/adot-latest-<날짜>.txt (작업본·저장소 양쪽). 이어서 run.sh log 가
  #      슬랙의 에이닷 메시지 대신 이 파일로 '## 텔레그램 - 에이닷비즈뉴스레터' 를 채운다.
  #   종료코드 2 = latest.txt 가 오늘자가 아니거나 COUNT=0 (저장 안 함. 수집이 늦은 것 —
  #   git pull 로 다시 받아 재시도. 끝내 안 되면 종전대로 슬랙 메시지로 폴백된다).
  python3 "$MNT/cowork/adot_latest.py" --date "$DATE" --file "${2:?latest.txt 경로}" \
          --mnt "$MNT" --work "$W" ;;

adot-bot)
  # [2026-10-22 신설] 에이닷비즈뉴스레터를 텔레그램 Bot API 로 직접 받아 adot-latest 에 넘긴다.
  #   전제: 채널 관리자가 전용 봇을 채널 관리자로 추가 + secrets.env 에 TELEGRAM_ADOT_BOT_TOKEN.
  #   종료코드는 adot-latest 와 같다(0 투입 완료 / 2 오늘자 없음 / 1 오류).
  #   [2026-10-26] 뉴스레터는 봇(@adotbiz_news_bot)이 올려 Bot API 로 안 보인다 -> adot_botapi.py 가
  #     cowork/adot_embed.py 로 t.me/dailytechbrief/<번호>?embed=1 을 훑어 보탠다(기준 번호 cowork/raw/adot-lastid.txt).
  #     단독 점검: python3 cowork/adot_embed.py [--date YYYY-MM-DD]
  mkdir -p "$W/out"
  F="$W/out/adot-botapi-$DATE.txt"
  python3 "$MNT/cowork/adot_botapi.py" "$F"; rc=$?
  [ $rc -eq 0 ] || exit $rc
  python3 "$MNT/cowork/adot_latest.py" --date "$DATE" --file "$F" --mnt "$MNT" --work "$W" ;;

samsung-tme)
  # [2026-10-26 신설] ② 삼성 글로벌 AI/SW 텔레그램 — 다이제스트 ■ 항목 + 개별 뉴스 메시지(다이제스트 외)를 함께 뽑는다.
  #   사용자: "다이제스트 말고 다른 메세지에도 뉴스들이 있다 … 같이 가져오면 좋겠다, 링크는 니가 찾아야해"
  #   결과: cowork/raw/samsung-tme-<날짜>.md(.json). 종료코드 0 있음 / 2 둘 다 없음 / 1 받기 실패.
  #     bash cowork/run.sh samsung-tme [--date YYYY-MM-DD]
  shift; python3 "$MNT/cowork/samsung_tme.py" --date "$DATE" "$@" ;;

rfp-fetch)
  # [2026-10-22 신설] 국책과제 ① 수집 — 과기정통부·IITP·IRIS 공고(목록·본문·첨부 텍스트)를 이 서버에서 직접 받는다.
  #   사용자 지시: GitHub Actions(ai-rfp-fetch) 없이, 다른 작업 결과를 읽지 말고 직접 확인할 것.
  #   결과: $MNT/out/rfp/latest/{meta.json,index.md,items/*.md}. 기업마당은 접속이 끊겨 WebFetch 로 따로 본다.
  #     bash cowork/run.sh rfp-fetch [--days N] [--sites msit,iitp,iris,bizinfo]     # timeout 175000
  shift; python3 "$MNT/cowork/rfp_fetch.py" "$@" ;;

log)
  # 에이전트가 cowork/raw/ 에 저장한 슬랙 원문을 tier0 로그 포맷으로 변환.
  #   [2026-10-20] cowork/raw/adot-latest-<날짜>.txt 가 있으면 에이닷 섹션은 그 파일이 우선.
  cd "$W" && python3 cowork/slack_to_log.py --date "$DATE" 2>&1 | tail -8 ;;

tier0)
  # 슬랙에서 만든 로컬 로그가 있으면 그걸 쓰고, 없으면 기존 GitHub 로그로 폴백.
  #
  # [2026-08-03] --sections 에서 samsung 을 뺀다.
  #   do_samsung 의 기계 역추적(discover)이 25초 이상을 먹어 tier0 전체가
  #   45초 제한을 넘겨 죽었다(실측: samsung 포함 timeout / 제외 20초).
  #   삼성 섹션은 어차피 바로 다음 단계 samsung-dump/search/apply 에서 LLM 이
  #   다시 매칭하므로(정확도도 그쪽이 높다) 여기서 도는 건 순수 중복 작업이다.
  #   요약문만 자리를 잡아두면 되므로 samsung-dump 가 알아서 채운다.
  #
  # [2026-08-06] --sections 에서 curated 도 뺀다.
  #   curated 는 #개인테스트의 'Daily AI Tech Trends' 트렌드 브리핑이었는데
  #   상류 게시가 불안정해 실제로는 빈 섹션 경고만 남기는 날이 잦았다.
  #   더 이상 수집하지 않는다(slack_to_log.py 도 같이 막았다).
  cd "$W"
  SECS="slack,adot"
  if [ -f "$W/log-$DATE.md" ]; then
    echo "[tier0] 로컬 로그 사용 (슬랙 직접 수집) / 섹션=$SECS"
    python3 ingest_tier0.py --date "$DATE" --window 1d --sections "$SECS" \
            --file "$W/log-$DATE.md" 2>&1 | tail -8
  else
    echo "[tier0] 로컬 로그 없음 -> GitHub 로그로 폴백 / 섹션=$SECS"
    python3 ingest_tier0.py --date "$DATE" --window 1d --sections "$SECS" 2>&1 | tail -8
  fi
  # 삼성 요약문을 링크 없는 껍데기로 미리 넣어둔다(다음 단계에서 LLM 이 채움).
  python3 cowork/samsung_seed.py --date "$DATE" --file "$W/log-$DATE.md" 2>&1 | tail -2 ;;

newsclips-slack)
  # [2026-09-17 신설] 뉴스클립을 **슬랙 채널의 이메일 게시**로 받는 경로.
  #   슬랙 이메일은 본문이 메시지가 아니라 text/html 파일로 온다.
  #   slack_read_file <file_id> 응답을 그대로 파이프하면 body-plain 을
  #   꺼내 기존 지메일 파서(newsclips_mail.py)로 넘긴다. 파서는 하나다.
  DRY=""
  [ "${2:-}" = "dry" ] && DRY="--dry"
  cd "$W" && python3 cowork/slack_email_body.py \
    | python3 cowork/newsclips_mail.py --date "$DATE" --mnt "$MNT" $DRY ;;

newsclips-mail)
  # [2026-09-17] **이 경로는 폴백이다.** 정본은 `newsclips-slack` 이다.
  #   옮길 계정에 Gmail 커넥터가 붙지 않아 뉴스클립을 슬랙 채널 이메일로
  #   받기로 했다(사용자 지시). 지메일이 살아 있는 계정에서만 쓴다.
  # [2026-08-25 신설] tier0 소스 'SKT NewsClips' 를 **지메일**에서 적재한다.
  #   지메일 「News Clips PDF 분석(N월 N일)」 본문을 히어독으로 넘긴다.
  #   GitHub 문서(구 newsclips)와 달리 **원문 URL 이 함께 온다**
  #   (실측 08-25: 27건 전건 링크 보유 vs GitHub 15건 중 5건).
  #   조간 전체가 오므로 AI 무관 기사·사설은 스크립트가 걸러내고,
  #   제외한 건을 전부 찍어 사람이 확인할 수 있게 한다.
  #
  #   사용법:  bash cowork/run.sh newsclips-mail <<'RAW'
  #            ...지메일 본문 원문 붙여넣기...
  #            RAW
  #   미리보기(주입 없이 파싱만): bash cowork/run.sh newsclips-mail dry <<'RAW'
  DRY=""
  [ "${2:-}" = "dry" ] && DRY="--dry"
  cd "$W" && python3 cowork/newsclips_mail.py --date "$DATE" --mnt "$MNT" $DRY ;;

aibrief)
  # [2026-09-09 사용자 지시] tier0 봇큐레이션 소스 추가.
  #   '앞으로 tier0 뉴스 소스에 우리 루틴에 있는 일일 AI 뉴스 브리핑도
  #    봇큐레이션 소스로 추가해줘.' (매일 08:00 별도 스케줄 작업)
  #   그 작업은 **다른 스페이스**에서 돌아 여기서 직접 못 읽는다.
  #   전달 지점은 연결 폴더의 inbox/aibrief-<날짜>.md 다.
  #   파일이 없으면 조용히 건너뛴다(에러 아님).
  #
  #   사용법:  bash cowork/run.sh aibrief              # inbox 파일 읽기
  #            bash cowork/run.sh aibrief <<'RAW'      # 또는 붙여넣기
  #            ...브리핑 원문...
  #            RAW
  #            bash cowork/run.sh aibrief dry          # 파싱만
  #   **tier0 를 먼저 돌린 뒤에** 부른다(tier0-<날짜>.json 에 덧붙이므로).
  DRY=""
  [ "${2:-}" = "dry" ] && DRY="--dry"
  cd "$W" && python3 cowork/aibrief_ingest.py --date "$DATE" --mnt "$MNT" $DRY ;;

linkcheck)
  # [2026-08-26 신설] 붙어 있는 링크가 '그 기사'가 맞는지 검사한다.
  #   tier0 네 소스가 모두 링크를 갖고 들어오게 되면서 links 단계 부하는
  #   줄었지만, **받은 링크를 아무도 안 본다**는 구멍이 생겼다.
  #   8단계 검증은 '후보 목록 안에 있는 URL 인가'만 보고 살아 있는지는 안 본다.
  #
  #   URL 모양(목록/홈 페이지) + HTTP 404 + 제목 대조 세 가지를 본다.
  #   403 은 봇 차단이라 정상 취급하고, 제목을 못 읽으면 '미확인' 이다.
  #
  #   사용법:  bash cowork/run.sh linkcheck         # 브리프 링크(8단계)
  #            bash cowork/run.sh linkcheck tier0   # tier0 링크(5단계 뒤)
  #   결과는 out/linkcheck-<날짜>.json 에 남는다.
  #   tier0 검사는 '실패' 판정 URL 을 비운다(--fix). 뒤의 links 가 다시 찾는다.
  #
  #   [2026-08-27] 종료코드가 셋으로 늘었다.
  #     0 = 정상 / 1 = 고쳐야 할 링크 있음 / **2 = 검사 커버리지 미달**
  #   2 는 '링크가 나쁘다'가 아니라 '검사를 못 했다'는 뜻이다. 그냥 넘기지
  #   말고 **한 번 더 실행**한다. 판정 난 건은 캐시를 쓰고 못 받은 건만
  #   다시 조회하므로 두 번째는 빠르다. 두 번 돌려도 2 가 나오면 보고에 적는다.
  #   (08-26 사고: 브리프 30건 중 22건이 '미확인' 인데 실패 0 이라 그대로
  #    발송됐다. 사이트가 막은 게 아니라 TIMEOUT 8/WORKERS 8 조합이
  #    이 샌드박스의 동시성 지연 절벽 위에 있어 전부 시간초과된 것이었다.)
  SRC_ARG="${2:-brief}"
  FIX=""
  [ "$SRC_ARG" = "tier0" ] && FIX="--fix"
  cd "$W" || exit 1
  python3 cowork/linkcheck.py --date "$DATE" --source "$SRC_ARG" $FIX
  LC_RC=$?
  # 커버리지 미달(2)이면 여기서 한 번 자동 재시도한다. 사람/에이전트가
  # 재실행을 기억하는 데 기대지 않는다 - 링크 검사는 마지막 관문이라
  # 조용히 넘어가면 안 된다. 캐시 덕에 두 번째는 못 받은 건만 본다.
  if [ "$LC_RC" = "2" ]; then
    echo "[linkcheck] 커버리지 미달 - 자동 재시도 1회"
    sleep 3
    python3 cowork/linkcheck.py --date "$DATE" --source "$SRC_ARG" $FIX
    LC_RC=$?
    [ "$LC_RC" = "2" ] && echo "[linkcheck] [경고] 재시도 후에도 커버리지 미달입니다. 보고에 적으세요"
  fi
  # [2026-08-27] 브리프 검사 뒤에는 곧바로 제목을 맞춘다.
  #   별도 단계로 두면 작업 지시서에 없어서 안 돌 위험이 있다. 링크 검사가
  #   방금 받아온 제목을 그 자리에서 쓰는 것이라 순서상으로도 여기가 맞다.
  #   (tier0 검사 때는 돌리지 않는다. 그때는 브리프가 아직 없다.)
  #
  #   [2026-08-27] portalize 를 retitle **앞에** 넣는다. 순서가 중요하다:
  #     linkcheck(링크가 살아있나) -> portalize(포털 정적링크로 교체)
  #     -> retitle(최종 URL 의 제목으로 헤드라인 정렬)
  #   포털 제목이 원문보다 깨끗해서(매체 꼬리표가 없다) retitle 품질도 오른다.
  #   네이버 쿼터 탓에 30건에 약 75초 걸린다. timeout_ms 는 175000 그대로.
  if [ "$SRC_ARG" = "brief" ]; then
    python3 cowork/portalize.py --date "$DATE"
    python3 cowork/retitle.py --date "$DATE"
  fi
  exit $LC_RC ;;

samsung-slack)
  # [2026-09-15 사용자 지시] **삼성 뉴스는 지메일이 아니라 슬랙 #dtb-tier0 로 온다.**
  #   원문: "앞으로 삼성 뉴스는 지메일로 안오고 slack dtb-tier0 채널을 통해
  #   들어올거니까 참고해!"
  #
  #   형식이 종전 슬랙(■ 요약만, 링크 없음)과 다르다. 지금은 지메일과 **같은**
  #   `번호. 제목 (매체)` + `o 링크:` 형식이라 **링크가 100% 붙어 온다.**
  #   그래서 파서를 새로 만들지 않고 지메일 파서(newsclips_mail.py)를 그대로 쓴다.
  #   -> 5단계 삼성 링크 매칭(정확도 40~68%)은 계속 필요 없다.
  #
  #   실행 순서: log -> tier0 -> newsclips-mail -> **samsung-slack** -> linkcheck tier0
  #   (종전 samsung-mail 자리를 대신한다. 지메일이 다시 오면 그때만 samsung-mail)
  # [2026-09-17] **폴백.** 정본은 `samsung-slack`(09-15 전환). Gmail 없는
  #   계정에서는 쓸 수 없다.
  DRY=""
  [ "${2:-}" = "dry" ] && DRY="--dry"
  cd "$W" && sed -n '/^## 텔레그램 - 삼성 글로벌 AI\/SW 뉴스/,/^## /p' "log-$DATE.md" \
    | python3 cowork/newsclips_mail.py --date "$DATE" --mnt "$MNT" \
             --src samsung $DRY ;;

samsung-mail)
  # [2026-08-26 신설] tier0 소스 '삼성 큐레이션' 을 **지메일**에서 적재한다.
  #   지메일 「[글로벌 AI/SW 뉴스 브리핑] YYYY-MM-DD」 본문을 히어독으로 넘긴다.
  #   찾는 법: subject:("글로벌 AI/SW 뉴스 브리핑") newer_than:2d
  #   하루 두 통이 온다. **「[국내 매체 최우선 반영]」 판본을 쓴다**
  #   (나중에 오고 국내 매체 링크가 더 많다. 브리프의 국문 우선 방침과 맞다).
  #
  #   왜 바꿨나: 슬랙 #dtb-tier0 의 삼성 섹션은 **요약만** 와서 매일
  #   samsung-dump/search/apply 로 링크를 되짚어야 했고 정확도가 40~68%였다.
  #   지메일은 제목·매체·원문 URL 을 함께 준다(실측 08-26: 17건 전건 링크 보유).
  #   **이 분기를 쓰면 5단계 삼성 링크 매칭이 통째로 필요 없다.**
  #
  #   사용법:  bash cowork/run.sh samsung-mail <<'RAW'
  #            ...지메일 본문 원문 붙여넣기...
  #            RAW
  #   미리보기(주입 없이 파싱만): bash cowork/run.sh samsung-mail dry <<'RAW'
  DRY=""
  [ "${2:-}" = "dry" ] && DRY="--dry"
  cd "$W" && python3 cowork/newsclips_mail.py --date "$DATE" --mnt "$MNT" \
             --src samsung $DRY ;;

newsclips)
  # [2026-08-05] tier0 소스 'SKT NewsClips' (구 GitHub 경로).
  #   [2026-08-25] 지메일 소스로 교체됐다. 이 분기는 과거 날짜 재처리용으로만
  #   남긴다. 평시에는 newsclips-mail 을 쓸 것.
  #   GitHub logs/YYYY-MM-DD.md 는 원문 링크가 없어 제목+매체로 되짚어야 하고
  #   확보율이 낮다(실측 08-25: 15건 중 5건).
  #   [주의] 반드시 이 경로로 실행할 것. python3 직접 실행은 secrets.env 의
  #          네이버 키가 안 실려 해결률이 1/10 으로 떨어진다.
  cd "$W" && python3 cowork/newsclips.py --date "$DATE" \
             --deadline "${2:-32}" 2>&1 | tail -5 ;;

# --- 삼성 섹션 링크 매칭 (LLM 경로. 기본으로 이걸 쓴다) -------------------
# 삼성 텔레그램은 원문 링크 없이 요약만 온다. 기계 역추적은 질의도 판정도 약해
# 실측 정확도가 낮았다(07-30 40% / 07-31 63%). LLM 이 질의를 만들고 후보를 고른다.
#   dump -> (LLM 이 질의 작성) -> search -> (LLM 이 선택) -> apply
samsung-dump)   cd "$W" && python3 cowork/samsung_match.py --date "$DATE" --dump ;;
samsung-search) cd "$W" && python3 cowork/samsung_match.py --date "$DATE" --search "${2:?queries.json 경로 필요}" ;;
samsung-show)   cd "$W" && python3 cowork/samsung_match.py --date "$DATE" --show ;;
samsung-apply)  cd "$W" && python3 cowork/samsung_match.py --date "$DATE" --apply "${2:?picks.json 경로 필요}" ;;

# --- 간이 경로(폴백) ------------------------------------------------------
# LLM 매칭을 돌릴 여유가 없을 때, 기계가 붙인 매칭 중 틀린 것만 걷어낸다.
# 새로 찾아주지는 않으므로 재현율은 그대로고 오매칭만 제거된다.
samsung-list)
  cd "$W" && python3 cowork/verify_samsung.py --date "$DATE" --list ;;
verify)
  cd "$W" && python3 cowork/verify_samsung.py --date "$DATE" --reject "${2:-}" ;;

tier2)
  # [2026-08-03] 재개형 러너로 교체.
  #   tier2_par(89쿼리, workers=8)는 60~90초가 걸려 45초 제한 안에 못 끝낸다.
  #   workers 를 20 으로 올려도 마찬가지였고, 백그라운드로 돌려도 호출이 끝나면
  #   프로세스가 함께 죽는다(--die-with-parent). 그래서 캐시에 누적하며
  #   여러 번 나눠 돈다. 한 번 호출당 최대 38초를 쓴다.
  #   호출자가 한 번만 불러도 되도록 여기서 자체 루프를 돌지 않고,
  #   '남은 쿼리 수'를 찍어 재실행 필요 여부를 알린다.
  #
  # [2026-08-24] deadline 36 -> 120. 위 '45초 제한'은 틀린 전제였다.
  #   호스트 상한은 약 178초다(links 분기 주석에 실측 근거).
  #   실측 08-24: 36초 마감으로 97쿼리를 도는 데 5회 + finalize 1회를 불러
  #   2분 41초가 걸렸다. 그중 왕복 오버헤드가 48초다. 120초면 2회로 끝난다.
  #   **timeout_ms 175000 으로 부를 것.**
  # 구글뉴스 when: 파라미터도 발행일 창과 맞춘다. 창만 3일로 넓히고 when:1d 로
  # 긁으면 구글이 24시간치만 주므로 넓힌 의미가 없다(2026-08-03).
  cd "$W"
  GW="$(python3 -c "import sys,datetime,yaml;sys.path.insert(0,'.');import datewin as DW;print(str(DW.window_days(yaml.safe_load(open('config/keywords.yaml',encoding='utf-8'))['policy'],datetime.date.fromisoformat('$DATE')))+'d')")"
  echo "[tier2] 구글뉴스 when=$GW"
  python3 cowork/tier2_resume.py --date "$DATE" --window "$GW" \
          --workers 8 --deadline "${2:-120}" 2>&1 | tail -4 ;;

tier2-done)
  # 캐시가 다 찼을 때 out/tier2-DATE.json 생성 (tier2_par 과 동일 스키마)
  cd "$W" && python3 cowork/tier2_resume.py --date "$DATE" --finalize 2>&1 | tail -4 ;;

community)
  # [2026-09-23 신설, 임원 피드백] HN·GitHub·Hugging Face 커뮤니티 원천.
  #   "타입세이프AI·오픈클로처럼 새로운 반향을 일으키는 이슈를 빠르게 캐치하고 싶다."
  #   둘 다 매체 RSS(tier1)에는 0건이었고 국내 매체 첫 기사는 D+7 이었다.
  #   커뮤니티는 D+1 에 뜬다. tier0 급(src=community)으로 refine 컷에 고정된다.
  #   키 없이 된다. **tier2-done 뒤, buzz 앞에** 돌린다. 실측 20초 안팎.
  #     bash cowork/run.sh community        # out/community-<날짜>.json
  #     bash cowork/run.sh community dry    # 저장 없이 미리보기
  #   작업본(cowork 는 sync 가 복사)에서 돌려 out/ 이 작업본에 남게 한다. push 로 마운트 반영.
  cd "$W" && python3 cowork/community.py --date "$DATE" ${2:+--dry} ;;

buzz)
  # [2026-09-23 신설, 임원 피드백] 반향(버즈) 감지. 30일간 없던 낱말이 여러 매체에
  #   번지면서 제3자 반응(돌풍·열광·오픈소스 구현 잇따라·HN·GitHub)이 붙었는가.
  #   실측 09-21~23: '제브'가 tier2 에 사흘간 8건 들어왔는데 사전에 없는 이름이라
  #   점수 미달로 판정에 도달조차 못했다. 백테스트: 09-21 2위·09-22 1위·09-23 1위.
  #   산출물 out/buzz-<날짜>.json 을 refine 이 읽어 +3·컷 고정, blind 가 맨 위에 올린다.
  #   **community 뒤, refine 앞에** 돌린다. 실측 25초. 과거 날짜를 주면 백테스트.
  #     bash cowork/run.sh buzz             # 오늘
  #     bash cowork/run.sh buzz 2026-09-21  # 그날 파일로 재현(저장 안 함)
  if [ -n "${2:-}" ]; then
    cd "$W" && python3 cowork/buzz.py --date "$2" --dry
  else
    cd "$W" && python3 cowork/buzz.py --date "$DATE"
  fi ;;

refine)
  # [max-items 160 -> 300, link-workers 8 : 2026-07-31]
  #   링크 해결을 병렬화하기 전에는 160 에서도 40초 안팎이라 상향이 불가능했다.
  #   병렬화 후 300 이 29초에 끝난다(45초 제한 안). 컷을 넓히는 이유는
  #   실측상 사람 브리프 손실의 27%가 '링크없음'인데, 그중 대부분이 컷에 걸려
  #   링크 해결 단계에 도달조차 못 했기 때문이다.
  #   workers 는 8을 쓴다. 16 이면 순차 대비 링크를 잃는다(네이버 동시 호출 한계).
  # [2026-10-20 클라우드 분기] 마운트가 git 저장소(=클라우드 세션)면 링크 해결을
  #   refine 안에서 하지 않는다. 실측 10-20: 클라우드 프록시에서 openapi.naver.com
  #   연결이 20회 끊겨 refine 이 175초 상한에서 죽었고(3분 손실), --no-links 로
  #   다시 돌린 뒤 run.sh links 를 5회 돌려 복구했다. 링크는 어차피 재개형
  #   links 러너가 전부 다시 시도하므로 클라우드에서는 처음부터 분리한다.
  #   PC(연결 폴더)에서는 종전과 같다. 강제: DTB_REFINE_LINKS=1 (클라우드에서도 포함)
  #   / DTB_REFINE_NO_LINKS=1 (PC에서도 분리).
  NOLINKS=""
  if [ -n "${DTB_REFINE_NO_LINKS:-}" ]; then NOLINKS="--no-links"
  elif [ -z "${DTB_REFINE_LINKS:-}" ] && [ -d "$MNT/.git" ]; then NOLINKS="--no-links"; fi
  if [ -n "$NOLINKS" ]; then
    echo "[refine] 클라우드 모드: 링크 해결을 생략합니다 -> 이어서 bash cowork/run.sh links 를 '전부 시도 완료'까지 반복하세요"
  fi
  cd "$W" && python3 refine.py --date "$DATE" --min-score 6 \
            --max-items 300 --max-rescue 150 --link-workers 8 $NOLINKS 2>&1 | tail -4 ;;

links)
  # [2026-08-04] 링크 해결을 refine 에서 떼어낸 재개형 러너.
  #   실측: 1건당 1.2~1.6초(성공)/4.8초(실패). 하루 140건이면 워커 12개로도
  #   45초를 넘긴다. refine 이 타임아웃 나서 python3 refine.py 를 직접 부르면
  #   secrets.env 로딩이 빠져 키 없이 돌고, 그러면 0초에 전량 실패한다.
  #   그래서 refine 은 --no-links 로 돌리고 링크는 여기서 나눠 채운다.
  #   '남았습니다'가 안 나올 때까지 반복 실행할 것.
  #
  # [2026-08-21] workers 4 -> 8, deadline 24 -> 34.
  #   [왜] resolve_links.py 의 기본값이 workers=4 로 남아 있었다. 같은
  #     파이프라인의 refine(--link-workers 8)·body(--workers 8)는 이미 8을
  #     쓰고 있었으므로 4는 안전 마진이 아니라 갱신 누락으로 보인다.
  #     실측 08-21: 워커 4 / 마감 25초에서 회당 12~16건만 처리해 387건을
  #     채우는 데 30회(16분 30초)가 걸렸다. 이 한 단계가 그날 작업 43분의 38%.
  #   [상한] 8을 넘기지 말 것. refine 주석의 실측대로 16이면 네이버 동시
  #     호출 한계에 걸려 순차 대비 링크를 오히려 잃는다.
  #
  # [2026-08-24] deadline 34 -> 120. **위 08-21 주석의 '45초 상한'은 틀렸다.**
  #   [무엇이 틀렸나] 종전 주석은 "MCP bash 호출 상한이 45초다. 실행 34초 +
  #     왕복 8초 = 42초로 여유 3초"였다. 실측해 보니 호스트 상한은 45초가
  #     아니라 **약 178초**다(3분짜리 명령이 177,999ms 에서 잘렸다).
  #   [그래서 무슨 손해였나] 실측 08-24: 34초 마감으로 365건을 채우느라
  #     20회를 불렀다. 호출당 왕복 오버헤드 8초 x 20회 = 2분 40초가 포장비로
  #     나갔다. 게다가 34초 실행 + 8초 왕복 = 42초가 45초에 아슬아슬해서
  #     **4회가 결과 반환 직전에 잘려 그 회차가 통째로 날아갔다**(2분 30초).
  #     둘을 합쳐 5분이 순수 낭비였다.
  #   [주의] 마감을 올려도 **일의 총량은 안 줄어든다.** 네이버 오픈API 의
  #     초당 호출 제한이 진짜 천장이라 처리량은 초당 0.33~0.47건으로 고정이다
  #     (실측 08-24). 365건이면 어떻게 부르든 13분어치다. 이 변경이 줄이는
  #     것은 왕복 오버헤드와 잘림 손실뿐이다.
  #   [호출 방법] 이 단계는 **timeout_ms 를 175000 으로 주고 부를 것.**
  #     45초로 부르면 120초짜리가 매번 잘려 도리어 느려진다.
  #   기대치: 회당 55~70건 / 6회 안팎 / 9분.
  #
  # [2026-08-24] 세마포어·타임아웃은 건드리지 말 것 (실측으로 기각).
  #   naver_api._SEM / websearch._SEM 이 3으로 묶여 있어 워커 8이 실효
  #   동시성 3으로 눌린다. 그래서 6으로 올려 봤더니 24건 처리가 68초 ->
  #   150초 초과로 **더 느려졌다**(429 + 백오프). TIMEOUT 15 -> 6 도
  #   느린 성공을 실패로 바꿔 재시도를 부르므로 마찬가지로 더 느렸다.
  #   단건 API 응답은 1.0~1.6초로 정상이다. 병목은 우리 코드가 아니라 쿼터다.
  cd "$W" && python3 cowork/resolve_links.py --date "$DATE" \
             --workers "${3:-8}" --deadline "${2:-120}" ;;

retitle)
  # [2026-08-27 신설] 브리프 헤드라인을 '링크가 가리키는 실제 기사 제목'으로
  #   맞춘다. 지면(NewsClips) 제목이 지면 PDF 에서 딴 것이라 웹 기사 제목과
  #   자주 다르다(실측 08-26: '내년 800조원+ a' vs 실제 '2027년 800조원+α').
  #   같은 기사인데 사람이 링크를 열면 다른 제목이 떠서 헷갈린다.
  #   **linkcheck 뒤, push 앞에 돌린다**(제목을 받아 놔야 하기 때문).
  #   미리보기: bash cowork/run.sh retitle dry
  RT=""
  [ "${2:-}" = "dry" ] && RT="--dry"
  cd "$W" && python3 cowork/retitle.py --date "$DATE" $RT ;;

body-audit)
  # [2026-08-27] body 단계가 값을 하는지 누적으로 잰다. 파이프라인과 무관하며
  #   언제 돌려도 된다. 첫 측정(6일): 가점 75건 -> 정답지 3건(4.0%).
  #   표본이 얇아 폐지 판단은 보류했다. 채점일이 쌓이면 다시 볼 것.
  cd "$W" && python3 cowork/body_audit.py "${@:2}" ;;
body)
  # [2026-08-08] 링크가 확보된 후보의 본문을 받아 관심 주제 신호를 재채점한다.
  #   [왜] refine 의 채점은 title+summary 만 보는데, 실측상 후보의 96.9%가
  #     summary 가 비어 있다. 사실상 제목 한 줄로만 점수를 매겨 왔다.
  #     '경북도 가상공장 플랫폼'은 본문에 디지털트윈이 2번 나오는데
  #     제목에 없어 파이프라인이 영영 못 봤고, 사람은 그 기사를 실었다.
  #   가점만 주고 감점은 주지 않는다(본문의 스쳐 지나가는 언급 때문).
  #   links 를 다 채운 뒤, brief 앞에 돌린다.
  #   '남았습니다'가 안 나올 때까지 반복 실행할 것(회당 232건/26초 실측).
  #
  # [2026-08-24] deadline 30 -> 120. links 와 같은 이유다(위 주석 참조).
  #   호스트 상한이 45초가 아니라 178초이므로 30초씩 끊을 이유가 없었다.
  #   실측 08-24: 30초 마감으로 15회를 불러 6분이 걸렸다. 그중 왕복
  #   오버헤드가 2분이다. 120초면 4회 안팎으로 끝난다.
  #   **timeout_ms 175000 으로 부를 것.**
  cd "$W" && python3 cowork/body_rescore.py --date "$DATE" \
             --deadline "${2:-120}" ;;

bodygo)
  # [2026-09-03 신설] 판정이 끝난 뒤 '고' 전량의 본문을 받는다.
  #   [왜] run.sh body 는 **기계 점수 상위 300건**을 받는데, 고/중/저 판정은
  #     09-01 부터 점수를 보지 않고 한다. 두 집합이 설계상 어긋나서 실측
  #     09-03 에 '고' 30건 중 본문이 있던 건 9건뿐이었다. 순위를 매기기 전에
  #     리드를 확인하려면 '고' 쪽을 따로 받아야 한다.
  #   [언제] `run.sh verdict` 가 종료코드 0 을 낸 뒤, 순위(rank)를 매기기 전.
  #   [그다음] `run.sh bodypeek <idx> ...` 로 리드 700자를 꺼내 본다.
  #   '고' 30건 기준 1분 안팎이면 끝난다(워커 16 · 타임아웃 25초).
  #   링크 미확보 후보(N 접두)는 URL 이 없어 자동으로 빠진다.
  #   [2026-09-22 추가] 본문을 받은 뒤 **'고' 전량의 리드를 자동으로 찍는다.**
  #     받아 놓고 안 읽으면 받은 의미가 없다. 09-22 에 '고' 13건 중 리드를
  #     연 것은 0건이었다(사용자 적발). 출력은 out/peeked-<날짜>.json 에
  #     기록되고 run.sh rank 가 이를 검사해 안 본 건이 있으면 멈춘다.
  cd "$W" && python3 cowork/body_rescore.py --date "$DATE" \
             --from-verdict --deadline "${2:-150}" \
    && python3 cowork/bodypeek.py --date "$DATE" --go ;;

persona)
  # 정답지 누적을 그날 후보와 대조해 '이 사람의 선별 기준'을 뽑는다.
  #   out/persona.json 을 갱신하면 다음 brief 부터 프롬프트에 반영된다.
  #   정답지가 새로 올라온 날(backtest 뒤)에 돌리면 된다. 안 돌려도
  #   파이프라인은 그대로 동작한다(직전 결과를 계속 쓴다).
  cd "$W" && python3 cowork/persona.py --days "${2:-40}" 2>&1 | tail -5 ;;

brief)
  # [2026-08-03] brief 단독 실행 금지. 반드시 extend 까지 묶어 돈다.
  #   brief.py 는 프롬프트를 '새로 쓰기' 때문에, 양식 수정 등으로 brief 만
  #   다시 돌리면 extend 가 붙여둔 [링크 미확보 후보] 섹션(150건)이 통째로
  #   사라진다. 08-03 에 실제로 발생했고 검증 단계에서야 발견했다.
  #   그 섹션이 없으면 사람 브리프의 29%가 LLM 에게 아예 안 보인다.
  cd "$W" && python3 brief.py --date "$DATE" --target 30 2>&1 | tail -4 \
    && python3 cowork/prompt_extend.py --date "$DATE" --top "${2:-150}" 2>&1 | tail -12 ;;

brief-only)
  # extend 없이 프롬프트만 다시 만들고 싶을 때(디버깅용). 평소엔 쓰지 말 것.
  cd "$W" && python3 brief.py --date "$DATE" --target 30 2>&1 | tail -4
  echo "[경고] extend 를 돌리지 않았습니다. [링크 미확보 후보] 섹션이 없습니다." ;;

extend)
  # 링크가 없어 brief.py 가 버린 고득점 후보를 프롬프트에 되살린다.
  # 실측상 사람 브리프 기사의 29% 가 이 사유로 사라지고 있었다.
  # top 은 refine 의 --max-nolink(150)와 맞춘다. 이 숫자를 줄이면 측정상
  # '노출됨'으로 잡히는 항목이 실제로는 프롬프트에 없게 되어 지표가 부풀려진다.
  cd "$W" && python3 cowork/prompt_extend.py --date "$DATE" --top "${2:-150}" ;;

blind)
  # [2026-09-01] 점수를 가린 후보 전수 판정 목록을 만든다.
  #   기계는 553건을 '뽑는' 역할만 하고, 고/중/저 판정은 점수 없이 한다.
  cd "$W" && python3 cowork/blind_list.py --date "$DATE" ;;

version)
  # [2026-09-17 신설] 이 꾸러미가 어느 버전인지 + 무결성 자가검사.
  #   새 계정에서 처음 열었을 때 "제대로 받은 물건인가"를 여기서 본다.
  cd "$MNT" && python3 cowork/version_check.py "${@:2}" ;;

doctor)
  # [2026-09-14 신설] 이관 점검. 다른 계정/PC 로 옮긴 뒤 처음 한 번 돌린다.
  #   시크릿·과거이력·판정문서·MCP 등 '코드 밖'이 빠졌는지 짚는다.
  #   실패가 있으면 종료코드 1.
  #   **작업본이 아니라 마운트(연결 폴더)를 본다.** 이관 대상은 실제 폴더다.
  cd "$MNT" && python3 cowork/doctor.py ;;

export-pack)
  # [2026-09-14 신설] 이관용 최소 꾸러미. 194MB -> 18MB.
  #   index/(재생성 sqlite)와 그날치 중간 산출물을 뺀다.
  #   **secrets.env 는 일부러 넣지 않는다** - 따로 안전하게 옮긴다.
  #   사용법: bash cowork/run.sh export-pack [대상경로]
  cd "$MNT" && python3 cowork/export_pack.py ${2:+"$2"} ;;

timeline)
  # [2026-09-14 신설] 이슈 타임라인. **blind 뒤, 판정 앞에 반드시 돌린다.**
  #   사용자 지적: "단발성 규칙으로는 또 중요하지 않은 기사를 중요하게
  #   판단할 수도 있다. 사람은 규칙으로 기사를 결정하는 게 아니다."
  #   09-14 에 편집자가 뺀 4건 중 3건이 "이 사업이 지금 어디까지 왔는지"를
  #   알아야만 내릴 수 있는 판단이었다. 그 상태값을 만들어 주는 단계다.
  #   출력 out/timeline-<날짜>.md 를 **판정 전에 먼저 읽는다.**
  #     앞부분 = 진행 중인 이야기 + 거기 붙은 오늘 후보 (신규성 판단용)
  #     뒷부분 = 처음 나온 묶음 (주제별 비교 판정용)
  #   [2026-09-22 추가] timeline.py 가 끝나면 timeline_marks.py 가
  #     "과거 여러 날 후보였지만 한 번도 발송 안 된 이야기"를 blind 목록의
  #     해당 줄 끝에 [반복후보 N건·미발송 MM-DD~MM-DD] 로 찍는다.
  #     09-22 에 '현대차 아틸라스'는 08-26/08-31/09-01 세 줄이 전부 '·'
  #     (후보만 되고 미발송)였는데 timeline 을 **만들기만 하고 읽지 않아**
  #     rank 9 로 실었다. 판정표에 붙여 놓으면 지나칠 수 없다.
  cd "$W" && python3 cowork/timeline.py --date "$DATE" "${@:2}" \
    && python3 "$MNT/cowork/timeline_marks.py" --date "$DATE" ;;

case)
  # [2026-09-14 신설] 비교쌍 사례집에 한 건 덧붙인다.
  #   사용자가 발송본을 고칠 때마다 '뺀 것 / 넣은 것 + 이유'를 규칙이 아니라
  #   사례로 쌓는다. 규칙표를 늘리면 판정이 '표 훑기'로 되돌아가기 때문이다.
  #     bash cowork/run.sh case <<'RAW'
  #     날짜 | 제외|추가 | 헤드라인 | 사용자 사유 | 일반화
  #     RAW
  # [2026-09-16] 사례집 정본은 마운트다. 작업본에 쓰면 세션이 끝날 때 사라진다.
  cd "$W" && DTB_DOCS="$MNT/docs" python3 cowork/casebook.py --date "$DATE" "${@:2}" ;;

repeat)
  # [2026-09-21 신설] 반복 게이트. verdict 와 send 가 자동 호출한다.
  #   09-21 사용자 검토에서 뺀 5건 중 3건이 09-18 에 이미 뺀 건이었다.
  #   hist 는 '발송된 것'만 보고, 발송 전에 사용자가 뺀 건은 안 남는다.
  #   제외 이력(config/rejected.jsonl)과 과거 발송본을 함께 대조한다.
  cd "$W" && python3 "$MNT/cowork/repeat_gate.py" --date "$DATE" --mnt "$MNT" "${@:2}" ;;

reject-log)
  # [2026-09-21 신설] 사용자가 발송본에서 뺀 건을 기계 기록으로 남긴다.
  #   **사용자 검토를 반영한 턴에 반드시 같이 돌린다.**
  #   09-18 에 이걸 안 해서 09-21 에 같은 기사를 다시 rank 1 로 올렸다.
  #     bash cowork/run.sh reject-log [날짜] <<'RAW'
  #     헤드라인 | url | 사용자 사유
  #     RAW
  cd "$W" && python3 "$MNT/cowork/repeat_gate.py" --log \
      --date "${2:-$DATE}" --mnt "$MNT" ;;

review)
  # [2026-09-21 신설] 검토 반영 한 번에. 제외 기록 + 사례집 + 누락 점검.
  #   빼먹을 수 있는 단계를 하나로 묶는 것이 목적이다.
  #     bash cowork/run.sh review <<'RAW'
  #     제외 | 헤드라인 | url | 사용자 사유 | 일반화
  #     추가 | 헤드라인 | url | 사용자 사유 | 일반화
  #     RAW
  cd "$W" && python3 "$MNT/cowork/review_apply.py" --date "$DATE" --mnt "$MNT" ;;

agenda)
  # [2026-09-17 신설] 기자재·물량·동일주체 게이트. verdict 와 send 가 자동 호출한다.
  cd "$W" && python3 cowork/agenda_gate.py --date "$DATE" "${@:2}" ;;

newfact)
  # [2026-09-16 신설] '고' 의 `새사실:` 이 **본문에 실제로 있는 말인지** 검사.
  #   `새사실:` 문자열만 요구하면 본문을 안 읽고 지어낼 수 있다는 지적에서 나왔다.
  #   run.sh verdict 가 자동으로 부른다. 단독 실행도 된다.
  cd "$W" && python3 cowork/newfact_check.py --date "$DATE" "${@:2}" ;;

stats)
  # [2026-09-22 신설] 그날 실행이 '일을 했는지'를 숫자로 찍는다.
  #   사용자 선택으로 게이트 실패가 발송을 막지 않으므로(경고만), 대신
  #   이 숫자를 **실행 알림과 보고 맨 위에 그대로 붙인다.**
  #     bash cowork/run.sh stats            -> 사람이 읽는 형태
  #     bash cowork/run.sh stats --oneline  -> 알림 첫 줄용 한 줄
  shift || true
  cd "$W" && python3 "$MNT/cowork/runstats.py" --date "$DATE" "$@" ;;

find)
  # [2026-10-27 신설] 링크·본문 미확보 '고' 후보를 키워드로 다시 찾는다(네이버 뉴스 API).
  #   판정 중 WebSearch(미국 기준)로 국내 기사를 찾다 실패해 '중'에 묻는 일을 막는다.
  #     bash cowork/run.sh find "브로드컴 앤트로픽 630억"        -> 목록(원문·네이버 사본)
  #     bash cowork/run.sh find "도요타 휴머노이드" --body 2      -> 상위 2건 본문 + bodycache 반영
  shift || true
  cd "$W" && python3 cowork/find_article.py --date "$DATE" "$@" ;;

bodypeek)
  # [2026-09-02 신설] E축(알맹이 약함) 단독으로 '중'을 줄 때 본문 앞부분을 본다.
  #   본문은 run.sh body 가 이미 out/bodycache-<날짜>.json 에 받아 두었으므로
  #   추가 네트워크 비용이 없다. 안 여는 것이 손해다.
  #     bash cowork/run.sh bodypeek            -> 대상 후보 목록만 찍는다
  #     bash cowork/run.sh bodypeek 267 92 18  -> 그 idx 들의 앞 700자를 찍는다
  #     bash cowork/run.sh bodypeek --go       -> '고' 전량의 리드를 찍는다
  #     bash cowork/run.sh bodypeek --check    -> '고' 전량을 봤는지 검사한다
  #   [2026-09-22] 찍은 idx 는 out/peeked-<날짜>.json 에 남고 rank 가 검사한다.
  #   대상 범위는 tier0 통로이거나 보도 '다수'인 건이다. '중' 전체(98건)로
  #   넓히면 판정 후반이 형식적으로 변한다(2026-09-02 사용자 합의).
  shift || true
  cd "$W" && python3 cowork/bodypeek.py --date "$DATE" "$@" ;;

grid)
  # [2026-09-15 신설] rank 비교표. **bodygo·hist 뒤, rank 앞에 본다.**
  #   사용자 질문 "다른 사건 유형일 때는 어떻게 비교해서 랭킹을 매길 거야?"
  #   에 답이 없어서 만들었다. 네 축을 나란히 찍는다 —
  #     1 사업적용 > 2 확정성 > 3 파급(사람) > 4 체급
  #   **점수를 합산하지 않는다**(09-14 지시). 정렬은 사람이 한다.
  #   파급이 '산업구조' 급이면 1축을 역전할 수 있고(09-15 사용자 선택 B),
  #   역전하면 verdict 사유에 `역전:` 을 적어야 한다.
  shift || true
  cd "$W" && python3 cowork/rankgrid.py --date "$DATE" "$@" ;;

hist)
  # [2026-09-15 신설] '고' 판정 건의 과거 발송 이력 조회. **rank 매기기 전에 본다.**
  #   09-15 에 앤트로픽 IPO 를 실었다가 편집자가 뺐다 — 발송 이력에 7건이나
  #   있었는데 타임라인에서 네 줄로 흩어져 있어 못 봤다. 묶음이 아니라
  #   '고' 한 건씩 직접 조회한다.
  #     bash cowork/run.sh hist          -> '고' 전량
  #     bash cowork/run.sh hist 1 19     -> 특정 idx
  shift || true
  cd "$W" && python3 cowork/hist.py --date "$DATE" "$@" ;;

config)
  # [2026-09-15 신설] 오늘 쓸 설정값 한 장. **발송본을 쓰기 전에 이것부터 본다.**
  #   09-15 에 같은 원인으로 세 번 틀렸다 — 카테고리 집합·카테고리 배치·
  #   시사점 길이. 전부 설정값 사본이 여러 곳에 있고 낡은 것을 읽은 경우다.
  #   이 명령은 값을 안 들고 있고 brief.py / send_telegram.py 에서 읽어 온다.
  #   **여기에 숫자를 하드코딩하면 사본이 하나 더 느는 것이므로 금지.**
  cd "$W" && python3 cowork/config_sheet.py ;;

cats)
  # [2026-09-15 신설] 발송본 카테고리 검사. **정본은 brief.py 의 CATEGORY_ORDER.**
  #   09-15 에 폐기된 '독파모 & 모두의 AI' 섹션을 되살리고 순서도 틀렸는데
  #   send 까지 아무 검사에 안 걸렸다. 기계가 정본을 들고 있는데 발송본에
  #   그것을 강제하는 단계가 없던 것이 원인이다.
  #   목록 밖 섹션·순서·중복·번호 불연속은 **종료코드 1**(발송 차단).
  #   기계 분류와 다른 배치는 경고로만 찍는다(기계가 정답은 아니므로).
  shift || true
  cd "$W" && python3 cowork/catcheck.py --date "$DATE" "$@" ;;

style)
  # [2026-09-27 신설] 문체 게이트. 주요 내용·시사점이 서술형('~다.' '~습니다.')으로
  #   끝나면 종료코드 1. 09-23~25 사흘치가 서술형으로 나간 뒤 만들었다.
  #   8단계 검증에서 insight 다음에 돌린다. send 도 자동 호출한다(경고).
  cd "$W" && python3 "$MNT/cowork/style_check.py" --date "$DATE" --stem "${2:-brief}" ;;

insight)
  # [2026-09-09 임원 피드백] 시사점 품질 검사.
  #   '시사점에 가끔 의미 없는 게 있다. 좀 더 인사이트가 담겨 있으면 좋겠다.'
  #   동어반복·공허한 술어·너무 짧음 세 신호를 찾아 보여준다(경고 전용, 종료코드 0).
  #   8단계 검증에서 linkcheck 와 함께 돌린다.
  cd "$W" && python3 cowork/insight_check.py --date "$DATE" ;;

budget)
  # [2026-10-20 신설] 발송본을 쓰기 **전에** 건당 본문 예산을 찍는다.
  #   send_telegram 과 같은 자(tg_len)로 뼈대(머리글·헤더·헤드라인·매체·원문보기·라벨)를
  #   재고 상한에서 빼서 '주요 N자·시사점 M자'를 알려준다. 10-20 실측: 예산을 모른 채
  #   20건을 쓰고 재니 5,008/4,080 -> 압축 5회. 이 명령 뒤에 그 예산 안에서 한 번에 쓴다.
  #     run.sh budget                  -> verdict '고' rank 순 상위 20건
  #     run.sh budget --top 18         -> 상위 18건 가정
  #     run.sh budget --idx 123 125 …  -> 발송 예정 idx 직접
  #     run.sh budget --idx … --headline N66="바꾼 제목"
  #   20건 상한(09-10)·'빼지 말고 압축'(09-28)은 바꾸지 않는다. 계산기일 뿐이다.
  shift || true
  cd "$W" && python3 cowork/budget.py --date "$DATE" "$@" ;;

verdict-xlsx)
  # [2026-09-04 사용자 지시] 전수 판정 결과를 엑셀로 만든다.
  #   '앞으로 뉴스 리스트는 본문으로 안 보여줘도 되고, 대신 고/중/저 판단
  #    결과를 엑셀로 만들어서 항상 같이 줘.'
  #   산출물: out/판정-<날짜>.xlsx (요약 / 전수판정 / 발송본 3시트)
  #   9단계 send 뒤에 돌리고, 10단계 보고에서 present_files 로 함께 제시한다.
  #
  #   [2026-09-22 신설] **순위 근거 열(D~I)이 비는 사고가 두 번째다.**
  #     09-08~09-10 에 사흘 비었고(그때 경고를 넣었다), 09-15·09-18·09-21·09-22
  #     에 또 비었다. 사용자가 두 번 다 엑셀을 보고 발견했다.
  #     경고만으로는 안 막힌다. 원인은 코드가 아니라 **순서**다 —
  #     rankscore CSV 가 없거나 판정 CSV 보다 오래됐으면(판정을 다시 했는데
  #     rank 를 다시 안 돌렸으면) 조용히 빈 칸이 된다.
  #     그래서 여기서 **직접 검사하고, 낡았으면 rank 부터 다시 돌린다.**
  _vp="$W/out/verdict-$DATE.csv"; _rp="$W/out/rankscore-$DATE.csv"
  if [ -f "$_vp" ] && { [ ! -f "$_rp" ] || [ "$_vp" -nt "$_rp" ]; }; then
    echo "[verdict-xlsx] 순위표가 판정보다 낡았습니다 — rank 를 먼저 돌립니다." >&2
    # [2026-10-06] rank 는 verdict CSV 의 rank 열을 모델 순위로 **덮어쓴다.**
    #   손으로 매긴 편집 순위로 발송본을 골랐다면 그 순위가 사라진다(10-06 사고).
    #   handrank 파일이 없으면 덮어쓰기 전 rank 열을 보존해 둔다(사유는 빈 칸).
    _hp="$W/out/handrank-$DATE.csv"
    if [ ! -f "$_hp" ]; then
      python3 - "$_vp" "$_hp" <<'PY'
import csv, sys
rows = [r for r in csv.DictReader(open(sys.argv[1], encoding="utf-8"))
        if r.get("grade") == "고" and (r.get("rank") or "").strip()]
if rows:
    with open(sys.argv[2], "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f); w.writerow(["idx", "발송순위", "발송순위 사유"])
        for r in rows: w.writerow([r["idx"], r["rank"], "(자동보존: 사유 미기입)"])
    print("[verdict-xlsx] 편집 순위 %d건을 out/handrank 로 보존한 뒤 rank 를 돌립니다" % len(rows), file=sys.stderr)
PY
    fi
    (cd "$W" && python3 cowork/bodypeek.py --date "$DATE" --check \
       && python3 cowork/rankscore.py --date "$DATE") \
      || echo "[verdict-xlsx] **[경고] rank 재실행 실패 — 순위 근거 열(D~I)이 빈 칸으로 나갑니다.**" >&2
  fi
  cd "$W" && python3 cowork/verdict_xlsx.py --date "$DATE" ;;

spread)
  # [2026-09-22] 판정 뭉침 단독 검사. verdict 와 send 가 자동 호출한다.
  cd "$W" && python3 "$MNT/cowork/verdict_spread.py" --date "$DATE" "${@:2}" ;;

verdict)
  # [2026-09-01] 전수 판정 결과(out/verdict-<날짜>.csv)를 검증한다.
  #   행 수가 후보 수와 다르면 **판정을 건너뛴 것**이므로 종료코드 1.
  cd "$W" && python3 - "$DATE" <<'PY'
import csv, json, os, re, sys
day = sys.argv[1]
cand = []
for line in open("out/prompt-%s.txt" % day, encoding="utf-8"):
    if line.startswith('{"idx"'):
        cand.append(str(json.loads(line)["idx"]))
    elif line.startswith('{"nolink_idx"'):
        cand.append("N%s" % json.loads(line)["nolink_idx"])
path = "out/verdict-%s.csv" % day
if not os.path.exists(path):
    print("[verdict] %s 없음. run.sh blind 로 목록을 만든 뒤 채우세요." % path)
    sys.exit(1)
rows = [r for r in csv.DictReader(open(path, encoding="utf-8")) if r.get("idx")]
seen = {r["idx"].strip(): r for r in rows}
missing = [i for i in cand if i not in seen]
extra = [i for i in seen if i not in set(cand)]
from collections import Counter
g = Counter(r["grade"].strip() for r in rows)
print("[verdict] 후보 %d건 / 판정 %d건 | 고 %d · 중 %d · 저 %d"
      % (len(cand), len(rows), g.get("고", 0), g.get("중", 0), g.get("저", 0)))
if missing:
    print("[verdict] **판정 누락 %d건** (앞 20개): %s"
          % (len(missing), ", ".join(missing[:20])))
if extra:
    print("[verdict] 후보에 없는 idx %d건: %s" % (len(extra), ", ".join(extra[:10])))
bad = [r["idx"] for r in rows if r["grade"].strip() not in ("고", "중", "저")]
if bad:
    print("[verdict] 등급값 오류 %d건: %s" % (len(bad), ", ".join(bad[:10])))
if g.get("고", 0) == 0:
    print("[verdict] [경고] '고'가 0건입니다. 판정 기준을 다시 보세요.")

# [2026-09-07 신설] 판정·순위 사유에 카테고리 이름을 쓰면 안 된다.
#   사용자 지시(반복): "카테고리별로 분량을 정하지 말고, 중요한 기사가
#   있으면 많이 넣고 없으면 안 넣어도 된다." 09-07 에 전날 카테고리
#   분포(AI DC 5칸/정답지 0건)를 다음 날 순위의 입력값으로 써서
#   'Physical AI 8칸 / AI DC 0칸'이라는 정원을 만들었고, 그 결과
#   '고'로 판정한 AIDC 기사 3건을 3축(파급·확정성·SKT 관련성)이 아니라
#   카테고리를 이유로 컷했다. 사유 문자열에서 기계적으로 막는다.
#   기사 주제로서의 'AIDC·Physical AI' 는 사유에 써도 된다. 막을 것은
#   **정원·분포 어휘**다.
_CATWORDS = ("카테고리", "분량 배분", "분량배분", "정원", "편중", "분포",
             "칸 배정", "칸을", "칸으로", "칸 확보", "쿼터", "균형")
catbad = [r["idx"] for r in rows
          if r["grade"].strip() == "고"
          and any(w in (r.get("reason") or "") for w in _CATWORDS)]
if catbad:
    print("[verdict] **'고' 사유에 카테고리 표현 %d건**: %s"
          % (len(catbad), ", ".join(catbad[:15])))
    print("[verdict]   카테고리는 8단계 배치 기준일 뿐 판정·순위 근거가 "
          "아닙니다(dtb-no-category-quota).")
    rank_bad_cat = True
else:
    rank_bad_cat = False

# [2026-09-03 사용자 지시] rank 열 검사.
#   왜 넣었나: 09-03 에 놓친 11건 중 3건이 '고'로 제대로 판정해 놓고
#   순위에서 밀린 것이었다. 순위 단계는 흔적이 없어서 판정 실패인지
#   순위 실패인지 구분되지 않았고 스코어카드에 'LLM 선별 후보에서 탈락'
#   으로만 찍혔다. rank 를 파일에 남겨 다음 날 셀 수 있게 한다.
gos = [r for r in rows if r["grade"].strip() == "고"]
def _rank(r):
    v = (r.get("rank") or "").strip()
    return int(v) if v.isdigit() else None
ranked = [(r["idx"], _rank(r)) for r in gos]
norank = [i for i, k in ranked if k is None]
nums = [k for _, k in ranked if k is not None]
rank_bad = False
if not gos:
    pass
elif norank:
    print("[verdict] **'고' %d건 중 rank 누락 %d건**: %s"
          % (len(gos), len(norank), ", ".join(norank[:20])))
    print("[verdict]   형식은 idx,grade,reason,**rank** 입니다(2026-09-03 개정).")
    print("[verdict]   스케줄 지침(SKILL.md)의 'idx,grade,reason' 은 옛 형식입니다.")
    print("[verdict]   순위 매기는 법: docs/운영지침-변경이력.md 또는 프롬프트 2단계")
    rank_bad = True
elif sorted(nums) != list(range(1, len(gos) + 1)):
    dup = sorted({n for n in nums if nums.count(n) > 1})
    print("[verdict] **rank 가 1..%d 연속이 아닙니다.** 중복=%s / 값=%s"
          % (len(gos), dup or "-", sorted(nums)[:20]))
    rank_bad = True
# rank 잘못 채운 '중'·'저'는 경고만 (컷 대상이 아니므로)
stray = [r["idx"] for r in rows
         if r["grade"].strip() != "고" and (r.get("rank") or "").strip()]
if stray:
    print("[verdict] [경고] '고'가 아닌데 rank 가 채워진 건 %d건: %s"
          % (len(stray), ", ".join(stray[:10])))

# [2026-09-10 신설 2] '고' 사유에 **반증**을 함께 적게 강제한다.
#   왜 넣었나: 09-10 하루에 같은 오판을 네 번 했다. 애플워치(연례 갱신을
#   신규 출시로) · 하나카드(참여사를 주관사처럼) · 통신3사(상시 채용을
#   사건으로) · 대동(농작업 로봇을 휴머노이드 특례로). **네 건 다 규칙은
#   이미 있었다**(C축 기존개선·A축 주체·채용 저·09-09 로봇 특례 축소).
#   공통점은 하나 - **올릴 근거를 하나 찾고 거기서 멈췄다. 반증을 안 했다.**
#   그런데 내릴 근거는 네 번 다 같은 제목 안에 있었다:
#     '시리즈12'(12번째) · '참여'(주관사 아님) · '상시'(반복) · '농작업'(산업용).
#   문서에 규칙을 한 줄 더 얹는 방식은 이미 네 번 실패했으므로,
#   **판정 경로에서 형식으로 강제한다.**
#
#   형식: 고 사유 = "올린 근거 / 내릴 뻔한 근거와 그것을 넘는 이유"
#     (O) F1(20조)·첫 원전 계약 / 내릴 이유 없음
#     (O) 국책과제 선정 / 농작업=산업용 자동화지만 431억+국산NPU 자립이라 넘음
#     (X) 모두의AI 금융파트너 확대          <- 반증 칸이 없다
#   '/' 뒤를 쓰다 보면 대동·하나카드 같은 건은 쓰는 순간 스스로 걸린다.
gos_r = [r for r in rows if r["grade"].strip() == "고"]
noref = [r["idx"] for r in gos_r if "/" not in (r.get("reason") or "")]
ref_bad = False
if gos_r and noref:
    print("[verdict] **'고' %d건 중 반증을 안 적은 건 %d건**: %s"
          % (len(gos_r), len(noref), ", ".join(noref[:20])))
    print("[verdict]   형식: '올린 근거 / 내릴 뻔한 근거와 넘는 이유'")
    print("[verdict]   내릴 이유가 정말 없으면 '/ 내릴 이유 없음' 이라고 적으세요.")
    print("[verdict]   09-10 오판 4건은 전부 반증을 안 해서 났습니다.")
    ref_bad = True

# [2026-09-16 신설] '고' 사유에 **이 기사에만 있는 새 사실**을 적게 강제한다.
#   왜 넣었나: 09-16 에 편집자가 뺀 4건 중 3건이 같은 병이었다.
#     · 'AI 안전 충돌'(rank 1) — 아모데이의 속도조절 주장은 09-14 발송,
#       젠슨황의 반대는 09-15 발송. 오늘 기사는 그 둘을 나란히 놓은 것뿐.
#     · 'ICT R&D 2조원'    — 내년도 R&D 예산은 이전에 수차례 나갔다.
#     · '허깅페이스 해킹'   — 사건은 08-28·09-14 발송. 새로 붙은 것은
#       올트먼 코멘트 하나이고, 그나마 속도조절론을 뒷받침하는 의견이었다.
#   사용자 원문: *"새로운 내용이 없는거잖아. 근데 너는 1순위로 넣었어."*
#
#   09-14 축1('신규성은 제목이 아니라 본문으로 확인한다')과
#   사례집 09-15 항목('hist 로 몇 번 나갔는지 세고, 새 사실을 한 문장으로
#   못 쓰면 중')이 **이미 있었다.** 지키지 않아서 난 사고이므로 규칙을
#   더 적지 않고 판정 경로에서 형식으로 막는다.
#
#   형식: 고 사유 안에 `새사실:<한 문장>` 을 넣는다.
#     (O) 빅테크 신규출시 / 반증 … / 새사실:Live·확장사고를 별도 모델로 분리
#     (X) 지정학·AI규범 / 반증 …            <- 새사실 칸이 없다
#   '어제 기사와 무엇이 다른가'를 한 줄로 못 쓰면 그 건은 '고'가 아니다.
newfact = [r["idx"] for r in gos_r if "새사실:" not in (r.get("reason") or "")]
nf_bad = False
if gos_r and newfact:
    print("[verdict] **'고' %d건 중 새 사실을 안 적은 건 %d건**: %s"
          % (len(gos_r), len(newfact), ", ".join(newfact[:20])))
    print("[verdict]   사유에 `새사실:<이 기사에만 있는 것 한 문장>` 을 넣으세요.")
    print("[verdict]   먼저 `run.sh hist` 로 그 이야기가 최근에 몇 번 나갔는지 봅니다.")
    print("[verdict]   09-16 에 뺀 4건 중 3건이 '새 사실 없음' 이었습니다.")
    nf_bad = True

# [2026-09-10 신설] '중' 사유도 근거를 남겨야 한다.
#   왜 넣었나: 09-10 판정에서 '중' 196건이 **전부 "중요도 중"** 한 문구였다.
#   그래서 (1) 왜 중을 줬는지 재현이 안 되고 (2) 다음 날 채점에서
#   'LLM 선별 후보에서 탈락' 한 덩어리로 뭉개지고 (3) 사용자가
#   "왜 아이폰 듀오가 중이냐" 고 물었을 때 답을 못 했다.
#   실제로 그 건은 '애플을 이미 한 건 썼으니까' 라는, 판정 축이 아닌
#   이유로 깎인 것이었는데 기록이 없어 세 번을 되짚어야 했다.
#   '고' 는 사유가 46건 46종으로 멀쩡했다. 문제는 '중' 뿐이다.
jungs = [r for r in rows if r["grade"].strip() == "중"]
jung_bad = False
if jungs:
    uniq = {(r.get("reason") or "").strip() for r in jungs}
    uniq.discard("")
    blank = [r["idx"] for r in jungs if not (r.get("reason") or "").strip()]
    if len(uniq) < 5:
        print("[verdict] **'중' %d건의 사유가 %d종뿐입니다: %s**"
              % (len(jungs), len(uniq), ", ".join(sorted(uniq)[:5])))
        print("[verdict]   '중' 도 축 이름을 남기세요(E축 알맹이 / C축 진행단계 /")
        print("[verdict]   F2 중국 / F7 언론사 / 학계 / 2금융 / 지표갱신 …).")
        print("[verdict]   한 문구로 뭉개면 다음 날 채점에서 원인을 못 가릅니다.")
        jung_bad = True
    if blank:
        print("[verdict] **'중' 사유 빈칸 %d건**: %s"
              % (len(blank), ", ".join(blank[:15])))
        jung_bad = True

# [2026-09-15 신설] '고' 에 붙은 [중복의심] 표시를 무시하지 못하게 한다.
#   왜 넣었나: 09-15 에 편집자가 뺀 '앤트로픽 2조달러 IPO 나스닥'(idx 1)은
#   blind 목록에 **[중복의심: 조직명 공유 [2026-09-09] "앤트로픽·오픈AI IPO
#   이후 투자적격 등급"]** 이 이미 찍혀 있었다. 기계가 경고했는데 읽고도
#   rank 19 로 실었다. 사용자 원문: *"이전부터 IPO 관련 기사들이 계속
#   나왔었고, 오늘 기사에는 새로운 내용이 거의 없어서 내가 빼자고 한거야."*
#
#   **자동으로 빼지 않는다.** 같은 날 idx 19(오픈AI 휴머노이드)도 중복의심이
#   붙었지만 짝이 '오픈AI 상장 연기' 라 다른 사건이었고 편집자가 실었다.
#   조직명만 공유하는 오탐이 섞이므로 기계가 자르면 안 된다.
#   요구하는 것은 하나 — **사유에 '중복검토:' 를 적어 해명할 것.**
dup = {}
try:
    _bl = open("out/candidates-blind-%s.txt" % day, encoding="utf-8")
    for _l in _bl:
        _m = re.match(r"^(N?\d+) \| ", _l)
        if _m and "[중복의심" in _l:
            dup[_m.group(1)] = _l.split("[중복의심", 1)[1].rstrip().rstrip("]")[:70]
except Exception:
    pass
dup_bad = False
_need = [r for r in gos_r
         if r["idx"] in dup and "중복검토" not in (r.get("reason") or "")]
if _need:
    print("[verdict] **'고' 중 [중복의심] 표시를 해명하지 않은 건 %d건**" % len(_need))
    for r in _need:
        print("[verdict]   idx %-4s 짝: %s" % (r["idx"], dup.get(r["idx"], "")))
    print("[verdict]   사유에 '중복검토: <같은 사건인가 / 새 사실이 무엇인가>' 를 적으세요.")
    print("[verdict]   같은 사건이면 '중' 으로 내리고, 다른 사건이면 왜 다른지 한 줄.")
    print("[verdict]   09-15 앤트로픽 IPO 를 이 표시를 보고도 실어 편집자가 뺐습니다.")
    dup_bad = True

# [2026-09-15 신설] 역전(B) 을 쓸 때 사유를 남기게 한다.
#   사용자 선택 B: "파급이 매우 크면 1축(사업적용)을 역전해 상단 허용".
#   역전 자체는 옳다 — 우리 사업에 안 걸리는 빅테크 지각변동이 구조적으로
#   밀리면 안 되기 때문이다. 다만 **아무 때나 쓰면 축이 있으나 마나**가 되므로
#   1축이 더 높은 건을 제치고 올라간 건에는 사유에 `역전:` 을 요구한다.
#
#   **[2026-09-15 즉시 정정] 종료코드 1 이 아니라 경고(0)로 둔다.**
#   1축 자동값은 '주제 키워드' 라서 '이 기사가 우리 사업을 바꾸나' 와 자주
#   어긋난다. 실측: 오라클 감원 기사가 헤드라인의 'AI 데이터센터' 때문에
#   최우선3 으로 잡혀, 그보다 위에 있던 19건이 전부 역전으로 찍혔다.
#   **게이트가 내 순위가 아니라 분류기 오판을 재고 있었다.**
#   막는 대신 눈에 띄게만 한다. 1축 확정은 `run.sh grid --csv` 로 사람이 한다.
rev_bad = False
try:
    sys.path.insert(0, "cowork")
    import rankgrid as _rg
    _bh = {}
    _bp = "out/candidates-blind-%s.txt" % day
    if os.path.exists(_bp):
        for _l in open(_bp, encoding="utf-8"):
            _m = re.match(r"^(N?\d+) \| \S+ \| .+? \| \S* \| \S+ \| (?:⟨[^⟩]+⟩ )?(.+?)(?: \[중복의심.*)?$",
                          _l.rstrip())
            if _m:
                _bh[_m.group(1)] = _m.group(2)
    _ranked = [r for r in gos_r if (r.get("rank") or "").strip().isdigit()]
    _ranked.sort(key=lambda r: int(r["rank"]))
    _seen_biz = 9
    _rev = []
    for r in _ranked:
        _b = _rg.biz_of(_bh.get(r["idx"], ""))
        # 앞에서 이미 더 좋은(작은) 1축이 나왔는데 지금 것이 그보다 나쁘면
        # 정상. 반대로 **뒤에 더 좋은 1축이 남아 있는데 먼저 올라온 것**이 역전.
        _seen_biz = min(_seen_biz, _b)
        r["_biz"] = _b
    for i, r in enumerate(_ranked):
        # 1단계 차이는 정상 판단 범위다. **2단계 이상 뛰어넘을 때만** 묻는다.
        #   실측 09-15: 1단계까지 잡으면 31건 중 20건이 걸려 표가 무의미해졌다.
        _later_better = [x for x in _ranked[i + 1:] if x["_biz"] <= r["_biz"] - 2]
        if _later_better and "역전" not in (r.get("reason") or ""):
            _rev.append((r, _later_better[0]))
    if _rev:
        print("[verdict] [참고] 1축을 2단계 이상 역전한 건 %d건 (사유 없음)" % len(_rev))
        print("[verdict]   **경고일 뿐 발송을 막지 않습니다.** 1축 자동값이 자주 틀립니다.")
        for r, o in _rev[:6]:
            print("[verdict]   rank %-3s %s(%s)  <- rank %s %s(%s) 보다 위"
                  % (r["rank"], r["idx"], _rg.BIZ_LABEL[r["_biz"]],
                     o["rank"], o["idx"], _rg.BIZ_LABEL[o["_biz"]]))
        print("[verdict]   의도한 역전이면 사유에 `역전:<파급 근거>` 를 적어 두세요(09-15 B).")
        print("[verdict]   자동값이 틀린 것이면 그냥 넘어가면 됩니다 - `run.sh grid` 로 확인.")
except Exception as _e:
    print("[verdict] [경고] 역전 점검을 못 했습니다: %s" % _e)

# [2026-09-30 사용자 지시] '고'가 적은 날의 2차 검토.
#   09-30 '고' 8건(역대 최저) -> 사용자가 중·저에서 4건을 직접 골라 추가했다.
#   원문: "고 기사 숫자가 매우 작았어 ... 너가 너무 고 선정 기준이 까다로운건
#   아닌지 다시 한번 고민 필요". 하한선으로 '중'을 채우는 게 아니라, 감점 사유가
#   C축(진행)·E축(알맹이)·G축/중복인 '중'을 한 번 더 본 흔적을 남기게 한다.
#   통과 조건: '고' 12건 이상이거나, 사유에 `2차검토:` 가 있는 행이 1건 이상.
#   (올린 건은 '고' 사유에, 안 올린 건은 '중' 사유에 `2차검토:<이유>` 를 붙인다)
R2_MIN = 12
r2_bad = False
if len(gos) < R2_MIN:
    marked = [r["idx"] for r in rows if "2차검토:" in (r.get("reason") or "")]
    if not marked:
        pool = [r for r in rows if r["grade"].strip() == "중"
                and re.match(r"\s*(C축|E축|G축|중복|동일주체|F7)", r.get("reason") or "")]
        print("[verdict] **'고' %d건 < %d — 2차 검토 필요(2026-09-30 사용자 지시)**"
              % (len(gos), R2_MIN))
        print("[verdict]   아래 '중' 을 다시 보고, 올리면 '고' 사유에·안 올리면 '중' 사유에")
        print("[verdict]   `2차검토:<이유>` 를 붙이세요. 투자 추진(새 숫자)·에이전트 실행")
        print("[verdict]   인프라(E-g)·앵글이 바뀐 같은 주체(G축)가 09-30 에 놓친 유형입니다.")
        print("[verdict]   '저' 의 `중복:` 도 앵글(투자↔제품·사용기)이 다르면 다시 볼 것(09-30 인스팅트).")
        for r in pool[:25]:
            print("[verdict]     %-5s %s" % (r["idx"], (r.get("reason") or "")[:70]))
        r2_bad = True
    else:
        print("[verdict] 2차 검토 흔적 %d건 ('고' %d건 < %d)" % (len(marked), len(gos), R2_MIN))

order = sorted(gos, key=lambda r: (_rank(r) is None, _rank(r) or 0))
print("[verdict] '고' %d건 (rank 순): %s"
      % (len(gos), ", ".join("%s:%s" % ((r.get("rank") or "-").strip(), r["idx"])
                             for r in order)))
sys.exit(1 if (missing or extra or bad or rank_bad or rank_bad_cat
               or jung_bad or ref_bad or nf_bad or dup_bad or rev_bad or r2_bad) else 0)
PY
  _v=$?
  # [2026-09-16 신설] 위 게이트는 '새사실: 을 적었는가' 만 본다. 본문을 안 읽고
  #   지어낼 수 있으므로 **본문 대조**를 여기서 한 번 더 한다.
  # [2026-09-22 신설] 판정 뭉침 게이트. 전수 판정을 실제로 했는지 본다.
  #   09-22 에 사유 8종 중 하나가 91% 였는데 옛 '1종뿐' 검사는 통과시켰다.
  ( cd "$W" && python3 "$MNT/cowork/verdict_spread.py" --date "$DATE" ) || _v=1
  ( cd "$W" && python3 cowork/newfact_check.py --date "$DATE" ) || _v=1
  # [2026-09-16 신설] 사례집이 비어 있으면 판정이 무효다. 09-16 에 작업본
  #   사례집이 빈 파일인 줄 모르고 판정했다(마운트엔 18건이 있었다).
  ( cd "$MNT" && python3 cowork/casebook.py --check ) || _v=1
  # [2026-09-17 신설] 어젠다 밖 게이트. 09-17 에 편집자가 뺀 2건이 둘 다
  #   이미 있던 규칙이었다 — 발전기 공급(D축 배제7 기자재)과 GPU 물량 배정
  #   (09-14 '단순 물량 확보는 중'). 규칙을 또 적는 대신 해명을 강제한다.
  ( cd "$W" && python3 "$MNT/cowork/repeat_gate.py" --date "$DATE" --mnt "$MNT" ) || _v=1
  ( cd "$W" && python3 cowork/agenda_gate.py --date "$DATE" ) || _v=1
  # [2026-09-30 신설] 신규성 감점 게이트. '후보로 여러 번 봤다' ≠ '독자가 안다'.
  #   09-29 LLM 재킹·09-30 인스팅트 사용기를 발송 0건인데 반복/중복으로 내렸다.
  ( cd "$W" && python3 "$MNT/cowork/novelty_gate.py" "$DATE" ) || _v=1
  # [2026-10-27 신설] 링크·본문 미확보 강등 게이트. 못 찾아서 내린 '중' 에 run.sh find 기록 요구.
  ( cd "$W" && python3 "$MNT/cowork/rescue_gate.py" "$DATE" ) || _v=1
  exit $_v
  ;;
novelty)
  ( cd "$W" && python3 "$MNT/cowork/novelty_gate.py" "$DATE" ) ;;
rescue)
  ( cd "$W" && python3 "$MNT/cowork/rescue_gate.py" "$DATE" ) ;;

deals)
  # [2026-08-31] 큰 딜(1천억원 이상) 전량검토 확인. 8단계 검증에서 돈다.
  #   왜 필요한가: 선별 기준 F1('1천억 이상은 큰 건')은 A·C축 감점을 뒤집는
  #   보정 규칙인데, 눈으로만 적용하다 보니 점수가 낮은 건에서 통째로 건너뛴다.
  #   실측 08-31: '우주에 데이터센터 짓는다고 3500억 받은 스타트업'(5점,
  #   tier0)을 무명 스타트업 + 주말 기획으로 보고 중으로 내렸는데 정답지였다.
  #   빠진 건이 있으면 종료코드 1. 지우라는 뜻이 아니라 **사유를 대라는 뜻**이다.
  cd "$W" && python3 - "$DATE" <<'PY'
import json, re, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath("brief.py")))
import importlib.util
spec = importlib.util.spec_from_file_location("bf", "brief.py")
bf = importlib.util.module_from_spec(spec)
sys.modules["bf"] = bf
spec.loader.exec_module(bf)
day = sys.argv[1]
prompt = open("out/prompt-%s.txt" % day, encoding="utf-8").read()
cands = []
for l in prompt.split("\n"):
    if l.startswith('{"idx"') or l.startswith('{"nolink_idx"'):
        try:
            cands.append(json.loads(l))
        except Exception:
            pass
brief = open("out/brief-%s.md" % day, encoding="utf-8").read()
burls = set(re.findall(r"\]\((https?://[^)]+)\)", brief))
btext = brief
big, taken, missing = bf.big_deals(cands), 0, []
flat = re.sub(r"[^0-9A-Za-z가-힣]", "", btext)
for v, d, members in big:
    # 같은 사건 묶음 안에서 **아무 멤버라도** 브리프에 있으면 채택으로 친다.
    # (대표로 뽑힌 기사와 내가 실은 기사가 다를 수 있다)
    ok = False
    for m in members:
        core = re.sub(r"[^0-9A-Za-z가-힣]", "", m.get("headline") or "")[:16]
        if (m.get("url") and m["url"] in burls) or (core and core in flat):
            ok = True
            break
    if ok:
        taken += 1
    else:
        missing.append((v, d))
print("[deals] 묻힌 큰 딜 %d건 (1천억원 이상 & %d점 미만) / 브리프 채택 %d건"
      % (len(big), bf.DEAL_SCORE_MAX, taken))
for v, d in sorted(missing, key=lambda x: -x[0]):
    print("  [빠짐] %s %s (%s, %s점)" % (
        bf.fmt_krw(v), (d.get("headline") or "")[:62],
        d.get("outlet") or "?", d.get("score")))
if missing:
    print("[deals] 빠진 %d건은 **사유를 댈 수 있어야 합니다**. 허용 사유는 둘뿐:" % len(missing))
    print("        (가) 그 사건이 이미 다른 후보로 브리프에 있다")
    print("        (나) D축 배제 7항목에 해당한다(항목 이름을 댈 것)")
    print("        '무명이라'·'평이해서'·'점수가 낮아서'는 사유가 아닙니다.")
    # [2026-08-31] 종료코드는 0 으로 둔다. 소급 검증상 하루 평균 9건이 뜨고
    #   그중 정답지는 16일에 3건이라, 발송을 막는 게이트로 쓰면 매일 걸려
    #   곧 무시하게 된다(알람 피로). 근본 처방은 refine 의 금액 인식 쪽이고
    #   이 목록은 눈으로 한 번 훑는 **보조 점검**이다.
    sys.exit(0)
print("[deals] 누락 없음")
PY
  ;;

rank)
  # [2026-09-09 전면 교체] config/rankweights.json 의 **학습된 가중치**로
  #   '고' 건의 점수를 계산해 verdict 의 rank 열을 채운다.
  #   종전 판은 사람이 손으로 적은 rankscore CSV 를 읽어 정렬만 했다
  #   (실측 45.7%, 무작위 37.4%). 신 모델은 71.7%.
  #   점수 구성은 cowork/rankscore.py 상단 주석 참조.
  #   [2026-09-22 추가] 순위를 매기기 전에 **'고' 전량의 본문을 봤는지 검사**한다.
  #     제목만 보고 '고'를 주는 일을 구조적으로 막는다. 안 본 건이 있으면
  #     여기서 멈춘다. `bash cowork/run.sh bodypeek --go` 로 열고 다시 오세요.
  cd "$W" && python3 cowork/bodypeek.py --date "$DATE" --check \
    && python3 cowork/rankscore.py --date "$DATE" ;;

rankeval)
  # [2026-09-09 신설] 순위 가중치의 leave-one-day-out 회귀 검증.
  #   **피처·정규식을 건드리면 반드시 이것부터 돌린다.** 쓰기 없음.
  cd "$W" && python3 cowork/rankfit.py --eval "${@:2}" ;;

rankfit)
  # [2026-09-09 신설] 정답지가 새로 올라온 날 backtest 뒤에 돌린다.
  #   전체 날짜로 학습해 config/rankweights.json 을 갱신한다.
  #   갱신 전에 rankeval 로 신 모델이 현행보다 나은지 확인할 것.
  cd "$W" && python3 cowork/rankfit.py --fit "${@:2}" \
    && cp -f "$W/config/rankweights.json" "$MNT/config/rankweights.json" \
    && echo "[rankfit] 마운트에도 반영: config/rankweights.json" ;;

tg-del)
  # [2026-09-07 신설] 이미 보낸 초안 메시지를 지운다.
  #   사용법: bash cowork/run.sh tg-del <message_id> [message_id ...]
  #   브리프를 고쳐 다시 보낼 때 옛 메시지를 남겨 두면 어느 것이
  #   최종본인지 알 수 없어서 필요하다. 초안 채널만 대상으로 한다.
  shift
  cd "$W" && python3 - "$@" <<'PYDEL'
import os, sys, json, urllib.request
tok = os.environ.get("TELEGRAM_BOT_TOKEN")
chat = os.environ.get("TELEGRAM_DRAFT_CHAT_ID")
if not tok or not chat:
    print("[tg-del] 토큰/채널ID 없음 (cowork/secrets.env 확인)")
    sys.exit(1)
for mid in sys.argv[1:]:
    url = "https://api.telegram.org/bot%s/deleteMessage" % tok
    data = json.dumps({"chat_id": chat, "message_id": int(mid)}).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=20))
        print("[tg-del] %s -> %s" % (mid, r.get("ok")))
    except Exception as e:
        print("[tg-del] %s -> 실패 %s" % (mid, e))
PYDEL
  ;;

send)
  dtb_require_workday || exit 3
  # 완성된 브리프를 텔레그램 채널로 보낸다. push 뒤에 돌린다.
  # [2026-09-01] 발송 대상은 brief-<날짜>.md 하나다(30/20 두 단계 폐지).
  #   30건을 보내려면 run.sh send draft brief 처럼 stem 을 명시한다.
  #   run.sh send            -> 초안 채널(TELEGRAM_DRAFT_CHAT_ID)
  #   run.sh send final      -> 운영 채널(TELEGRAM_FINAL_CHAT_ID)
  #   run.sh send dry        -> 발송 없이 미리보기
  # 토큰·채널ID 는 cowork/secrets.env 에 있다(git·메모리에 남기지 말 것).
  #
  # [2026-09-21 신설] **발송 잠금.** 2026-09-18 사용자 지시로 파이프라인은
  #   '판정 엑셀까지 만들고 멈춘다'. 그런데 그 개정이 스케줄 작업 프롬프트에는
  #   반영되지 않아(09-21 확인) 프롬프트만 읽은 실행은 여전히 여기로 온다.
  #   문서 두 곳이 갈리면 실행이 낡은 쪽을 따라가므로 **코드로 잠근다.**
  #   사용자가 "보내"라고 한 뒤에만 `run.sh send go` 로 푼다.
  #     run.sh send        -> 잠김(종료코드 1)
  #     run.sh send dry    -> 계산기. 항상 허용
  #     run.sh send go     -> 초안 채널로 실제 발송
  # [2026-09-22 사용자 지시] **자동 발송으로 되돌린다.**
  #   원문: "내일부터는 판정한 결과로 텔레그램 발송까지해!!! 내가 검토 없어"
  #   -> 09-18 의 '판정 엑셀까지 만들고 멈춘다' 와 09-21 의 발송 잠금(DTB_SEND_OK)
  #      을 **해제한다.** 스케줄 실행이 판정 뒤 곧바로 보낸다.
  #     run.sh send        -> 초안 채널(TELEGRAM_DRAFT_CHAT_ID) 자동 발송  [기본]
  #     run.sh send dry    -> 계산기. 발송 없음
  #     run.sh send go     -> send 와 동일(옛 해제 경로. 계속 받아준다)
  #     run.sh send final  -> 운영 채널. **09-22 사용자가 '초안 채널 유지'를
  #                           택했으므로 스케줄 실행은 쓰지 않는다.**
  if [ "${2:-}" = "go" ]; then set -- "send" "draft" "${@:3}"; fi

  # [2026-09-22] **게이트는 경고만 남기고 발송을 막지 않는다.**
  #   사용자 선택(09-22): '경고만 남기고 무조건 발송'.
  #   게이트는 계속 전부 돌린다 - 끄지 않는다. 실패해도 멈추지만 않을 뿐
  #   결과는 로그에 남고, 10단계 보고와 푸시 알림에 실패한 게이트 이름을
  #   **반드시** 적어야 한다. 검토자가 없으므로 이 로그가 유일한 사후 추적이다.
  GATE_FAIL=""
  if [ "${2:-draft}" != "dry" ]; then
    (cd "$W" && python3 cowork/catcheck.py --date "$DATE" --stem "${3:-brief}") || GATE_FAIL="$GATE_FAIL cats"
    (cd "$W" && python3 "$MNT/cowork/verdict_spread.py" --date "$DATE") || GATE_FAIL="$GATE_FAIL spread(전수판정뭉침)"
    (cd "$W" && python3 cowork/newfact_check.py --date "$DATE") || GATE_FAIL="$GATE_FAIL newfact"
    (cd "$MNT" && python3 cowork/casebook.py --check) || GATE_FAIL="$GATE_FAIL case"
    (cd "$W" && python3 "$MNT/cowork/repeat_gate.py" --date "$DATE" --mnt "$MNT") || GATE_FAIL="$GATE_FAIL repeat"
    (cd "$W" && python3 cowork/agenda_gate.py --date "$DATE") || GATE_FAIL="$GATE_FAIL agenda"
    (cd "$W" && python3 cowork/bodypeek.py --date "$DATE" --check) || GATE_FAIL="$GATE_FAIL peek('고'본문미확인)"
    (cd "$W" && python3 "$MNT/cowork/style_check.py" --date "$DATE" --stem "${3:-brief}") || GATE_FAIL="$GATE_FAIL style(서술형종결)"
    if [ -n "$GATE_FAIL" ]; then
      echo "[send] **[경고] 게이트 실패:$GATE_FAIL — 그래도 발송합니다(2026-09-22 사용자 선택).**" >&2
      echo "[send]   보고·알림에 실패한 게이트 이름과 사유를 반드시 적을 것." >&2
    else
      echo "[send] 게이트 전부 통과"
    fi
  fi

  case "${2:-draft}" in
    dry)   cd "$W" && python3 cowork/send_telegram.py --date "$DATE" --dry-run --stem "${3:-brief}" ;;
    final) cd "$W" && python3 cowork/send_telegram.py --date "$DATE" --to final --stem "${3:-brief}" ;;
    *)     cd "$W" && python3 cowork/send_telegram.py --date "$DATE" --to draft --stem "${3:-brief}" ;;
  esac ;;

push)
  mkdir -p "$MNT/out" "$MNT/logs" "$MNT/index"
  cp "$W/out/"*"$DATE"* "$MNT/out/" 2>/dev/null
  cp "$W/logs/"*"$DATE"* "$MNT/logs/" 2>/dev/null
  # [주의] scorecard.jsonl 은 파일명에 날짜가 없어 위 글롭에 안 걸린다.
  #   따로 챙기지 않으면 세션이 끝날 때 누적 기록이 통째로 사라진다.
  cp "$W/logs/scorecard.jsonl" "$MNT/logs/" 2>/dev/null
  cp "$W"/truth-*.json "$MNT"/ 2>/dev/null
  # SQLite 는 마운트에서 열지 않고 '파일 복사'만 한다(읽기/쓰기 아님).
  cp "$W/index/articles.sqlite" "$MNT/$COWORK_DB" 2>/dev/null
  echo "[push] 반영된 산출물:"
  ls -la "$MNT/out/" | grep "$DATE"
  ;;

cloud-push)
  # [2026-10-15 클라우드 이관] push(작업본 -> 저장소 폴더) 뒤에 git 으로 되돌린다.
  #   마운트 폴더가 없는 클라우드 실행에서는 이것이 '그날 작업을 남기는' 유일한 길이다.
  #   .gitignore 가 bodycache/t2cache/secrets 를 거르므로 git add -A 로 충분하다.
  #   사용: bash cowork/run.sh cloud-push [커밋 메시지]
  bash "$MNT/cowork/run.sh" push >/dev/null 2>&1 || true
  cd "$MNT" || exit 1
  if [ ! -d .git ]; then echo "[cloud-push] git 저장소가 아닙니다: $MNT"; exit 1; fi
  git config user.email >/dev/null 2>&1 || git config user.email "dtb-bot@users.noreply.github.com"
  git config user.name  >/dev/null 2>&1 || git config user.name  "DTB bot"
  git add -A
  if git diff --cached --quiet; then echo "[cloud-push] 바뀐 것 없음"; exit 0; fi
  git commit -q -m "${2:-dtb $DATE}" && echo "[cloud-push] 커밋: $(git log -1 --format=%h) ${2:-dtb $DATE}"
  if git remote get-url origin >/dev/null 2>&1; then
    git push -q origin HEAD && echo "[cloud-push] push 완료 -> $(git remote get-url origin)" \
      || { echo "[cloud-push] [실패] push 가 안 됐습니다. 커밋은 남아 있으니 다시 시도하세요"; exit 1; }
  else
    echo "[cloud-push] origin 이 없어 커밋만 했습니다 (git remote add origin <url> 뒤 push)"
  fi ;;

cloud-prune)
  # [2026-10-15] 저장소 비대화 방지. out/ 의 90일 지난 큰 json(tier2·refined·ranked·prompt·timeline)을
  #   지운다. buzz 코퍼스는 D-40 까지만 읽고 persona 는 32일치만 읽으므로 90일이면 넉넉하다.
  #   brief-*.md·verdict·판정 엑셀·signals 같은 작은 기록은 지우지 않는다(hist·repeat 가 읽는다).
  #   사용: bash cowork/run.sh cloud-prune [일수=90]
  KEEP="${2:-90}"; CUT="$(TZ=Asia/Seoul date -d "$DATE - $KEEP days" +%F)"
  cd "$MNT/out" || exit 1
  n=0
  for f in tier2-*.json refined-*.json ranked-*.json prompt-*.txt timeline-*.md candidates-blind-*.txt linkcheck-*.json; do
    [ -e "$f" ] || continue
    d="$(printf '%s' "$f" | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}' | head -1)"
    [ -n "$d" ] && [[ "$d" < "$CUT" ]] && { rm -f "$f"; n=$((n+1)); }
  done
  echo "[cloud-prune] $CUT 이전 큰 산출물 $n 개 삭제 (기록용 brief/verdict/판정은 유지)" ;;

backtest)
  # 사람이 실제 발송한 브리프를 GitHub brief_final 에서 받아 자동 채점한다.
  # 사용: bash cowork/run.sh backtest [YYYY-MM-DD]   (생략 시 가장 최근)
  #
  # [2026-08-04] 채점 후 scorecard 에 자동 적재한다.
  #   그 전에는 결과가 stdout 에만 남아서, 세션이 바뀌면 어제 찾아낸 편향을
  #   오늘 다시 처음부터 판단해야 했다(실제로 08-03 -> 08-04 에 그랬다).
  cd "$W"
  if [ -n "${2:-}" ]; then
    python3 cowork/backtest.py --date "$2"; D="$2"
  else
    python3 cowork/backtest.py --latest
    # --latest 가 고른 날짜를 다시 알아낸다(가장 최근 truth 파일).
    D="$(ls -1 "$W"/truth-*.json 2>/dev/null | sed 's/.*truth-//;s/\.json//' \
         | sort | tail -1)"
  fi
  if [ -n "${D:-}" ]; then
    python3 cowork/scorecard.py --date "$D" --record
    # 사람이 열어볼 검증 리포트(엑셀). 손보정 전 값이므로, '?' 항목을 확인해
    # scorecard 로 손보정한 뒤 report 를 한 번 더 돌리면 수치가 갱신된다.
    python3 cowork/report.py --date "$D"
  fi
  echo
  python3 cowork/scorecard.py --trend --days 14 ;;

report)
  # 손보정 후 리포트만 다시 만들 때. 사용: bash cowork/run.sh report [YYYY-MM-DD]
  cd "$W" && python3 cowork/report.py --date "${2:-$DATE}" ;;

scorecard)
  # 손보정. 자동 판정이 못 잡은 '같은 사건' 과 '내가버림'의 세부 유형을 넣는다.
  # 사용:
  #   bash cowork/run.sh scorecard 2026-08-03 '{"event_hit_manual":12,
  #        "details":{"대한약사회":"국내 의료·공공데이터"}}'
  cd "$W" && python3 cowork/scorecard.py --date "${2:?날짜 필요}" \
             --annotate "${3:?패치 JSON 필요}" ;;

trend)
  # 최근 N일 추이. 사용: bash cowork/run.sh trend [일수]
  cd "$W" && python3 cowork/scorecard.py --trend --days "${2:-14}" ;;

keycheck)
  # 키가 제대로 읽혔는지, 그리고 실제로 네이버가 받아주는지 확인한다.
  # 키 값 자체는 절대 출력하지 않는다(길이와 앞 2글자만).
  if [ -z "${NAVER_CLIENT_ID:-}" ]; then
    echo "[keycheck] NG: cowork/secrets.env 가 없거나 NAVER_CLIENT_ID 줄이 없습니다."
    exit 1
  fi
  echo "[keycheck] ID   길이 ${#NAVER_CLIENT_ID} (앞 2글자 ${NAVER_CLIENT_ID:0:2}...)"
  echo "[keycheck] SECRET 길이 ${#NAVER_CLIENT_SECRET}"
  code=$(curl -s -o /tmp/nv.json -w "%{http_code}" -m 15 \
    -H "X-Naver-Client-Id: $NAVER_CLIENT_ID" \
    -H "X-Naver-Client-Secret: $NAVER_CLIENT_SECRET" \
    "https://openapi.naver.com/v1/search/news.json?query=AI&display=1")
  if [ "$code" = "200" ]; then
    echo "[keycheck] OK: 네이버 검색 API 정상 (HTTP 200)"
  else
    echo "[keycheck] NG: HTTP $code"
    head -c 200 /tmp/nv.json; echo
    echo "  401/403 이면 키가 틀렸거나 '검색' API 사용 설정이 안 된 것입니다."
  fi
  ;;

shadow)
  # [2026-10-08] 소스 정리 섀도 실험. RSS + 구글 쿼리 9개(config/shadow_exclude.yaml)를 뺀
  #   후보를 out/shadow/<날짜>/ 에 따로 만들고, 그날 발송본·'고'가 남는지 대조해
  #   out/shadow/log.jsonl 에 누적한다. 실제 refined/prompt/brief 는 건드리지 않는다.
  #   send 뒤에 돌린다(brief·verdict 가 있어야 대조된다). 5초 안팎.
  cp "$MNT/cowork/shadow_refine.py" "$W/cowork/" && cp "$MNT/config/shadow_exclude.yaml" "$W/config/"
  cd "$W" && python3 cowork/shadow_refine.py --date "$DATE" --base-dir "$W" --log-dir "$MNT/out/shadow"
  python3 - "$MNT/config/shadow_exclude.yaml" "$DATE" <<'PY'
import sys,yaml
e=yaml.safe_load(open(sys.argv[1],encoding='utf-8')).get('end_date','')
if sys.argv[2]>=e: print("[shadow] ** 실험 종료일(%s) 도달 — run.sh shadow-report 로 결과를 보고하고 제외 확정 여부를 사용자에게 물을 것 **" % e)
PY
  ;;

shadow-report)
  # 섀도 실험 누적 결과 요약(백테스트 9/17~10/8 포함).
  python3 - "$MNT/out/shadow/log.jsonl" <<'PY'
import sys,json
rows=[json.loads(l) for l in open(sys.argv[1],encoding='utf-8')]
def tot(k,f): return sum(r[k][f] if isinstance(r[k][f],int) else len(r[k][f]) for r in rows)
print("[shadow-report] %d일 (%s ~ %s)" % (len(rows), rows[0]['date'], rows[-1]['date']))
for k,nm in (('sent','발송본'),('go',"'고'")):
    print("  %s: 대조군 후보에 있던 %d건 중 섀도에서 사라짐 %d건" % (nm, tot(k,'in_base'), tot(k,'lost')))
    for r in rows:
        for t in r[k]['lost']: print("     - %s %s" % (r['date'], t[:60]))
ib=sum(sum(r['input_base'] or [0]) for r in rows); ic=sum(sum(r['input_cut'] or [0]) for r in rows)
print("  수집 입력 %d -> %d건 (-%.0f%%)" % (ib, ic, 100*(ib-ic)/max(ib,1)))
PY
  ;;

*)
  echo "사용법: bash cowork/run.sh {sync|slack-save|log|collect|tier0|newsclips-mail|newsclips|aibrief|samsung-mail|linkcheck|samsung-dump|samsung-search|samsung-show|samsung-apply|samsung-list|verify|tier2|tier2-done|refine|links|body|body-audit|brief|retitle|extend|blind|verdict|verdict-xlsx|insight|deals|push|send|backtest|scorecard|report|persona|trend|keycheck|repeat|reject-log|review}"
  exit 1 ;;
esac
