@echo off
rem 5분마다 사이트 갱신을 깨우는 작업을 윈도우 작업 스케줄러에 등록한다 (이 PC 가 켜져 있고 로그인돼 있을 때만 동작).
rem 해제:  schtasks /Delete /TN "OpsBoard Refresh" /F
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$a = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('\"' + '%~dp0trigger_refresh.vbs' + '\"');" ^
  "$t = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 5);" ^
  "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2);" ^
  "Register-ScheduledTask -TaskName 'OpsBoard Refresh' -Action $a -Trigger $t -Settings $s -Force | Out-Null; 'registered'"
pause
