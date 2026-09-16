; Inno Setup script -- builds dist\TrackSync-Setup.exe, the single-file
; installer with the usual wizard.
;
; make_installer.bat compiles this; it fetches Inno Setup automatically if
; the machine does not already have it. Nothing here needs Inno Setup 6.3+
; features, so any Inno Setup 6.x can compile it.

#define AppName     "TrackSync"
; The version is passed in by make_installer.bat, which reads it out of
; tracksync/__init__.py. Hardcoding it here is how you end up shipping an
; installer labelled 1.1.0 that contains something else entirely.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppExe      "TrackSync.exe"
#define AppPublisher "TrackSync"

[Setup]
; A fixed GUID is what lets a later version replace this one in place
; instead of installing alongside it.
AppId={{7E4A1F62-9C3D-4B58-A0E1-3B5D2C9F7A41}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}

; PrivilegesRequired=lowest installs under the user's own profile, so the
; installer never triggers a UAC prompt and works on a locked-down machine.
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
DisableWelcomePage=no

UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=..\dist
; The filename carries the version, so a stale installer sitting in dist\
; is obvious instead of being silently double-clicked.
OutputBaseFilename=TrackSync-Setup-{#AppVersion}
SetupIconFile=..\assets\TrackSync.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
WizardSizePercent=110

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; \
    GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\TrackSync\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; \
    Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; \
    Flags: nowait postinstall skipifsilent

[Messages]
WelcomeLabel2=This will install [name/ver] on your computer.%n%nTrackSync aligns two recordings of the same event that were made in different places and started at different times.%n%nIt installs for you only, so no administrator password is needed.
