; Inno Setup script for the Decision Agent installer (DecisionAgent-Setup.exe).
; Build the exe first (pyinstaller packaging\decision_agent.spec), then:
;   iscc /DAppVersion=0.2.0 packaging\installer.iss
; Output: dist\DecisionAgent-Setup.exe
; Only dist\DecisionAgent is packed: no API key, no demo data, no user information.

#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif
#define AppName "Decision Agent"
#define AppExe "DecisionAgent.exe"
#define RunKey "Software\Microsoft\Windows\CurrentVersion\Run"
#define WebView2Client "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
#define WebView2Bootstrapper "https://go.microsoft.com/fwlink/p/?LinkId=2124703"

[Setup]
AppId={{8F3A6C2E-5B1D-4C7A-9E2F-1D6B0A4C3E71}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Decision Agent
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} Setup
DefaultDirName={autopf}\DecisionAgent
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Per-user install by default (no administrator rights needed); the user may switch to all users.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=DecisionAgent-Setup
SetupIconFile=..\src\decision_agent\desktop\static\app.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; The running program holds this mutex, so setup and uninstall ask to close it first.
AppMutex=DecisionAgentSingleInstance
CloseApplications=yes

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
korean.DesktopIcon=바탕화면에 바로가기 만들기
korean.Autostart=Windows 시작 시 자동 실행 (트레이에서 조용히 감시)
korean.Extra=추가 작업:
korean.LaunchApp=Decision Agent 실행
korean.WebView2Ask=이 컴퓨터에 Microsoft Edge WebView2 런타임이 없습니다.%n%nDecision Agent의 창을 표시하는 데 필요합니다. Microsoft에서 내려받아 지금 설치할까요?%n%n설치하지 않으면 프로그램이 기본 브라우저에서 열립니다.
korean.WebView2Failed=WebView2 런타임을 설치하지 못했습니다. 프로그램은 기본 브라우저에서 열립니다. 나중에 Microsoft 웹사이트에서 WebView2 런타임을 설치할 수 있습니다.
korean.RemoveData=앱 데이터도 삭제할까요?%n%n설정, 점검 결과, 메모, 그리고 Windows 자격 증명 관리자에 저장된 API 키와 연결 비밀 값을 지웁니다.%n감시 폴더의 문서 파일은 삭제하지 않습니다.%n%n[아니요]를 누르면 다시 설치했을 때 그대로 이어서 쓸 수 있습니다.
english.DesktopIcon=Create a desktop shortcut
english.Autostart=Start automatically when Windows starts (watch quietly from the tray)
english.Extra=Additional tasks:
english.LaunchApp=Launch Decision Agent
english.WebView2Ask=Microsoft Edge WebView2 Runtime is not installed.%n%nIt is needed to show the Decision Agent window. Download it from Microsoft and install it now?%n%nWithout it the program opens in the default browser.
english.WebView2Failed=Could not install the WebView2 Runtime. The program will open in the default browser.
english.RemoveData=Also delete the app data?%n%nThis removes settings, results, notes, and the API key and connection secrets stored in Windows Credential Manager.%nDocuments in the watched folders are never deleted.

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; GroupDescription: "{cm:Extra}"; Flags: unchecked
Name: "autostart"; Description: "{cm:Autostart}"; GroupDescription: "{cm:Extra}"

[Files]
Source: "..\dist\DecisionAgent\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; Same value the program writes from its own settings (Autostart class): current user only.
Root: HKCU; Subkey: "{#RunKey}"; ValueType: string; ValueName: "DecisionAgent"; \
  ValueData: """{app}\{#AppExe}"" run --background"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchApp}"; Flags: nowait postinstall skipifsilent

[Code]
function WebView2Installed(): Boolean;
var
  Version: String;
begin
  Result := False;
  if RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{#WebView2Client}', 'pv', Version) or
     RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{#WebView2Client}', 'pv', Version) or
     RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{#WebView2Client}', 'pv', Version) then
    Result := (Version <> '') and (Version <> '0.0.0.0');
end;

procedure InstallWebView2();
var
  ResultCode: Integer;
  Setup: String;
begin
  try
    DownloadTemporaryFile('{#WebView2Bootstrapper}', 'MicrosoftEdgeWebview2Setup.exe', '', nil);
    Setup := ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe');
    if not Exec(Setup, '/silent /install', '', SW_SHOW, ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
      MsgBox(CustomMessage('WebView2Failed'), mbInformation, MB_OK);
  except
    MsgBox(CustomMessage('WebView2Failed'), mbInformation, MB_OK);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and not WebView2Installed() and not WizardSilent() then
  begin
    if MsgBox(CustomMessage('WebView2Ask'), mbConfirmation, MB_YESNO) = IDYES then
      InstallWebView2();
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    { The program may have turned on autostart itself; never leave a Run entry to a removed exe. }
    RegDeleteValue(HKCU, '{#RunKey}', 'DecisionAgent');
    if not UninstallSilent() then
      if MsgBox(CustomMessage('RemoveData'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        Exec(ExpandConstant('{app}\{#AppExe}'), 'reset-data --yes', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;
