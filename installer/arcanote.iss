; Installer di ArcaNote (Inno Setup 6). Installa per l'utente corrente, senza richiedere i diritti di amministratore.
#define AppName "ArcaNote"
#define AppVersion "1.0"

[Setup]
AppId={{6F1D2B7A-3C54-4E0B-9A8D-5B2E71C0A4F3}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=ArcaNote
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=ArcaNote-Setup
SetupIconFile=..\assets\arcanote.ico
UninstallDisplayIcon={app}\ArcaNote.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ShowLanguageDialog=no

[Languages]
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"

[Tasks]
Name: "desktopicon"; Description: "Crea un'icona sul desktop"; GroupDescription: "Collegamenti:"

[Files]
Source: "..\dist\ArcaNote.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\ArcaNote.exe"; Tasks: desktopicon
Name: "{group}\{#AppName}"; Filename: "{app}\ArcaNote.exe"
Name: "{group}\Disinstalla {#AppName}"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\ArcaNote.exe"; Description: "Avvia {#AppName}"; Flags: nowait postinstall skipifsilent
