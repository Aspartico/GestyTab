@echo off
rem Crea dist\GestyTab.exe (serve Python 3.9+ su Windows)
pip install -r requirements.txt pyinstaller || exit /b 1
pyinstaller --onefile --windowed --name GestyTab arca_search.py || exit /b 1
echo Fatto: dist\GestyTab.exe
