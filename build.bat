@echo off
pip install -r requirements.txt
pyinstaller --onefile --windowed --name BerthMonitor --icon=icon.ico --hidden-import=matplotlib.backends.backend_tkagg main.py
echo.
echo 완료되면 dist\BerthMonitor.exe 파일이 생성됩니다.
pause
