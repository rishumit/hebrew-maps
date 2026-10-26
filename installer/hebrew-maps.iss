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
; Version: installer.yml passes the tag's version (/DMyAppVersion=1.2.0) after writing it into
; hmaps/__init__.py, tauri.conf.json and Cargo.toml (tools/set_version.py) – one version everywhere.
;
; Unattended update from inside the program (hmaps/api/appupdate.py):
;   HebrewMaps-Setup-x.y.z.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS
;     /FORCECLOSEAPPLICATIONS /NOCANCEL /LOG=<file> /AUTOUPDATE=1
; PrepareToInstall closes the window and the server (gracefully, then forcibly) so no file is
; locked, the program files are replaced, and /AUTOUPDATE=1 starts the program again at the end.
; The data in {app}\data (downloaded packages, components.json, the aerial-photo cache) is never
; touched: data files that ship with a local build are only copied when they do not exist yet.
;
; Desktop app: if build.ps1 / CI put the Tauri exe (hebrew-maps-desktop.exe) into
; dist\hebrew-maps\, the shortcuts start it (native window; it starts hebrew-maps.exe
; itself, with --no-browser). Without it – or on a PC without the WebView2 runtime –
; the shortcuts start hebrew-maps.exe, which opens the browser as before.

#define MyAppName      "מפות בעברית"
#define MyAppNameEn    "HebrewMaps"
; build.ps1 / installer.yml pass /DMyAppVersion=x.y.z (= hmaps.__version__)
#ifndef MyAppVersion
  #define MyAppVersion "1.1.0"
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
; Upgrades: files of the running program are closed (Restart Manager), never a reboot; the
; program is started again by the [Run] entries below when /AUTOUPDATE=1 is given.
CloseApplications=force
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no
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

; Data shipped with a local build (CI builds ship none: data\ is not in git). Upgrades must
; never replace what the user downloaded since (maps.current would point back to an old
; database) – onlyifdoesntexist everywhere below, except the sample database of the app.

; Data — current map pointer file
Source: "..\data\maps.current"; DestDir: "{app}\data"; Flags: onlyifdoesntexist skipifsourcedoesntexist

; Data — map databases (wildcard matches any dated build)
Source: "..\data\maps-*.db"; DestDir: "{app}\data"; Flags: onlyifdoesntexist skipifsourcedoesntexist

; Data — routing graph
Source: "..\data\routing\*"; DestDir: "{app}\data\routing"; Flags: onlyifdoesntexist recursesubdirs createallsubdirs skipifsourcedoesntexist

; Data — map tiles
Source: "..\data\tiles\*"; DestDir: "{app}\data\tiles"; Flags: onlyifdoesntexist recursesubdirs createallsubdirs skipifsourcedoesntexist

; Data — DEM / terrain elevation
Source: "..\data\dem\*"; DestDir: "{app}\data\dem"; Flags: onlyifdoesntexist recursesubdirs createallsubdirs skipifsourcedoesntexist

; Data — public transit (GTFS)
Source: "..\data\transit\*"; DestDir: "{app}\data\transit"; Flags: onlyifdoesntexist recursesubdirs createallsubdirs skipifsourcedoesntexist

; Data — media / images
Source: "..\data\media\*"; DestDir: "{app}\data\media"; Flags: onlyifdoesntexist recursesubdirs createallsubdirs skipifsourcedoesntexist

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
; in-app update (/AUTOUPDATE=1, always silent): open the program again
Filename: "{app}\{#MyDesktopExe}"; Flags: nowait; Check: IsAutoUpdate and HasWebView2
Filename: "{app}\{#MyAppExeName}"; Flags: nowait; Check: IsAutoUpdate and not HasWebView2
#else
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
Filename: "{app}\{#MyAppExeName}"; Flags: nowait; Check: IsAutoUpdate
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

{ /AUTOUPDATE=1 on the command line: started by the program's own updater (appupdate.py). }
function IsAutoUpdate(): Boolean;
begin
  Result := ExpandConstant('{param:AUTOUPDATE|0}') = '1';
end;

{ Upgrades: running copies (the desktop app and its server, or an older browser-mode
  server) lock their files – stop them before copying. First a normal close (the window
  closes and stops its server), then by force whatever is left; the installer itself was
  started outside the window's Job Object, so it survives this. No /T: an installer started
  by the server is its child and must not be killed with the tree. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Exec('taskkill.exe', '/IM {#MyDesktopExe}', '', SW_HIDE, ewWaitUntilTerminated, Code);
  if Code = 0 then
    Sleep(2000);
  Exec('taskkill.exe', '/F /IM {#MyDesktopExe}', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec('taskkill.exe', '/F /IM {#MyAppExeName}', '', SW_HIDE, ewWaitUntilTerminated, Code);
  { give Windows a moment to release the file handles }
  Sleep(500);
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
