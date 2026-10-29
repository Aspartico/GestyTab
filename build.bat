@echo off
rem Crea dist\ArcaNote.exe (portable) e, se Inno Setup 6 e' installato, dist\ArcaNote-Setup.exe (installer)
pip install -r requirements.txt pyinstaller || exit /b 1
pyinstaller --onefile --windowed --name ArcaNote --icon assets\arcanote.ico --add-data "assets\arcanote.ico;assets" arcanote.py || exit /b 1
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" installer\arcanote.iss
echo Fatto: cartella dist
