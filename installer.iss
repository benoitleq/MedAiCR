; Installeur Windows pour MedAiCR (genere avec Inno Setup 6).
; Compiler : "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss

#define MyAppName "MedAiCR"
#define MyAppVersion "1.1.0"
#define MyAppPublisher "MedAiCR"
#define MyAppExe "MedAiCR.exe"

[Setup]
AppId={{B3F4B0C2-3A7E-4E2A-9C1D-1A2B3C4D5E6F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\MedAiCR
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer
OutputBaseFilename=MedAiCR_Setup_{#MyAppVersion}
SetupIconFile=anonymiseur.ico
UninstallDisplayIcon={app}\{#MyAppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Installation par utilisateur (pas besoin de droits administrateur),
; l'utilisateur peut choisir "tous les utilisateurs" s'il est administrateur.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; GroupDescription: "Raccourcis :"; Flags: checkedonce

[Files]
Source: "dist\MedAiCR.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "anonymiseur.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; IconFilename: "{app}\anonymiseur.ico"
Name: "{group}\Désinstaller {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; IconFilename: "{app}\anonymiseur.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "Lancer {#MyAppName} maintenant"; Flags: nowait postinstall skipifsilent
