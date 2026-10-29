@echo off
rem Crea dist\ArcaNote.exe (serve Python 3.9+ su Windows)
pip install -r requirements.txt pyinstaller || exit /b 1
pyinstaller --onefile --windowed --name ArcaNote arcanote.py || exit /b 1
echo Fatto: dist\ArcaNote.exe
