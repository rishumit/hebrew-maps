; installer\hebrew-maps.iss
; Inno Setup 6 script for Hebrew Maps / מפות בעברית
;
; Compile from project root:
;   iscc installer\hebrew-maps.iss
; or via build.ps1 which passes /DMyAppVersion=x.y.z

#define MyAppName      "מפות בעברית"
#define MyAppNameEn    "HebrewMaps"
#define MyAppVersion   GetEnv("MyAppVersion")
#if MyAppVersion == ""
  #define MyAppVersion "1.0.0"
#endif
#define MyAppPublisher "Hebrew Maps"
#define MyAppURL       "http://localhost:8080"
#define MyAppExeName   "hebrew-maps.exe"

[Setup]
AppId={{6F8A3C2E-1D4B-4E7A-9F0C-HEBREW0MAPS1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppNameEn}
DefaultGroupName={#MyAppName}
OutputBaseFilename=HebrewMaps-Setup-{#MyAppVersion}
OutputDir=.
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
PrivilegesRequired=lowest
WizardStyle=modern
Uninstallable=yes
ShowLanguageDialog=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "hebrew";  MessagesFile: "compiler:Languages\Hebrew.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
; PyInstaller bundle — EXE + _internal\ tree
; build.ps1 passes --distpath installer\dist so the bundle lands here:
; The .iss is compiled with its directory as CWD, so "dist\" is relative to installer\
Source: "dist\hebrew-maps\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; Web assets (HTML, JS, CSS, fonts, icons)
Source: "..\web\*"; DestDir: "{app}\web"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

; Data — current map pointer file
Source: "..\data\maps.current"; DestDir: "{app}\data"; Flags: ignoreversion skipifsourcedoesntexist

; Data — map databases (wildcard matches any dated build)
Source: "..\data\maps-*.db"; DestDir: "{app}\data"; Flags: ignoreversion skipifsourcedoesntexist

; Data — routing graph
Source: "..\data\routing\*"; DestDir: "{app}\data\routing"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

; Data — map tiles
Source: "..\data\tiles\*"; DestDir: "{app}\data\tiles"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

; Data — DEM / terrain elevation
Source: "..\data\dem\*"; DestDir: "{app}\data\dem"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

; Data — public transit (GTFS)
Source: "..\data\transit\*"; DestDir: "{app}\data\transit"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

; Data — media / images
Source: "..\data\media\*"; DestDir: "{app}\data\media"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

; Data — sample database (always present)
Source: "..\data\sample\*"; DestDir: "{app}\data\sample"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

[Dirs]
; Create an empty reports directory so the app can write quality reports
Name: "{app}\data\reports"

[Icons]
Name: "{group}\{#MyAppName}";           Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}";     Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "taskkill.exe"; Parameters: "/F /IM {#MyAppExeName}"; Flags: runhidden skipifdoesntexist

[Code]
function InitializeSetup(): Boolean;
begin
  if not IsWin64 then
  begin
    MsgBox(
      'This application requires a 64-bit version of Windows 10 or later.' + #13#10 +
      'האפליקציה דורשת Windows 64-bit (גרסה 10 ומעלה).',
      mbError, MB_OK
    );
    Result := False;
    Exit;
  end;
  Result := True;
end;
