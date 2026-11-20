# 사이트 갱신 깨우기.
# GitHub 의 예약 실행(cron)은 5분으로 적어도 실제로는 1시간에 한 번쯤만 돈다. 그래서 이 PC 의 작업 스케줄러가
# 5분마다 이 스크립트를 돌려 "지금 수집해"라는 신호(workflow_dispatch)만 보낸다. 수집 자체는 GitHub 에서 돈다.
# 로그인 정보는 따로 저장하지 않고, git 이 이미 기억하고 있는 GitHub 자격증명을 그때그때 꺼내 쓴다.
# 등록/해제: install_refresh_task.bat / schtasks /Delete /TN "OpsBoard Refresh" /F
$log = Join-Path $PSScriptRoot "trigger_refresh.log"
try {
    $env:GIT_TERMINAL_PROMPT = "0"
    $env:GCM_INTERACTIVE = "never"
    $remote = (& git -C $PSScriptRoot remote get-url origin).Trim()
    if ($remote -notmatch "github\.com[:/]+([^/]+)/(.+?)(\.git)?$") { throw "origin 주소를 알 수 없음: $remote" }
    $repo = $Matches[1] + "/" + $Matches[2]
    # 파워셸에서 바로 넘기면 줄 끝에 CR 이 붙어 git 이 못 읽는다. LF 로만 쓴 파일을 cmd 로 넘긴다.
    $tmp = [IO.Path]::GetTempFileName()
    [IO.File]::WriteAllText($tmp, "protocol=https`nhost=github.com`n`n")
    $out = & cmd.exe /c "git credential fill < `"$tmp`"" 2>$null
    Remove-Item $tmp -ErrorAction SilentlyContinue
    $token = ($out | Where-Object { $_ -like "password=*" } | Select-Object -First 1) -replace "^password=", ""
    if (-not $token) { throw "저장된 GitHub 로그인 정보가 없음" }
    $headers = @{ Authorization = "Bearer $token"; Accept = "application/vnd.github+json"; "User-Agent" = "opsboard-refresh" }
    Invoke-RestMethod -Method Post -Uri "https://api.github.com/repos/$repo/actions/workflows/update.yml/dispatches" `
        -Headers $headers -Body '{"ref":"main"}' -ContentType "application/json" -TimeoutSec 30 | Out-Null
    $msg = "ok $repo"
} catch {
    $msg = "FAIL " + $_.Exception.Message
}
$line = (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + " " + $msg
$keep = @()
if (Test-Path $log) { $keep = @(Get-Content $log -Tail 300) }
Set-Content -Path $log -Value ($keep + $line) -Encoding UTF8
