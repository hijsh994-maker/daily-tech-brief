# 최종 Daily Tech Brief 보관 - 평일 13:00 자동 실행 (작업 스케줄러 TIR-brief-archive)
#
# 텔레그램 '테크 뉴스 동향 테스트방'에 올라온 그날의 최종본을 읽어
#   - GitHub  slack-trend-backdata / brief_final/YYYYMMDD.md
#   - 로컬    news-crawler / brief-YYYY-MM-DD.txt   (compare.py 정답지)
# 두 곳에 저장한다. 브리프가 아직 없으면 아무 것도 저장하지 않고 끝난다.
#
# [주의] run_daily.ps1 과 같은 이유로 경로는 $PSScriptRoot 로 잡고,
#        python 출력은 Out-File -Encoding utf8 로 받는다(UTF-16 리다이렉트 금지).

$ErrorActionPreference = "Continue"
$root = $PSScriptRoot
Set-Location -LiteralPath $root

$py = "C:\Users\SKTelecom\AppData\Local\Python\pythoncore-3.14-64\python.exe"
$env:PYTHONIOENCODING = "utf-8"

$today = (Get-Date).ToString("yyyy-MM-dd")
$log = Join-Path $root "logs\brief-archive-$today.log"

"=== 브리프 보관 시작 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" |
    Out-File -LiteralPath $log -Encoding utf8

$out = & $py (Join-Path $root "archive_brief.py") --date $today 2>&1
$code = $LASTEXITCODE
$out | Out-File -LiteralPath $log -Append -Encoding utf8

switch ($code) {
    0 { "[{0}] 완료" -f (Get-Date -Format 'HH:mm:ss') }
    1 { "[{0}] 오늘자 브리프 없음 - 저장 생략" -f (Get-Date -Format 'HH:mm:ss') }
    2 { "[{0}] 텔레그램 세션 만료 - .telegram-sync\login.py 재실행 필요" -f (Get-Date -Format 'HH:mm:ss') }
    3 { "[{0}] git 실패 - 저장소 인증/충돌 확인 필요" -f (Get-Date -Format 'HH:mm:ss') }
    4 { "[{0}] 파싱 0건 - 브리프 형식이 바뀌었을 수 있음(archive_brief.py 파서 점검)" -f (Get-Date -Format 'HH:mm:ss') }
    default { "[{0}] 알 수 없는 종료코드 {1}" -f (Get-Date -Format 'HH:mm:ss'), $code }
} | Out-File -LiteralPath $log -Append -Encoding utf8
