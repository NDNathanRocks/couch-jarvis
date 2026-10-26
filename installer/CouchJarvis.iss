; Inno Setup script for {#AppName}. Build with installer\build.ps1 (needs Inno Setup 6).
; Per-user install: no administrator rights, nothing is written outside the user profile.

#define AppName "Armrest"
; The 0.x name: its shortcuts are removed on update. Internal names (exe, folders, AppId) never change.
#define OldAppName "Couch Jarvis"

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{7B1E3C52-5A9D-4B5E-9C1A-2F4D8E6A0C31}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppName}
DefaultDirName={autopf}\CouchJarvis
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=CouchJarvis-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName}
CloseApplications=force

[InstallDelete]
; 0.x shortcuts used the old name; the new ones replace them (the app, its data and the AppId are unchanged)
Type: files; Name: "{autoprograms}\{#OldAppName}.lnk"
Type: files; Name: "{autoprograms}\{#OldAppName} settings.lnk"
Type: files; Name: "{autodesktop}\{#OldAppName}.lnk"

[Tasks]
Name: "startup"; Description: "Start {#AppName} when I sign in to Windows"; GroupDescription: "Startup:"
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "dist\CouchJarvis\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\scripts\setup_brain.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\CouchJarvis.exe"
Name: "{autoprograms}\{#AppName} settings"; Filename: "{app}\CouchJarvis.exe"; Parameters: "--open-settings"; Comment: "Open your settings file (add your own Groq or Gemini key here)"
Name: "{autoprograms}\Set up AI brain"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\setup_brain.ps1"" -Pause"; Comment: "Download the local AI model that understands free-form commands (about 2-8 GB)"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\CouchJarvis.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "CouchJarvis"; ValueData: """{app}\CouchJarvis.exe"""; Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\CouchJarvis.exe"; Flags: nowait runasoriginaluser; Check: RelaunchAfterSilentUpdate
Filename: "{app}\CouchJarvis.exe"; Description: "Start {#AppName} now (a pairing QR code appears on screen)"; Flags: nowait postinstall skipifsilent
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\setup_brain.ps1"" -PauseOnError"; Description: "Download the local AI brain now (Ollama and a model, 2-8 GB; skip it if you entered a Gemini key)"; Flags: nowait postinstall skipifsilent; Check: OfferBrainDownload

[UninstallRun]
Filename: "taskkill.exe"; Parameters: "/im CouchJarvis.exe /f"; Flags: runhidden; RunOnceId: "StopCouchJarvis"

[Code]
const
  { The AppId above, as Windows stores it. NEVER change it: it is how every later installer
    recognises an existing install and updates it in place (plan.md section 12). }
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{7B1E3C52-5A9D-4B5E-9C1A-2F4D8E6A0C31}_is1';

var
  KeyPage: TInputQueryWizardPage;
  PreviousVersion: String;  { empty on a first install }

function InstalledVersion: String;
begin
  Result := '';
  if not RegQueryStringValue(HKCU, UninstallKey, 'DisplayVersion', Result) then
    if not RegQueryStringValue(HKLM, UninstallKey, 'DisplayVersion', Result) then
      Result := '';
end;

{ -1: A is older than B, 0: same (or unreadable), 1: A is newer }
function CompareVersions(const A, B: String): Integer;
var
  PackedA, PackedB: Int64;
begin
  if not (StrToVersion(A, PackedA) and StrToVersion(B, PackedB)) then
    Result := 0
  else
    Result := ComparePackedVersion(PackedA, PackedB);
end;

function Updating: Boolean;
begin
  Result := PreviousVersion <> '';
end;

function NoGeminiKey: Boolean;
begin
  Result := Trim(KeyPage.Values[0]) = '';
end;

{ Only a first install offers the local brain download; an update keeps what is already set up. }
function OfferBrainDownload: Boolean;
begin
  Result := (not Updating) and NoGeminiKey;
end;

function RelaunchAfterSilentUpdate: Boolean;
begin
  Result := Updating and WizardSilent;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := Updating and (PageID = KeyPage.ID);  { settings (and any key) are kept as they are }
end;

procedure InitializeWizard;
begin
  KeyPage := CreateInputQueryPage(wpSelectTasks, 'AI brain (optional)',
    'A free Gemini key makes free-form commands fast and needs no big download.',
    'Get a free key at aistudio.google.com/apikey and paste it here, or leave this empty to ' +
    'download a local AI model after installing (several GB). You can change it later in ' +
    'Start menu > {#AppName} settings.');
  KeyPage.Add('Gemini API key (optional):', False);
end;

procedure SaveGeminiKey;
var
  Config, Key: String;
  Lines, Kept: TArrayOfString;
  I, N: Integer;
begin
  Key := Trim(KeyPage.Values[0]);
  if Key = '' then Exit;
  Config := ExpandConstant('{userappdata}\CouchJarvis\config.env');
  ForceDirectories(ExpandConstant('{userappdata}\CouchJarvis'));
  { keep the user's settings, else start from the shipped defaults (relay and phone-app addresses) }
  if not LoadStringsFromFile(Config, Lines) then
    if not LoadStringsFromFile(ExpandConstant('{app}\_internal\installer\defaults.env'), Lines) then
      SetArrayLength(Lines, 0);
  { whole lines only, and any earlier key is replaced (a reinstall must not add a second one) }
  SetArrayLength(Kept, GetArrayLength(Lines) + 1);
  N := 0;
  for I := 0 to GetArrayLength(Lines) - 1 do
    if Pos('CJ_GEMINI_API_KEY=', Trim(Lines[I])) <> 1 then
    begin
      Kept[N] := Lines[I];
      N := N + 1;
    end;
  Kept[N] := 'CJ_GEMINI_API_KEY=' + Key;
  SetArrayLength(Kept, N + 1);
  SaveStringsToUTF8FileWithoutBOM(Config, Kept, False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then SaveGeminiKey;
end;

function EdgeInstalled: Boolean;
begin
  Result := RegKeyExists(HKLM, 'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe')
         or RegKeyExists(HKCU, 'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe');
end;

function CouchJarvisRunning: Boolean;
var
  Code: Integer;
begin
  Result := Exec(ExpandConstant('{cmd}'),
    '/c tasklist /fi "imagename eq CouchJarvis.exe" /nh | find /i "CouchJarvis.exe" >nul',
    '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
end;

{ A supervisor can start a fresh copy between one kill and the next, so kill until none is left. }
procedure StopCouchJarvis;
var
  Code, Tries: Integer;
begin
  for Tries := 1 to 10 do
  begin
    Exec('taskkill.exe', '/im CouchJarvis.exe /f /t', '', SW_HIDE, ewWaitUntilTerminated, Code);
    Sleep(1000);
    if not CouchJarvisRunning then Break;
  end;
  Sleep(500);  { let Windows release the files }
end;

{ Runs right before Setup checks for files in use: a copy started since InitializeSetup would
  make that check give up, and a silent update would then abort. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if PreviousVersion <> '' then StopCouchJarvis;
end;

function InitializeSetup: Boolean;
var
  Order: Integer;
begin
  Result := True;
  PreviousVersion := InstalledVersion;
  if PreviousVersion <> '' then
  begin
    Order := CompareVersions(PreviousVersion, '{#AppVersion}');
    if Order > 0 then
      Result := SuppressibleMsgBox('{#AppName} ' + PreviousVersion + ' is already installed, which is newer ' +
                  'than this installer ({#AppVersion}). Install the older version over it?',
                  mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES
    else if Order = 0 then
      Result := SuppressibleMsgBox('{#AppName} ' + PreviousVersion + ' is already installed. Reinstall it?',
                  mbConfirmation, MB_YESNO, IDYES) = IDYES
    else
      Result := SuppressibleMsgBox('{#AppName} (formerly {#OldAppName}) ' + PreviousVersion +
                  ' is installed. This will update it to {#AppVersion}.' + #13#10#13#10 +
                  'Your settings and paired phones are kept. {#AppName} ' +
                  'is closed for a moment and started again afterwards.',
                  mbInformation, MB_OKCANCEL, IDOK) = IDOK;
    { The app is a background process with no window Windows could ask to close, so Setup's own
      "close applications" step would give up. Stop it (and the daemon it started) here, first. }
    if Result then StopCouchJarvis;
  end
  else if not EdgeInstalled then
    Result := SuppressibleMsgBox('Microsoft Edge was not found. {#AppName} drives Edge to play your media, so ' +
                     'install Edge first.' + #13#10#13#10 + 'Install {#AppName} anyway?',
                     mbConfirmation, MB_YESNO, IDYES) = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    { suppressible, so a silent uninstall never waits for a click; the silent answer keeps the data }
    if SuppressibleMsgBox('Also delete your {#AppName} settings, paired phones and the TV browser profile?',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
    begin
      DelTree(ExpandConstant('{userappdata}\CouchJarvis'), True, True, True);
      DelTree(ExpandConstant('{localappdata}\CouchJarvis'), True, True, True);
    end;
end;
