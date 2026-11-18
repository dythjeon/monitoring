@echo off
REM 인터넷에 올릴 파일 묶음(cloud 폴더)을 최신 원본으로 갱신한다.
REM scraper.py / server.py / index.html 을 고친 뒤 이걸 실행하고, cloud 폴더 내용을 GitHub 에 올리면 된다.
cd /d "%~dp0"
copy /y scraper.py cloud\ >nul
copy /y server.py cloud\ >nul
copy /y index.html cloud\ >nul
echo cloud 폴더 갱신 완료. 이 폴더의 파일들을 GitHub 저장소에 올리세요.
dir /b cloud
pause
