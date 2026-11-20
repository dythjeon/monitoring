' trigger_refresh.ps1 을 창 없이 실행한다 (작업 스케줄러가 5분마다 부른다. 검은 창이 번쩍이지 않게).
Dim fso, dir
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
CreateObject("WScript.Shell").Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & dir & "\trigger_refresh.ps1""", 0, False
