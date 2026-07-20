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
DefaultDirName={userprofile}\.codex\skills\memory-wuxian
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
Source: "{#SourceRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: ".git\*,.github\*,memory\*,native-collector\target\*,packaging\*,dist\*,outputs\*,__pycache__\*,*.pyc"
Source: "{#SourceRoot}\packaging\windows\install.ps1"; DestDir: "{tmp}\MemoryWuxian"; Flags: ignoreversion deleteafterinstall
Source: "{#SourceRoot}\packaging\windows\uninstall.ps1"; DestDir: "{app}\packaging\windows"; Flags: ignoreversion

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{tmp}\MemoryWuxian\install.ps1"" -SkillRoot ""{app}"""; StatusMsg: "Installing and activating MemoryWuxian..."; Flags: runhidden waituntilterminated

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\packaging\windows\uninstall.ps1"" -SkillRoot ""{app}"""; Flags: runhidden waituntilterminated; RunOnceId: "MemoryWuxianCollector"
