@echo off
REM 직원 PC에서 접속할 수 있게 Windows 방화벽에 8790 포트를 연다 (같은 사내망에서만 허용).
REM 이 파일을 마우스 오른쪽 클릭 -> "관리자 권한으로 실행" 해야 한다. 한 번만 하면 된다.
chcp 65001 >nul
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo.
  echo  관리자 권한이 필요합니다. 이 파일을 마우스 오른쪽 클릭 -^> "관리자 권한으로 실행" 하세요.
  echo.
  pause
  exit /b 1
)
powershell -NoProfile -Command "if (Get-NetFirewallRule -DisplayName 'BerthMonitor Web 8790' -ErrorAction SilentlyContinue) { Write-Host '이미 열려 있습니다.' } else { New-NetFirewallRule -DisplayName 'BerthMonitor Web 8790' -Direction Inbound -Protocol TCP -LocalPort 8790 -RemoteAddress LocalSubnet -Action Allow | Out-Null; Write-Host '방화벽 8790 포트를 열었습니다.' }"
echo.
echo  이제 직원들은  http://203.228.232.205:8790  로 접속할 수 있습니다 (이 PC에서 run_web.bat 이 켜져 있을 때).
echo.
pause
