@echo off
chcp 65001 >nul
cd /d "%~dp0"
REM Barge 요율 엑셀을 읽어 사이트 비밀번호로 암호화한 뒤 GitHub 에 올린다.
REM 엑셀 내용이 바뀌었거나 사이트 비밀번호를 바꿨을 때 다시 실행하면 된다.
venv\Scripts\python.exe make_tariff.py
if errorlevel 1 (
  echo.
  echo 실패했습니다. 위 메시지를 확인해 주세요.
  pause
  exit /b 1
)
git add tariff.enc.json
git commit -m "Barge 요율 갱신" >nul 2>&1
git push origin main
echo.
echo 완료. 5분쯤 뒤 사이트의 Barge Tariff 탭에서 확인하세요.
pause
