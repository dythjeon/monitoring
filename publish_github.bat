@echo off
chcp 65001 >nul
cd /d "%~dp0"
REM 수정 사항을 GitHub 에 올린다. 원격 저장소가 없으면 주소를 물어 등록한다.
git remote get-url origin >nul 2>&1
if errorlevel 1 (
  set /p REPO=GitHub 저장소 주소(예: https://github.com/아이디/monitoring.git): 
  git remote add origin %REPO%
)
git add -A
git commit -m "update" >nul 2>&1
git branch -M main
git push -u origin main
echo.
echo 완료. 저장소의 Actions 탭에서 실행 상태를, Settings - Pages 에서 주소를 확인하세요.
pause
