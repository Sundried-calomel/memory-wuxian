#ifndef SourceRoot
  #error SourceRoot must be provided
#endif
#ifndef AppVersion
  #error AppVersion must be provided
#endif

[Setup]
AppId={{A7D86143-D3CB-4E8A-BA94-E5E24F8FC8CA}
AppName=MemoryWuxian
AppVersion={#AppVersion}
AppPublisher=Sundried-calomel
AppPublisherURL=https://github.com/Sundried-calomel/memory-wuxian
DefaultDirName={%USERPROFILE}\.codex\skills\memory-wuxian
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
SetupIconFile={#SourceRoot}\assets\memory-wuxian.ico
UninstallDisplayIcon={app}\assets\memory-wuxian.ico
OutputDir={#SourceRoot}\dist
OutputBaseFilename=MemoryWuxian-{#AppVersion}-Windows-x64-Setup

[Files]
Source: "{#SourceRoot}\*"; DestDir: "{tmp}\MemoryWuxian\candidate"; Flags: ignoreversion recursesubdirs createallsubdirs deleteafterinstall; Excludes: ".git\*,.github\*,memory\*,native-collector\target\*,packaging\*,dist\*,outputs\*,__pycache__\*,*.pyc"
Source: "{#SourceRoot}\runtime\windows\python\Lib\site-packages\__pycache__\sitecustomize.*.pyc"; DestDir: "{tmp}\MemoryWuxian\candidate\runtime\windows\python\Lib\site-packages\__pycache__"; Flags: ignoreversion deleteafterinstall
Source: "{#SourceRoot}\config.yaml"; DestDir: "{tmp}\MemoryWuxian\candidate"; DestName: "config.defaults.yaml"; Flags: onlyifdoesntexist ignoreversion deleteafterinstall
Source: "{#SourceRoot}\packaging\windows\install.ps1"; DestDir: "{tmp}\MemoryWuxian"; Flags: ignoreversion deleteafterinstall
Source: "{#SourceRoot}\packaging\windows\uninstall.ps1"; DestDir: "{tmp}\MemoryWuxian\candidate\packaging\windows"; Flags: ignoreversion deleteafterinstall

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\packaging\windows\uninstall.ps1"" -SkillRoot ""{app}"""; Flags: runhidden waituntilterminated; RunOnceId: "MemoryWuxianCollector"

[Code]
var
  TransactionExitCode: Integer;
  TransactionExecuted: Boolean;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Parameters: String;
  ResultCode: Integer;
begin
  if (CurStep <> ssPostInstall) or TransactionExecuted then
    exit;
  TransactionExecuted := True;
  Parameters :=
    '-NoProfile -ExecutionPolicy Bypass -File "' +
    ExpandConstant('{tmp}\MemoryWuxian\install.ps1') +
    '" -SkillRoot "' + ExpandConstant('{app}') +
    '" -CandidateRoot "' + ExpandConstant('{tmp}\MemoryWuxian\candidate') +
    '" -SourceEntrypoint "' + ExpandConstant('{param:SOURCEENTRYPOINT|inno}') + '"';
  if not Exec('powershell.exe', Parameters, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    TransactionExitCode := 31
  else
    TransactionExitCode := ResultCode;
  Log(Format('MemoryWuxian transaction exit code: %d', [TransactionExitCode]));
end;

function GetCustomSetupExitCode: Integer;
begin
  Result := TransactionExitCode;
end;
