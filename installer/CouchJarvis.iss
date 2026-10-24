; Inno Setup script for Couch Jarvis. Build with installer\build.ps1 (needs Inno Setup 6).
; Per-user install: no administrator rights, nothing is written outside the user profile.

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{7B1E3C52-5A9D-4B5E-9C1A-2F4D8E6A0C31}
AppName=Couch Jarvis
AppVersion={#AppVersion}
AppPublisher=Couch Jarvis
DefaultDirName={autopf}\CouchJarvis
DefaultGroupName=Couch Jarvis
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=CouchJarvis-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName=Couch Jarvis
CloseApplications=force

[Tasks]
Name: "startup"; Description: "Start Couch Jarvis when I sign in to Windows"; GroupDescription: "Startup:"
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "dist\CouchJarvis\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\scripts\setup_brain.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Couch Jarvis"; Filename: "{app}\CouchJarvis.exe"
Name: "{autoprograms}\Couch Jarvis settings"; Filename: "{app}\CouchJarvis.exe"; Parameters: "--open-settings"; Comment: "Open your settings file (add your own Groq or Gemini key here)"
Name: "{autoprograms}\Set up AI brain"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\setup_brain.ps1"" -Pause"; Comment: "Download the local AI model that understands free-form commands (about 2-8 GB)"
Name: "{autodesktop}\Couch Jarvis"; Filename: "{app}\CouchJarvis.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "CouchJarvis"; ValueData: """{app}\CouchJarvis.exe"""; Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\setup_brain.ps1"" -Pause"; Description: "Download the local AI brain now (Ollama and a model, 2-8 GB; skip it if you entered a Gemini key)"; Flags: postinstall skipifsilent; Check: NoGeminiKey
Filename: "{app}\CouchJarvis.exe"; Description: "Start Couch Jarvis now (a pairing QR code appears on screen)"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "taskkill.exe"; Parameters: "/im CouchJarvis.exe /f"; Flags: runhidden; RunOnceId: "StopCouchJarvis"

[Code]
var
  KeyPage: TInputQueryWizardPage;

function NoGeminiKey: Boolean;
begin
  Result := Trim(KeyPage.Values[0]) = '';
end;

procedure InitializeWizard;
begin
  KeyPage := CreateInputQueryPage(wpSelectTasks, 'AI brain (optional)',
    'A free Gemini key makes free-form commands fast and needs no big download.',
    'Get a free key at aistudio.google.com/apikey and paste it here, or leave this empty to ' +
    'download a local AI model after installing (several GB). You can change it later in ' +
    'Start menu > Couch Jarvis settings.');
  KeyPage.Add('Gemini API key (optional):', False);
end;

procedure SaveGeminiKey;
var
  Config, Defaults: String;
  Text: AnsiString;
  Key: String;
begin
  Key := Trim(KeyPage.Values[0]);
  if Key = '' then Exit;
  Config := ExpandConstant('{userappdata}\CouchJarvis\config.env');
  ForceDirectories(ExpandConstant('{userappdata}\CouchJarvis'));
  if not FileExists(Config) then
  begin
    { start from the shipped defaults so the relay and phone-app addresses are kept }
    Defaults := ExpandConstant('{app}\_internal\installer\defaults.env');
    if LoadStringFromFile(Defaults, Text) then
      SaveStringToFile(Config, Text + #13#10, False);
  end;
  SaveStringToFile(Config, 'CJ_GEMINI_API_KEY=' + Key + #13#10, True);
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

function InitializeSetup: Boolean;
begin
  Result := True;
  if not EdgeInstalled then
    Result := MsgBox('Microsoft Edge was not found. Couch Jarvis drives Edge to play your media, so ' +
                     'install Edge first.' + #13#10#13#10 + 'Install Couch Jarvis anyway?',
                     mbConfirmation, MB_YESNO) = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    if MsgBox('Also delete your Couch Jarvis settings, paired phones and the TV browser profile?',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    begin
      DelTree(ExpandConstant('{userappdata}\CouchJarvis'), True, True, True);
      DelTree(ExpandConstant('{localappdata}\CouchJarvis'), True, True, True);
    end;
end;
