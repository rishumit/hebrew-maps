; installer\hebrew-maps.iss
; Inno Setup 6 script for Hebrew Maps / מפות בעברית
;
; Compile from project root:
;   iscc installer\hebrew-maps.iss
;   or via build.ps1 which passes /DMyAppVersion=x.y.z
;
; Saved as UTF-8 *with BOM* – Inno Setup reads a file without BOM as ANSI and would
; garble the Hebrew strings.
;
; Desktop app: if build.ps1 / CI put the Tauri exe (hebrew-maps-desktop.exe) into
; dist\hebrew-maps\, the shortcuts start it (native window; it starts hebrew-maps.exe
; itself, with --no-browser). Without it – or on a PC without the WebView2 runtime –
; the shortcuts start hebrew-maps.exe, which opens the browser as before.

#define MyAppName      "מפות בעברית"
#define MyAppNameEn    "HebrewMaps"
; build.ps1 passes /DMyAppVersion=x.y.z; default to 1.0.0 otherwise
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define MyAppPublisher "Hebrew Maps"
#define MyAppURL       "http://localhost:8080"
#define MyAppExeName   "hebrew-maps.exe"
#define MyDesktopExe   "hebrew-maps-desktop.exe"
#if FileExists(AddBackslash(SourcePath) + "dist\hebrew-maps\" + MyDesktopExe)
  #define HasDesktop
#endif

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
UninstallDisplayIcon={app}\{#MyAppExeName}
#if FileExists(AddBackslash(SourcePath) + "icon.ico")
SetupIconFile=icon.ico
#endif

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
#ifdef HasDesktop
; Native window (Tauri) when the WebView2 runtime is present (always on Windows 11)
Name: "{group}\{#MyAppName}";           Filename: "{app}\{#MyDesktopExe}"; Check: HasWebView2
Name: "{autodesktop}\{#MyAppName}";     Filename: "{app}\{#MyDesktopExe}"; Tasks: desktopicon; Check: HasWebView2
Name: "{group}\{#MyAppName}";           Filename: "{app}\{#MyAppExeName}"; Check: not HasWebView2
Name: "{autodesktop}\{#MyAppName}";     Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; Check: not HasWebView2
#else
Name: "{group}\{#MyAppName}";           Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}";     Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
#endif

[Run]
#ifdef HasDesktop
Filename: "{app}\{#MyDesktopExe}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent; Check: HasWebView2
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent; Check: not HasWebView2
#else
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
#endif

[UninstallRun]
Filename: "taskkill.exe"; Parameters: "/F /IM {#MyDesktopExe}"; Flags: runhidden; RunOnceId: "KillDesktop"
Filename: "taskkill.exe"; Parameters: "/F /IM {#MyAppExeName}"; Flags: runhidden; RunOnceId: "KillServer"

[Code]
const
  WebView2Key = '\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function WebView2VersionOk(RootKey: Integer; SubKey: String): Boolean;
var
  V: String;
begin
  Result := False;
  if RegQueryStringValue(RootKey, SubKey, 'pv', V) then
    Result := (V <> '') and (V <> '0.0.0.0');
end;

{ Microsoft Edge WebView2 runtime – needed by the desktop window. Preinstalled on
  Windows 11 and on updated Windows 10; otherwise the browser is used. }
function HasWebView2(): Boolean;
begin
  Result := WebView2VersionOk(HKLM, 'SOFTWARE\WOW6432Node' + WebView2Key)
    or WebView2VersionOk(HKLM, 'SOFTWARE' + WebView2Key)
    or WebView2VersionOk(HKCU, 'Software' + WebView2Key);
end;

{ Upgrades: running copies (the desktop app and its server, or an older browser-mode
  server) lock their files – stop them before copying. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Exec('taskkill.exe', '/F /IM {#MyDesktopExe}', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec('taskkill.exe', '/F /IM {#MyAppExeName}', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := '';
end;

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
