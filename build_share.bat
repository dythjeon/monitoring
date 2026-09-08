@echo off
REM 남에게 나눠줄 배포본을 만든다.
REM onefile 이 아니라 onedir 인 이유: onefile 은 실행할 때 자기 자신을
REM %TEMP% 에 풀고 그걸 실행해서, 백신이 드로퍼로 오탐하는 일이 잦다.
REM 본인이 쓰는 dist\BerthMonitor.exe(onefile)를 덮지 않도록 경로를 분리한다.
venv\Scripts\pyinstaller.exe --noconfirm --onedir --windowed --noupx ^
  --name BerthMonitor --icon=icon.ico ^
  --hidden-import=matplotlib.backends.backend_tkagg ^
  --distpath dist_share --workpath build_share main.py

REM --noconfirm 이 dist_share 폴더를 통째로 지우므로 안내문은 빌드 뒤에 넣는다.
copy /Y share_readme.txt "dist_share\BerthMonitor\읽어주세요.txt"

powershell -NoProfile -Command "$s=Get-Date -Format 'yyyyMMdd'; $o=\"$env:USERPROFILE\Desktop\BerthMonitor_$s.zip\"; if (Test-Path $o) { Remove-Item $o -Force }; Compress-Archive -Path '.\dist_share\BerthMonitor' -DestinationPath $o -CompressionLevel Optimal; Write-Host ('생성: ' + $o)"

echo.
echo 바탕화면에 BerthMonitor_날짜.zip 이 생성되었습니다.
pause
