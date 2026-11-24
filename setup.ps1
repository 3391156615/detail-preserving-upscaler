# =============================================================================
#  setup.ps1 - one-shot environment bootstrapper
#
#  Flow:
#    1. Find a usable CPython (>= 3.10). If none is present, try winget, then
#       fall back to the official python.org silent installer.
#    2. Create .venv (CPU-only, isolated from the system site-packages).
#    3. Install requirements.txt with a strict "no prompt" pip invocation;
#       automatically retry through domestic mirrors when the default index
#       is unreachable.
#    4. Verify the critical imports and write .setup.lock (fingerprint used by
#       launcher.ps1 to skip this script on the next run).
#
#  Everything here is non-interactive. ASCII only, so Windows PowerShell 5.1
#  never mis-decodes the file.
# =============================================================================

param(
    [switch]$Force,
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"

# Any unhandled failure lands here: the message goes to the console window the
# user is actually looking at (PowerShell would otherwise only write it to the
# error stream, which is easy to miss), then the log keeps the full history.
trap {
    $msg = $_.Exception.Message
    Write-Host ""
    Write-Host "  [ERROR] $msg" -ForegroundColor Red
    try { Write-SetupLog ("fatal: " + $msg) "ERROR" } catch { }
    Write-Host ""
    Write-Host "  Setup stopped. Full details: logs\setup.log" -ForegroundColor Yellow
    Write-Host "  Fix the cause above, then double click Start.cmd again." -ForegroundColor Yellow
    exit 1
}

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location -LiteralPath $ProjectRoot

$VenvDir      = Join-Path $ProjectRoot ".venv"
$VenvPython   = Join-Path $VenvDir "Scripts\python.exe"
$LockFile     = Join-Path $ProjectRoot ".setup.lock"
$Requirements = Join-Path $ProjectRoot "requirements.txt"
$LogDir       = Join-Path $ProjectRoot "logs"
$SetupLog     = Join-Path $LogDir "setup.log"
$MinMajor     = 3
$MinMinor     = 10
$Mirrors      = @(
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple"
)

# --------------------------------------------------------------------------- io

function Assert-WritableProjectRoot {
    <#
      setup.ps1 is also documented for direct use (.\setup.ps1), i.e. without
      launcher.ps1 running the permission repair first. A package that was
      copied or unzipped can carry the read-only attribute or ACLs from another
      machine; in that case every step below (creating .venv, writing logs\,
      writing .setup.lock) fails with an access-denied error. Probe first,
      repair once, and otherwise fail with an actionable message.
    #>
    $probe = Join-Path $ProjectRoot (".write-probe-" + [Guid]::NewGuid().ToString("N").Substring(0, 8) + ".tmp")
    $writable = $false
    try {
        [System.IO.File]::WriteAllText($probe, "probe")
        Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
        $writable = $true
    } catch {
        $writable = $false
    }
    if ($writable) { return }

    Write-Warn "The project folder is not writable - attempting a repair"
    try { $null = & attrib.exe -R (Join-Path $ProjectRoot "*.*") /S /D 2>$null } catch { }
    try {
        $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $null = & icacls.exe $ProjectRoot /reset /T /C /Q 2>$null
        $null = & icacls.exe $ProjectRoot /grant ("{0}:(OI)(CI)F" -f $identity) /T /C /Q 2>$null
    } catch { }

    try {
        [System.IO.File]::WriteAllText($probe, "probe")
        Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
        Write-Warn "Permissions repaired (read-only attribute cleared, ACL inheritance restored)"
    } catch {
        throw ("The project folder is not writable: {0}. Move it to a writable local disk (for example D:\Tools\Upscaler) and run Start.cmd again." -f $ProjectRoot)
    }
}

function Initialize-Dirs {
    foreach ($dir in @($LogDir, (Join-Path $ProjectRoot "data"), (Join-Path $ProjectRoot "results"))) {
        if (-not (Test-Path -LiteralPath $dir)) {
            try {
                New-Item -ItemType Directory -Path $dir -Force -ErrorAction Stop | Out-Null
            } catch {
                throw ("Cannot create the folder '{0}' ({1}). The project folder must be writable." -f $dir, $_.Exception.Message)
            }
        }
    }
}

function Write-SetupLog {
    param([string]$Message, [string]$Level = "INFO")
    try {
        if (-not (Test-Path -LiteralPath $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
        $line = "{0} | {1,-7} | {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
        Add-Content -LiteralPath $SetupLog -Value $line -Encoding UTF8
    } catch { }
}

function Write-Section {
    param([string]$Text)
    Write-Host ""
    Write-Host "  >> $Text" -ForegroundColor Cyan
    Write-SetupLog $Text
}

function Write-Detail {
    param([string]$Text)
    Write-Host "     $Text" -ForegroundColor DarkGray
}

function Write-Ok {
    param([string]$Text)
    Write-Host "     [OK] $Text" -ForegroundColor Green
    Write-SetupLog $Text
}

function Write-Warn {
    param([string]$Text)
    Write-Host "     [!!] $Text" -ForegroundColor Yellow
    Write-SetupLog $Text "WARN"
}

# -------------------------------------------------------------------- python

function Test-PythonCandidate {
    param([string]$ExePath, [string[]]$PrefixArgs)
    if (-not $ExePath) { return $null }
    try {
        $probe = @($PrefixArgs) + @("-c", "import sys; print('%d|%d|%d' % (sys.version_info[0], sys.version_info[1], sys.maxsize > 2**32))")
        $raw = & $ExePath @probe 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $raw) { return $null }
        $text = ($raw | Select-Object -Last 1).Trim()
        $parts = $text.Split("|")
        if ($parts.Count -lt 3) { return $null }
        $major = [int]$parts[0]
        $minor = [int]$parts[1]
        $is64 = $parts[2] -eq "1"
        if ($major -lt $MinMajor -or ($major -eq $MinMajor -and $minor -lt $MinMinor)) { return $null }
        if (-not $is64) { Write-Warn "Ignoring 32-bit interpreter: $ExePath" ; return $null }
        return @{
            Path    = $ExePath
            Args    = @($PrefixArgs)
            Version = "$major.$minor"
            Label   = ("$ExePath " + ($PrefixArgs -join " ")).Trim()
        }
    } catch {
        return $null
    }
}

function Resolve-Python {
    param([string]$Preferred = "")

    if ($Preferred) {
        $found = Test-PythonCandidate -ExePath $Preferred -PrefixArgs @()
        if ($found) { return $found }
    }

    $pyLauncher = Get-Command "py" -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        foreach ($version in @("-3.12", "-3.13", "-3.11", "-3.10", "-3")) {
            $found = Test-PythonCandidate -ExePath $pyLauncher.Source -PrefixArgs @($version)
            if ($found) { return $found }
        }
    }

    foreach ($name in @("python", "python3")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) {
            $found = Test-PythonCandidate -ExePath $cmd.Source -PrefixArgs @()
            if ($found) { return $found }
        }
    }

    # Well-known per-user install locations (PATH may not be refreshed yet)
    $localApps = Join-Path $env:LOCALAPPDATA "Programs\Python"
    if (Test-Path -LiteralPath $localApps) {
        foreach ($dir in (Get-ChildItem -LiteralPath $localApps -Directory -ErrorAction SilentlyContinue |
                          Sort-Object Name -Descending)) {
            $exe = Join-Path $dir.FullName "python.exe"
            $found = Test-PythonCandidate -ExePath $exe -PrefixArgs @()
            if ($found) { return $found }
        }
    }
    return $null
}

function Install-Python {
    Write-Section "No usable Python found - installing CPython automatically"

    $winget = Get-Command "winget" -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Detail "Trying winget (silent, user scope) ..."
        Write-SetupLog "winget install Python.Python.3.12"
        & $winget.Source install --id Python.Python.3.12 --exact --silent `
            --accept-package-agreements --accept-source-agreements --scope user 2>$null | Out-Null
        $found = Resolve-Python
        if ($found) { Write-Ok "Python installed through winget ($($found.Version))"; return $found }
        Write-Warn "winget path did not yield a usable interpreter, falling back to python.org"
    }

    $installer = Join-Path $env:TEMP "python-3.12.10-amd64.exe"
    $url = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
    Write-Detail "Downloading $url"
    Write-SetupLog "Downloading installer: $url"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    try {
        Invoke-WebRequest -Uri $url -OutFile $installer -UseBasicParsing -TimeoutSec 600
    } catch {
        throw "Unable to download the Python installer. Please install Python 3.10+ manually and run Start.cmd again."
    }

    Write-Detail "Running silent installer (this can take a couple of minutes) ..."
    $arguments = "/quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_pip=1 Include_launcher=1 Shortcuts=0"
    $process = Start-Process -FilePath $installer -ArgumentList $arguments -Wait -PassThru
    Write-SetupLog "Installer exit code: $($process.ExitCode)"

    $found = Resolve-Python
    if (-not $found) { throw "Automatic Python installation finished but no interpreter was detected." }
    Write-Ok "Python installed ($($found.Version))"
    return $found
}

# ----------------------------------------------------------------------- pip

function Get-RequirementsHash {
    if (-not (Test-Path -LiteralPath $Requirements)) { return "" }
    $bytes = [System.IO.File]::ReadAllBytes($Requirements)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $hash = $sha.ComputeHash($bytes)
    return ([System.BitConverter]::ToString($hash) -replace "-", "").Substring(0, 16).ToLowerInvariant()
}

function Invoke-Pip {
    param([string[]]$PipArguments, [string]$Label)

    Write-SetupLog ("pip " + ($PipArguments -join " "))
    & $VenvPython @PipArguments
    if ($LASTEXITCODE -eq 0) { return $true }

    Write-Warn "$Label failed on the default index, retrying through mirrors"
    foreach ($mirror in $Mirrors) {
        $host_ = ([Uri]$mirror).Host
        Write-Detail "Mirror: $mirror"
        $retry = @($PipArguments) + @("-i", $mirror, "--trusted-host", $host_)
        & $VenvPython @retry
        if ($LASTEXITCODE -eq 0) { return $true }
    }
    return $false
}

function New-VirtualEnvironment {
    param($Python)

    Write-Section "Creating the isolated virtual environment (.venv)"

    # Windows still applies its 260-character limit to a lot of pip's own code
    # path. Inside a deeply nested folder `venv` dies in ensurepip with a bare
    # "exit code 1" that reads like a permission problem, so warn about the
    # real cause before spending minutes on it.
    $rootLength = $ProjectRoot.Length
    if ($rootLength -gt 110) {
        Write-Warn ("This folder path is {0} characters long." -f $rootLength)
        Write-Detail "Windows caps paths at 260 characters, and pip fails on paths that deep."
        Write-Detail "If setup fails below, move this folder to a short path (e.g. D:\Upscaler)."
    }

    if (Test-Path -LiteralPath $VenvDir) {
        Write-Detail "Removing the previous environment ..."
        try {
            Remove-Item -LiteralPath $VenvDir -Recurse -Force
        } catch {
            throw "Unable to remove the existing .venv ($($_.Exception.Message)). Close any running instance and retry."
        }
    }

    $venvArgs     = @($Python.Args) + @("-m", "venv", ('"' + $VenvDir + '"'))
    $venvOutFile  = Join-Path $LogDir ".venv-out.txt"
    $venvErrFile  = Join-Path $LogDir ".venv-err.txt"
    $venvExit     = -1
    try {
        # Start-Process instead of "& python -m venv": under
        # $ErrorActionPreference = 'Stop' any stderr text from a native command
        # becomes a terminating NativeCommandError, and the useful message
        # from ensurepip would be swallowed.
        $proc = Start-Process -FilePath $Python.Path -ArgumentList $venvArgs -PassThru -NoNewWindow -Wait -RedirectStandardOutput $venvOutFile -RedirectStandardError $venvErrFile
        $venvExit = $proc.ExitCode
    } catch {
        Write-SetupLog ("venv could not be started: " + $_.Exception.Message) "WARN"
    }

    $venvErrText = ""
    if (Test-Path -LiteralPath $venvErrFile) {
        $venvErrText = (Get-Content -LiteralPath $venvErrFile -Raw -ErrorAction SilentlyContinue)
        if ($venvErrText) { Write-SetupLog ("venv stderr: " + ($venvErrText -replace "`r?`n", " ; ")) "WARN" }
    }
    foreach ($file in @($venvOutFile, $venvErrFile)) {
        Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue
    }

    if ($venvExit -ne 0 -or -not (Test-Path -LiteralPath $VenvPython)) {
        if ($venvErrText -match "ensurepip|No such file or directory|FileNotFoundError") {
            throw ("Could not finish creating .venv inside '{0}' (path length {1} characters). Windows' 260-character path limit breaks pip on folders nested this deep - move the whole folder to a short path such as D:\Upscaler and double click Start.cmd again." -f $ProjectRoot, $rootLength)
        }
        throw ("Failed to create the virtual environment (exit code {0}). Try running: {1} -m venv .venv" -f $venvExit, $Python.Label)
    }
    Write-Ok "Virtual environment created"
}

# ---------------------------------------------------------------------- main

Assert-WritableProjectRoot
Initialize-Dirs
Write-SetupLog "==== setup started (Force=$Force) ===="

if (-not (Test-Path -LiteralPath $Requirements)) {
    throw "requirements.txt is missing from the project root."
}

if ($Force -and (Test-Path -LiteralPath $LockFile)) {
    Remove-Item -LiteralPath $LockFile -Force -ErrorAction SilentlyContinue
    Write-Detail "Existing .setup.lock removed (force rebuild)"
}

$python = Resolve-Python -Preferred $PythonExe
if (-not $python) {
    $python = Install-Python
}
Write-Section ("Interpreter: " + $python.Label + "  (Python " + $python.Version + ")")
Write-Ok "Python requirement satisfied (>= $MinMajor.$MinMinor)"

if (Test-Path -LiteralPath $VenvPython) {
    Write-Section "Reusing the existing virtual environment"
    $smoke = & $VenvPython -c "print('alive')" 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $smoke) {
        Write-Warn "Existing .venv looks broken - rebuilding"
        New-VirtualEnvironment -Python $python
    } else {
        Write-Ok "Existing .venv interpreter is healthy"
    }
} else {
    New-VirtualEnvironment -Python $python
}

Write-Section "Upgrading the packaging toolchain"
$toolchainArgs = @("-m", "pip", "install", "--upgrade", "--disable-pip-version-check", "--no-input", "pip", "setuptools", "wheel")
if (-not (Invoke-Pip -PipArguments $toolchainArgs -Label "toolchain upgrade")) {
    Write-Warn "Toolchain upgrade skipped (continuing with the bundled pip)"
} else {
    Write-Ok "pip / setuptools / wheel are up to date"
}

Write-Section "Installing production dependencies (CPU-only wheels)"
Write-Detail "This is the slow part of the first run; typical duration is 1-4 minutes."
$installArgs = @("-m", "pip", "install", "--disable-pip-version-check", "--no-input", "-r", "`"$Requirements`"")
if (-not (Invoke-Pip -PipArguments $installArgs -Label "requirements install")) {
    throw "Dependency installation failed. See logs\setup.log and re-run Start.cmd (network required)."
}
Write-Ok "All production dependencies installed"

Write-Section "Verifying the runtime"
# Every module is probed in its own short-lived interpreter. One combined
# probe script looks tidier but is fragile: if a single optional import takes
# the process down (onnxruntime does that on machines without the MSVC
# runtime) the whole report is lost and a perfectly healthy environment ends
# up reported as broken.
$verifyPath = Join-Path $ProjectRoot "data\.verify_runtime.py"
$verifyCode = @'
import sys
import importlib

module = sys.argv[1]
try:
    importlib.import_module(module)
except Exception as exc:  # noqa: BLE001
    print("missing", module, type(exc).__name__)
    sys.exit(1)
print("ok", module)
'@

function Invoke-VerifyProbe {
    param([string]$Module)
    # Start-Process is used instead of "& python ... 2>&1": with
    # $ErrorActionPreference = 'Stop', Windows PowerShell 5.1 turns the stderr
    # output of a native command into a terminating NativeCommandError, which
    # used to abort the whole setup even though the probe had run fine.
    $stdoutPath = Join-Path $ProjectRoot "data\.verify_out.txt"
    $stderrPath = Join-Path $ProjectRoot "data\.verify_err.txt"
    $text = ""
    try {
        $probeArgs = @(('"' + $verifyPath + '"'), $Module)
        $null = Start-Process -FilePath $VenvPython -ArgumentList $probeArgs -PassThru -NoNewWindow -Wait -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
        foreach ($file in @($stdoutPath, $stderrPath)) {
            if (Test-Path -LiteralPath $file) {
                $chunk = Get-Content -LiteralPath $file -Raw -ErrorAction SilentlyContinue
                if ($chunk) { $text += $chunk.Trim() + " " }
                Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue
            }
        }
    } catch {
        $text = "probe-error " + $_.Exception.Message
    }
    return $text.Trim()
}

try {
    [System.IO.File]::WriteAllText($verifyPath, $verifyCode, (New-Object System.Text.UTF8Encoding($false)))
} catch {
    throw ("Cannot write the verification script into 'data' ({0}). The project folder must be writable." -f $_.Exception.Message)
}

# module -> distribution name on PyPI, used to repair a partial install
$coreChecks = @(
    @("numpy", "numpy"),
    @("cv2", "opencv-python-headless"),
    @("scipy", "scipy"),
    @("skimage", "scikit-image"),
    @("PyQt5.QtWidgets", "PyQt5"),
    @("PIL", "Pillow")
)

function Test-CoreModules {
    $missing = @()
    $reports = @()
    foreach ($check in $coreChecks) {
        $moduleName = $check[0]
        $result = Invoke-VerifyProbe -Module $moduleName
        $reports += ("{0}={1}" -f $moduleName, ($result -replace "`r?`n", " "))
        if ($result -notmatch "^ok ") { $missing += $check[1] }
    }
    return @{
        Missing = $missing
        Report  = ($reports -join " | ")
    }
}

$coreState = Test-CoreModules
Write-SetupLog ("verify output: " + $coreState.Report)

# A mirror or corporate proxy can answer a pip run with a partial set of
# wheels while still exiting 0, so a missing module is repaired here instead
# of being reported as "your machine cannot run this program".
$repairRound = 0
while (($coreState.Missing.Count -gt 0) -and ($repairRound -lt 2)) {
    $repairRound++
    Write-Warn ("Runtime check reported missing packages: " + ($coreState.Missing -join ", "))
    Write-Detail "Installing them now (happens when a mirror drops a wheel)."
    $repairArgs = @("-m", "pip", "install", "--disable-pip-version-check", "--no-input") + $coreState.Missing
    if (-not (Invoke-Pip -PipArguments $repairArgs -Label "missing package install")) {
        Write-Warn "Could not install the missing packages automatically."
        break
    }
    $coreState = Test-CoreModules
    Write-SetupLog ("verify retry " + $repairRound + " output: " + $coreState.Report)
}
Remove-Item -LiteralPath $verifyPath -Force -ErrorAction SilentlyContinue

if ($coreState.Missing.Count -gt 0) {
    throw ("Runtime verification failed. Missing modules: " + ($coreState.Missing -join ", ") + ". Probe detail: " + $coreState.Report + ". Re-run Start.cmd, or repair by hand with: .venv\Scripts\python.exe -m pip install -r requirements.txt")
}
Write-Ok "Core imports verified (numpy / OpenCV / SciPy / scikit-image / PyQt5 / Pillow)"

# onnxruntime is optional: several machines lack the MSVC runtime it needs and
# the probe can even take the interpreter down, so it never blocks setup.
$onnxProbe = Invoke-VerifyProbe -Module "onnxruntime"
if ($onnxProbe -match "^ok ") {
    Write-Ok "onnxruntime ready"
} else {
    Write-SetupLog ("onnxruntime probe: " + $onnxProbe) "WARN"
    Write-Warn "onnxruntime unavailable - the deep-learning algorithm will gracefully fall back"
}

Write-Section "Writing the environment lock (.setup.lock)"
$packages = @{}
try {
    $freeze = & $VenvPython -m pip freeze --disable-pip-version-check 2>$null
    foreach ($line in $freeze) {
        if ($line -match "^(?<name>[^=<>!\[]+)==") { $packages[$Matches["name"].Trim()] = ($line -split "==")[1].Trim() }
    }
} catch { }

$lock = [ordered]@{
    schema               = 2
    created_at           = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    python_version       = $python.Version
    python_executable    = $python.Path
    venv                 = ".venv"
    requirements_sha256  = (Get-RequirementsHash)
    packages             = $packages
    platform             = [System.Environment]::OSVersion.VersionString
    project              = "Detail-Preserving Image Upscaling System"
}
$json = $lock | ConvertTo-Json -Depth 6
[System.IO.File]::WriteAllText($LockFile, $json, (New-Object System.Text.UTF8Encoding($false)))
Write-Ok "Lock written: $LockFile"

Write-Host ""
Write-Host "  Environment is ready. Subsequent launches skip this script entirely." -ForegroundColor Green
Write-SetupLog "==== setup finished ===="

# Make sure the caller (launcher.ps1) can read the venv path back
exit 0
