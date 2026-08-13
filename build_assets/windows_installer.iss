; Inno Setup script for Snowsky Echo Mini Toolbox
; Build the app first with PyInstaller so dist\Snowsky Echo Mini Toolbox exists.

#define MyAppName "Snowsky Echo Mini Toolbox"
#define MyAppVersion "1.5.1"
#define MyAppPublisher "Snowsky"
#define MyAppExeName "Snowsky Echo Mini Toolbox.exe"

#ifndef MyOutputDir
#define MyOutputDir "..\\dist"
#endif

#ifndef MyOutputBaseFilename
#define MyOutputBaseFilename "Snowsky-Echo-Mini-Toolbox-Windows-Setup"
#endif

[Setup]
AppId={{6D54F096-E30B-4B80-B445-E45D560C9365}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#MyOutputDir}
OutputBaseFilename={#MyOutputBaseFilename}
PrivilegesRequired=admin
SetupIconFile=..\build_assets\icons\windows-icon.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop icon"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "..\dist\Snowsky Echo Mini Toolbox\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
