# Daily Tech Brief - 당일 파이프라인 (매일 09:35 자동 실행)
#
# [운영 모드 B] API 결제 없이 Claude Pro 로 운영.
#   collect~refine~프롬프트 생성까지 자동, 완성 프롬프트를 클립보드에 복사.
#   사람은 claude.ai(Pro) 에 Ctrl+V 로 붙여 초안을 받고 검토 후 발송.
#
# [주의] PowerShell 5.1 은 UTF-8 스크립트의 한글 경로 리터럴을 깨뜨린다.
#        그래서 경로를 하드코딩하지 않고 $PSScriptRoot(스크립트 위치)로 잡는다.
#        python 출력도 UTF-16 리다이렉트(*>>) 대신 Out-File utf8 로 받는다.

$ErrorActionPreference = "Continue"
$root = $PSScriptRoot
Set-Location -LiteralPath $root

$py = "C:\Users\SKTelecom\AppData\Local\Python\pythoncore-3.14-64\python.exe"
$env:PYTHONIOENCODING = "utf-8"
$env:NAVER_CLIENT_ID = [Environment]::GetEnvironmentVariable('NAVER_CLIENT_ID', 'User')
$env:NAVER_CLIENT_SECRET = [Environment]::GetEnvironmentVariable('NAVER_CLIENT_SECRET', 'User')
# ANTHROPIC_API_KEY 가 있으면 brief.py 가 초안까지 생성(모드 A), 없으면 프롬프트만(모드 B).
$env:ANTHROPIC_API_KEY = [Environment]::GetEnvironmentVariable('ANTHROPIC_API_KEY', 'User')

$today = (Get-Date).ToString("yyyy-MM-dd")
$log = Join-Path $root "logs\daily-$today.log"

function Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $msg
    $line | Out-File -LiteralPath $log -Append -Encoding utf8
}

# 로그 새로 시작
"=== 당일 파이프라인 시작 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" |
    Out-File -LiteralPath $log -Encoding utf8

# 각 단계: python 자체 로그도 남기지만, 요약은 여기로. 표준출력은 각 스크립트가
# 자체 UTF-8 처리하므로 여기서는 종료코드만 확인하고 단계 완료를 기록한다.
$steps = @(
    @("ingest_tier0.py", @("--date", $today, "--window", "1d")),
    @("ingest_tier2.py", @("--date", $today, "--window", "1d")),
    # max-items 160 = 링크 해결까지 하는 '주 후보'. 여기에 더해 refine 이
    # '구제 후보'(컷 밖이지만 이미 링크를 가진 저점 항목)를 150건까지 함께 낸다.
    # 구제 후보는 링크 해결을 생략하므로 추가 비용이 0이고, 기계 점수가 낮게
    # 나온 중요 기사(은유적 제목·키워드 없는 정책)를 LLM 이 건져낼 수 있게 한다.
    #
    # [80 -> 160 상향, 2026-07-31] 임계(6점)를 넘는 클러스터가 날마다 120~230건인데
    # 80 에서 잘라 왔다. 그래서 8점짜리가 93위라는 이유로 프롬프트에 아예 안 실렸다
    # (4일 실측: 임계 통과 항목의 1/6~1/4 가 이 지점에서 증발). 링크 해결이 늘어
    # refine 이 17초 -> 38초가 되지만 전체 창(09:35)에는 여유가 충분하다.
    @("refine.py",       @("--date", $today, "--min-score", "6", "--max-items", "160",
                           "--max-rescue", "150")),
    # LLM 이 후보 중 30건을 선별 + 문장 작성. 사람이 검토하며 더 줄인다.
    @("brief.py",        @("--date", $today, "--target", "30"))
)
foreach ($s in $steps) {
    Log ("실행: {0} {1}" -f $s[0], ($s[1] -join ' '))
    $out = & $py (Join-Path $root $s[0]) @($s[1]) 2>&1
    $out | Out-File -LiteralPath $log -Append -Encoding utf8
    Log ("완료: {0} (exit {1})" -f $s[0], $LASTEXITCODE)
}

# 완성 프롬프트를 클립보드에 복사 (모드 B: 붙여넣기만 하면 됨)
$prompt = Join-Path $root "out\prompt-$today.txt"
if (Test-Path -LiteralPath $prompt) {
    Get-Content -LiteralPath $prompt -Raw -Encoding utf8 | Set-Clipboard
    Log "프롬프트를 클립보드에 복사함. claude.ai 에 Ctrl+V 하세요."
    "오늘($today) 브리프 프롬프트가 준비됐습니다. claude.ai 에 붙여넣으세요." |
        Out-File -LiteralPath (Join-Path $root "out\READY-$today.txt") -Encoding utf8
} else {
    Log "경고: 프롬프트 파일이 생성되지 않음 ($prompt)"
}
Log "=== 파이프라인 종료 ==="
