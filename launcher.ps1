# =============================================================================
#  launcher.ps1 - startup gatekeeper for the Detail-Preserving Image Upscaling System
#
#  Responsibilities:
#    1. Locate the project root and prepare logs/.
#    2. Verify that .venv is usable ON THIS MACHINE (a venv copied from another
#       computer is never usable: pyvenv.cfg pins the original interpreter path).
#    3. If the environment is stale/broken/foreign, park it aside and call
#       setup.ps1 to build a clean .venv from scratch.
#    4. Launch app.py with the venv interpreter (pythonw.exe, no console).
#
#  Switches:
#    -Check        diagnose only, never builds or launches (exit 0 = healthy)
#    -ForceSetup   ignore the lock file and rebuild the environment
#    -Console      run app.py with a console window (for debugging)
#
#  All messages are intentionally ASCII-only so that Windows PowerShell 5.1
#  never has to guess about code pages.
# =============================================================================

param(
    [switch]$ForceSetup,
    [switch]$Console,
    [switch]$Check
)

$ErrorActionPreference = "Stop"

# Keep failures visible: an unhandled error would otherwise be printed only to
# the error stream of the console that Start.cmd opens and closes.
trap {
    $msg = $_.Exception.Message
    Write-Host ""
    Write-Host "  [ERROR] $msg" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Startup stopped. Details: logs\startup.log and logs\setup.log" -ForegroundColor Yellow
    exit 1
}

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
# A restricted folder (or a UNC share) can reject Set-Location; every other
# operation below uses absolute paths, so failing here is not fatal.
if (-not $ProjectRoot.StartsWith("\\")) {
    try { Set-Location -LiteralPath $ProjectRoot } catch { }
}

$VenvDir       = Join-Path $ProjectRoot ".venv"
$VenvPython    = Join-Path $VenvDir "Scripts\python.exe"
$VenvPythonw   = Join-Path $VenvDir "Scripts\pythonw.exe"
$VenvConfig    = Join-Path $VenvDir "pyvenv.cfg"
$LockFile      = Join-Path $ProjectRoot ".setup.lock"
$Requirements  = Join-Path $ProjectRoot "requirements.txt"
$SetupScript   = Join-Path $ProjectRoot "setup.ps1"
$LogDir        = Join-Path $ProjectRoot "logs"
$StartupLog    = Join-Path $LogDir "startup.log"
$AppEntry      = Join-Path $ProjectRoot "app.py"

function Write-StartupLog {
    param([string]$Message, [string]$Level = "INFO")
    try {
        if (-not (Test-Path -LiteralPath $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
        $line = "{0} | {1,-7} | {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
        Add-Content -LiteralPath $StartupLog -Value $line -Encoding UTF8
    } catch { }
}

function Write-Banner {
    Write-Host ""
    Write-Host "  +--------------------------------------------------------------+" -ForegroundColor DarkCyan
    Write-Host "  |  Detail-Preserving Image Upscaling System                    |" -ForegroundColor DarkCyan
    Write-Host "  |  Detail-Preserving Interpolation / Scaling Workbench         |" -ForegroundColor DarkCyan
    Write-Host "  +--------------------------------------------------------------+" -ForegroundColor DarkCyan
    Write-Host ""
}

function Get-RequirementsHash {
    if (-not (Test-Path -LiteralPath $Requirements)) { return "" }
    try {
        $bytes = [System.IO.File]::ReadAllBytes($Requirements)
        $sha = [System.Security.Cryptography.SHA256]::Create()
        $hash = $sha.ComputeHash($bytes)
        return ([System.BitConverter]::ToString($hash) -replace "-", "").Substring(0, 16).ToLowerInvariant()
    } catch {
        return ""
    }
}

# ------------------------------------------------------- permissions / MOTW

function Test-WritableProjectDir {
    <# Creates and deletes a probe file: true only if we can really write. #>
    param([string]$Path = $ProjectRoot)
    try {
        if (-not (Test-Path -LiteralPath $Path)) {
            New-Item -ItemType Directory -Path $Path -Force -ErrorAction Stop | Out-Null
        }
        $probe = Join-Path $Path (".write-probe-" + [Guid]::NewGuid().ToString("N").Substring(0, 8) + ".tmp")
        [System.IO.File]::WriteAllText($probe, "probe")
        Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
        return $true
    } catch {
        return $false
    }
}

function Clear-NetworkBlock {
    <#
      Removes the "downloaded from the internet" mark (Zone.Identifier stream)
      from the scripts in the project. A marked .ps1 is blocked by the default
      execution policy on the receiver machine; with -ExecutionPolicy Bypass it
      already runs, but the mark also triggers SmartScreen prompts and can make
      antivirus software block the freshly extracted files, so we clear it.
    #>
    $count = 0
    foreach ($pattern in @("*.ps1", "*.cmd", "*.bat", "*.py", "*.json", "*.onnx")) {
        $items = @(Get-ChildItem -LiteralPath $ProjectRoot -Filter $pattern -File -Recurse -Force -ErrorAction SilentlyContinue |
                   Where-Object { $_.FullName -notmatch '\\\.venv(\\|$)' })
        foreach ($item in $items) {
            try {
                $zone = Get-Item -LiteralPath $item.FullName -Stream Zone.Identifier -ErrorAction SilentlyContinue
                if ($zone) {
                    Unblock-File -LiteralPath $item.FullName -ErrorAction Stop
                    $count++
                }
            } catch { }
        }
    }
    if ($count -gt 0) {
        Write-StartupLog ("permissions: cleared the network mark on " + $count + " file(s)") "WARN"
    }
    return $count
}

function Repair-WritePermissions {
    <#
      Guarantees that this folder is writable BEFORE anything tries to create
      .venv, logs\ or .setup.lock. A package that travelled as a zip can arrive
      with the read-only attribute set, or with ACLs copied from another user
      profile - both make the first start fail with "access denied".

      Escalation order (each step only runs when the previous one was not
      enough; every step is logged to logs\startup.log):
        0. write probe - stop immediately when the folder is already writable
        1. attrib -R /S /D  - strip the read-only attribute from the whole tree
        2. icacls /reset then /grant <current user>:(OI)(CI)F - drop the
           author's explicit ACEs, restore inheritance, grant us full control
        3. .NET Set-Acl fallback for shells where icacls.exe is unavailable
    #>
    $result = @{ Writable = $false; Steps = @() }

    if (Test-WritableProjectDir) {
        $result.Writable = $true
        return $result
    }

    $result.Steps += "write probe failed"
    Write-StartupLog "permissions: project folder is not writable, repairing" "WARN"
    Write-Host "  [permissions] This folder is not writable. Repairing ..." -ForegroundColor Yellow

    # 1. read-only attribute (the usual zip / copy artefact)
    try {
        $null = & attrib.exe -R (Join-Path $ProjectRoot "*.*") /S /D 2>$null
        $result.Steps += "attrib -R /S /D"
        Write-Host "                cleared read-only attributes" -ForegroundColor DarkGray
        Write-StartupLog "permissions: attrib -R /S /D applied" "WARN"
    } catch {
        Write-StartupLog ("permissions: attrib failed: " + $_.Exception.Message) "WARN"
    }

    # 2. ACLs (explicit deny copied from the author profile, or missing entry)
    try {
        $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $null = & icacls.exe $ProjectRoot /reset /T /C /Q 2>$null
        $result.Steps += "icacls /reset"
        $null = & icacls.exe $ProjectRoot /grant ("{0}:(OI)(CI)F" -f $identity) /T /C /Q 2>$null
        $result.Steps += ("icacls /grant " + $identity)
        Write-Host "                restored ACL inheritance, granted full control" -ForegroundColor DarkGray
        Write-StartupLog ("permissions: icacls reset, full control granted to " + $identity) "WARN"
    } catch {
        Write-StartupLog ("permissions: icacls failed: " + $_.Exception.Message) "WARN"
    }

    # 3. .NET fallback, used only when the command line tools were unusable
    if (-not (Test-WritableProjectDir)) {
        try {
            $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
            $acl = Get-Acl -LiteralPath $ProjectRoot
            $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
                $identity, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")
            $acl.SetAccessRule($rule)
            $acl.SetAccessRuleProtection($false, $false)
            Set-Acl -LiteralPath $ProjectRoot -AclObject $acl
            $result.Steps += ".NET Set-Acl"
            Write-StartupLog "permissions: .NET ACL fallback applied" "WARN"
        } catch {
            Write-StartupLog ("permissions: .NET ACL fallback failed: " + $_.Exception.Message) "WARN"
        }
    }

    $result.Writable = Test-WritableProjectDir
    if ($result.Writable) {
        Write-Host "  [permissions] Repaired, continuing." -ForegroundColor Green
        Write-StartupLog "permissions: repaired" "WARN"
    }
    return $result
}

function Assert-UsableInstallPath {
    <#
      Some locations cannot host a virtual environment no matter the ACLs:
      a UNC share, Program Files (admin only), or a very deep path where the
      site-packages hierarchy exceeds MAX_PATH. Report these early instead of
      letting pip fail with an obscure error.
    #>
    $warnings = @()

    if ($ProjectRoot.StartsWith("\\")) {
        $warnings += "The project is running from a network share ($ProjectRoot). Copy it to a local disk first."
    }
    if ($ProjectRoot -match '(?i)^[A-Z]:\\Program Files') {
        $warnings += "The project sits inside Program Files, which needs administrator rights. Move it to a user folder."
    }
    if ($ProjectRoot.Length -gt 90) {
        $warnings += ("The folder path is long ({0} characters). Long site-packages paths may exceed MAX_PATH; a shorter path such as D:\Tools\Upscaler is safer." -f $ProjectRoot.Length)
    }
    if ($ProjectRoot -match '(?i)\\OneDrive\\') {
        $warnings += "OneDrive folders can be cloud-only or write-protected; prefer a plain local folder."
    }
    return $warnings
}

function Get-VenvBaseHome {
    <# Reads the base interpreter path that the venv is bound to. #>
    if (-not (Test-Path -LiteralPath $VenvConfig)) { return "" }
    try {
        foreach ($line in (Get-Content -LiteralPath $VenvConfig -Encoding ASCII)) {
            if ($line -match '^\s*home\s*=\s*(.+?)\s*$') { return $Matches[1] }
        }
    } catch { }
    return ""
}

function Test-EnvironmentReady {
    <# Returns @{ Ready = $true/$false; Reason = "..." }. #>
    $result = @{ Ready = $false; Reason = "" }

    if (-not (Test-Path -LiteralPath $VenvPython)) {
        $result.Reason = "Virtual environment interpreter is missing (.venv\Scripts\python.exe)."
        Write-StartupLog $result.Reason
        return $result
    }

    # A venv is only usable on the machine that created it. Detect the foreign case.
    $baseHome = Get-VenvBaseHome
    if ([string]::IsNullOrWhiteSpace($baseHome)) {
        $result.Reason = "pyvenv.cfg is missing or unreadable inside .venv."
        Write-StartupLog $result.Reason "WARN"
        return $result
    }
    if (-not (Test-Path -LiteralPath $baseHome)) {
        $result.Reason = "This .venv was created on another computer (base interpreter not found: $baseHome)."
        Write-StartupLog $result.Reason "WARN"
        return $result
    }

    if (-not (Test-Path -LiteralPath $LockFile)) {
        $result.Reason = "Lock file .setup.lock is missing."
        Write-StartupLog $result.Reason
        return $result
    }

    $expected = Get-RequirementsHash
    try {
        $lock = Get-Content -LiteralPath $LockFile -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        $result.Reason = "Lock file unreadable."
        Write-StartupLog ($result.Reason + " Will rebuild.") "WARN"
        return $result
    }
    if ($lock.requirements_sha256 -and $expected -and ($lock.requirements_sha256 -ne $expected)) {
        $result.Reason = "requirements.txt changed since the last setup."
        Write-StartupLog ($result.Reason + " Will rebuild.") "WARN"
        return $result
    }

    # Smoke test: the venv interpreter must import the two pillars of the app.
    # Captured through Start-Process so a warning written to stderr (Qt and
    # numpy both do this) is not mistaken for a broken environment - with
    # $ErrorActionPreference = 'Stop', "& python -c ... 2>&1" would raise
    # NativeCommandError and throw the whole environment away.
    $probe = "import sys, cv2, numpy; import PyQt5.QtCore; print('PROBE_OK', sys.version.split()[0])"
    $probeDir = Join-Path $ProjectRoot "data"
    if (-not (Test-Path -LiteralPath $probeDir)) {
        try { New-Item -ItemType Directory -Path $probeDir -Force | Out-Null } catch { }
    }
    $probeOut = Join-Path $ProjectRoot "data\.probe_out.txt"
    $probeErr = Join-Path $ProjectRoot "data\.probe_err.txt"
    try {
        $null = Start-Process -FilePath $VenvPython -ArgumentList @("-c", ('"' + $probe + '"')) -PassThru -NoNewWindow -Wait -RedirectStandardOutput $probeOut -RedirectStandardError $probeErr
        $output = @()
        foreach ($f in @($probeOut, $probeErr)) {
            if (Test-Path -LiteralPath $f) {
                $chunk = Get-Content -LiteralPath $f -Raw -ErrorAction SilentlyContinue
                if ($chunk) { $output += $chunk.Trim() }
                Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue
            }
        }
    } catch {
        $result.Reason = "Venv interpreter cannot be started: " + $_.Exception.Message
        Write-StartupLog $result.Reason "WARN"
        return $result
    }
    $text = ($output | Out-String).Trim()
    if ($text -notmatch 'PROBE_OK') {
        $result.Reason = "Venv smoke test failed: " + $text
        Write-StartupLog $result.Reason "WARN"
        return $result
    }

    Write-StartupLog ("Environment OK: " + $text)
    $result.Ready = $true
    return $result
}

function Move-BrokenEnvironmentAside {
    <# Keeps the old environment for inspection but gets it out of the way. #>
    if (-not (Test-Path -LiteralPath $VenvDir)) { return }
    $stamp  = Get-Date -Format "yyyyMMdd_HHmmss"
    $target = Join-Path $ProjectRoot (".venv.broken-" + $stamp)
    try {
        Move-Item -LiteralPath $VenvDir -Destination $target -Force
        Write-Host "  Old environment parked as: $(Split-Path -Leaf $target)" -ForegroundColor Yellow
        Write-StartupLog ("Parked stale venv: " + $target) "WARN"
    } catch {
        Write-StartupLog ("Could not park stale venv: " + $_.Exception.Message) "WARN"
        try { Remove-Item -LiteralPath $VenvDir -Recurse -Force } catch { }
    }
    if (Test-Path -LiteralPath $LockFile) {
        Remove-Item -LiteralPath $LockFile -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-Setup {
    if (-not (Test-Path -LiteralPath $SetupScript)) {
        throw "setup.ps1 is missing from the project root."
    }
    Write-Host "  [1/2] Preparing the isolated Python environment." -ForegroundColor Yellow
    Write-Host "        This happens once per machine; please keep this window open." -ForegroundColor DarkGray
    Write-Host ""
    $arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$SetupScript`"")
    if ($ForceSetup) { $arguments += "-Force" }
    & powershell @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Environment setup failed with exit code $LASTEXITCODE. See logs\setup.log."
    }
    Write-Host ""
}

function Start-Application {
    if (-not (Test-Path -LiteralPath $AppEntry)) { throw "app.py is missing from the project root." }

    $launcher = $VenvPythonw
    $arguments = @("`"$AppEntry`"")
    if ($Console) {
        $launcher = $VenvPython
        $arguments = @("`"$AppEntry`"", "--console")
    }
    if (-not (Test-Path -LiteralPath $launcher)) { $launcher = $VenvPython }

    Write-Host "  [2/2] Starting the workbench..." -ForegroundColor Green
    Write-StartupLog ("Launching application: " + $launcher)
    $process = Start-Process -FilePath $launcher -ArgumentList $arguments -WorkingDirectory $ProjectRoot -PassThru
    Write-Host ""
    Write-Host "  GUI is running (pid $($process.Id)). This window will close automatically." -ForegroundColor DarkGray
    Start-Sleep -Milliseconds 900
}

try {
    Write-Banner
    Write-StartupLog "---- launcher started ----"
    Write-StartupLog ("Project root: " + $ProjectRoot)

    $pathWarnings = @(Assert-UsableInstallPath)
    foreach ($note in $pathWarnings) { Write-StartupLog ("path check: " + $note) "WARN" }

    $untagged = Clear-NetworkBlock
    if ($untagged -gt 0) {
        Write-Host ("  Cleared the network mark on {0} file(s)." -f $untagged) -ForegroundColor DarkGray
    }

    if ($Check) {
        $writableNow = Test-WritableProjectDir
    } else {
        $repair = Repair-WritePermissions
        foreach ($step in $repair.Steps) { Write-StartupLog ("permissions step: " + $step) }
        $writableNow = $repair.Writable
        if (-not $writableNow) {
            throw ("The project folder is not writable ({0}). The environment cannot be created there. Move the folder to a writable local disk (for example D:\Tools\DetailPreservingUpscaler), keep it out of Program Files, out of OneDrive-only folders and out of read-only archives, then double click Start.cmd again." -f $ProjectRoot)
        }
    }

    $state = Test-EnvironmentReady

    if ($Check) {
        Write-Host "  Project root : $ProjectRoot"
        Write-Host "  Python venv  : $(if (Test-Path -LiteralPath $VenvPython) { $VenvPython } else { '<missing>' })"
        $baseHome = Get-VenvBaseHome
        Write-Host "  Venv base    : $(if ($baseHome) { $baseHome } else { '<unknown>' })"
        Write-Host "  PowerShell   : $($PSVersionTable.PSVersion)"
        $sysPython = Get-Command python -ErrorAction SilentlyContinue
        $pyLauncher = Get-Command py     -ErrorAction SilentlyContinue
        Write-Host "  System python: $(if ($sysPython) { $sysPython.Source } else { '<not on PATH>' })"
        Write-Host "  py launcher  : $(if ($pyLauncher) { $pyLauncher.Source } else { '<not on PATH>' })"
        $roCount = @(Get-ChildItem -LiteralPath $ProjectRoot -Force -ErrorAction SilentlyContinue |
                     Where-Object { $_.Attributes -band [System.IO.FileAttributes]::ReadOnly }).Count
        Write-Host "  Writable     : $(if ($writableNow) { 'yes' } else { 'NO - Start.cmd will repair it' })"
        Write-Host "  Read-only    : $(if ($roCount -gt 0) { '{0} item(s) at root' -f $roCount } else { 'none at root' })"
        foreach ($note in $pathWarnings) {
            Write-Host "  [NOTE] $note" -ForegroundColor Yellow
        }
        Write-Host ""
        if ($state.Ready) {
            Write-Host "  [OK] Environment is ready on this machine." -ForegroundColor Green
            exit 0
        }
        Write-Host "  [NOT READY] $($state.Reason)" -ForegroundColor Yellow
        Write-Host "  Run Start.cmd once; it will build a fresh environment automatically." -ForegroundColor DarkGray
        exit 2
    }

    if ($ForceSetup) {
        Write-StartupLog "Force rebuild requested."
        Move-BrokenEnvironmentAside
        Invoke-Setup
    } elseif ($state.Ready) {
        Write-Host "  Environment lock verified (.setup.lock) - skipping setup." -ForegroundColor Green
        Write-Host ""
    } else {
        Write-Host "  Environment needs (re)building: $($state.Reason)" -ForegroundColor Yellow
        Move-BrokenEnvironmentAside
        Invoke-Setup
    }

    if (-not (Test-Path -LiteralPath $VenvPython)) {
        throw "Virtual environment is still missing after setup: $VenvPython"
    }

    $verify = Test-EnvironmentReady
    if (-not $verify.Ready) {
        throw "Environment is still not usable after setup: $($verify.Reason)"
    }

    Start-Application
    exit 0
}
catch {
    $message = $_.Exception.Message
    Write-StartupLog $message "ERROR"
    Write-Host ""
    Write-Host "  [FAILED] $message" -ForegroundColor Red
    if ($message -match 'setup failed') {
        Write-Host "  If setup could not obtain Python 3.10+, install it manually from" -ForegroundColor DarkGray
        Write-Host "  https://www.python.org/downloads/  (tick 'Add python.exe to PATH')," -ForegroundColor DarkGray
        Write-Host "  then double click Start.cmd again." -ForegroundColor DarkGray
    }
    if ($message -match 'not writable') {
        Write-Host "  Automatic repair did not help. Please:" -ForegroundColor DarkGray
        Write-Host "    1. Copy the extracted folder to a local disk, e.g. D:\Tools\Upscaler" -ForegroundColor DarkGray
        Write-Host "    2. Do not run it inside the zip preview, a network share or Program Files" -ForegroundColor DarkGray
        Write-Host "    3. Right click the folder > Properties > clear 'Read-only' if it is ticked" -ForegroundColor DarkGray
        Write-Host "    4. Double click Start.cmd again" -ForegroundColor DarkGray
    }
    Write-Host "  Details were written to: $StartupLog" -ForegroundColor DarkGray
    Write-Host "  Run Diagnose.cmd to collect a machine report." -ForegroundColor DarkGray
    Write-Host ""
    exit 1
}
