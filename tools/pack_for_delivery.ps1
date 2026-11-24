# =============================================================================
#  pack_for_delivery.ps1 - build a portable zip that works on the receiver PC
#
#  Guarantees for the person who receives the package:
#    * .venv / .setup.lock are never packed (they pin absolute interpreter
#      paths of the author machine and are unusable anywhere else);
#    * every packed file and folder is writable: the read-only attribute is
#      stripped from the whole staging tree before compression;
#    * every packed object inherits a plain ACL from the extraction folder:
#      explicit / restrictive ACEs copied from the author machine are removed
#      with "icacls /reset", so the receiver can create .venv, logs\ and
#      .setup.lock without an access-denied error;
#    * the source tree is only read - nothing is deleted, moved or rewritten,
#      and the local .venv stays fully usable.
#
#  Usage:
#    powershell -NoProfile -ExecutionPolicy Bypass -File tools\pack_for_delivery.ps1
#    ... -KeepResults          keep the results\ folder in the package
#    ... -Output D:\x\pkg.zip  explicit output path
#    ... -KeepStaging          keep the temporary staging folder for debugging
#
#  ASCII only on purpose: Windows PowerShell 5.1 must never guess a code page.
# =============================================================================

param(
    [string]$Source = "",
    [string]$Output = "",
    [string]$Name   = "DetailPreservingImageUpscalingSystem",
    [switch]$KeepResults,
    [switch]$KeepStaging
)

$ErrorActionPreference = "Stop"

# --------------------------------------------------------------- resolve paths

if (-not $Source) {
    $Source = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Definition)
}
if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
    throw "Source folder not found: $Source"
}
$Source = (Resolve-Path -LiteralPath $Source).Path

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
if (-not $Output) {
    $deliveryDir = Join-Path $Source "delivery"
    if (-not (Test-Path -LiteralPath $deliveryDir)) {
        New-Item -ItemType Directory -Path $deliveryDir -Force | Out-Null
    }
    $Output = Join-Path $deliveryDir ($Name + "_portable_" + $stamp + ".zip")
} else {
    $OutputParent = Split-Path -Parent $Output
    if ($OutputParent -and -not (Test-Path -LiteralPath $OutputParent)) {
        New-Item -ItemType Directory -Path $OutputParent -Force | Out-Null
    }
    $Output = [System.IO.Path]::GetFullPath($Output)
}

Write-Host ""
Write-Host "  Detail-Preserving Image Upscaling System - delivery packer" -ForegroundColor Cyan
Write-Host "  ---------------------------------------------------------" -ForegroundColor Cyan
Write-Host "  Source : $Source"
Write-Host "  Output : $Output"
Write-Host ""

# ------------------------------------------------------------- staging folder

$stagingRoot = Join-Path $env:TEMP ("dpius_pack_" + $stamp)
$stagingBase = Join-Path $stagingRoot $Name
if (Test-Path -LiteralPath $stagingRoot) {
    Remove-Item -LiteralPath $stagingRoot -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Path $stagingBase -Force | Out-Null

# ----------------------------------------------------------------- copy phase

$excludeDirs = @(
    ".venv",
    ".venv.broken-*",
    "__pycache__",
    ".pytest_cache",
    ".git",
    ".idea",
    ".vs",
    ".vscode",
    "delivery",
    "dist",
    "logs"
)
if (-not $KeepResults) { $excludeDirs += "results" }

$excludeFiles = @(
    ".setup.lock",
    "*.pyc",
    "*.pyo",
    "*.log",
    "Thumbs.db",
    ".DS_Store",
    "temp_out.txt",
    "temp_err.txt",
    ".verify_runtime.py",
    ".verify_out.txt",
    ".verify_err.txt",
    ".probe_out.txt",
    ".probe_err.txt"
)

$robocopyArgs = @(
    "`"$Source`"",
    "`"$stagingBase`"",
    "/E", "/R:1", "/W:1",
    "/NFL", "/NDL", "/NJH", "/NJS", "/NP"
)
$robocopyArgs += "/XD"
foreach ($dir in $excludeDirs) { $robocopyArgs += "`"$dir`"" }
$robocopyArgs += "/XF"
foreach ($file in $excludeFiles) { $robocopyArgs += "`"$file`"" }

Write-Host "  [1/5] Copying the source tree (read-only operation) ..." -ForegroundColor Yellow
$rc = Start-Process -FilePath "robocopy.exe" -ArgumentList $robocopyArgs -Wait -PassThru -NoNewWindow
# robocopy: 0-7 are success codes, 8 and above are real failures.
if ($rc.ExitCode -ge 8) {
    throw "robocopy failed with exit code $($rc.ExitCode)."
}

# --------------------------------------------------- strip the read-only flag

Write-Host "  [2/5] Stripping read-only attributes from the staging tree ..." -ForegroundColor Yellow
try {
    $attribOut = & attrib.exe -R (Join-Path $stagingBase "*.*") /S /D 2>&1
    $remaining = @(Get-ChildItem -LiteralPath $stagingBase -Recurse -Force -ErrorAction SilentlyContinue |
                   Where-Object { $_.Attributes -band [System.IO.FileAttributes]::ReadOnly })
    if ($remaining.Count -gt 0) {
        foreach ($item in $remaining) {
            try { $item.Attributes = $item.Attributes -band (-bnot [System.IO.FileAttributes]::ReadOnly) } catch { }
        }
    }
    Write-Host "          read-only attribute cleared" -ForegroundColor DarkGray
} catch {
    Write-Host "          WARNING: could not clear read-only attributes: $($_.Exception.Message)" -ForegroundColor Yellow
}

# ------------------------------------------------- reset inherited/explicit ACL

Write-Host "  [3/5] Normalising ACLs so the receiver inherits write access ..." -ForegroundColor Yellow
try {
    # /reset removes every explicit ACE and restores inheritance from the
    # parent folder, so no restrictive rule travels with the package.
    $null = & icacls.exe $stagingBase /reset /T /C /Q 2>&1
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $null = & icacls.exe $stagingBase /grant ("{0}:(OI)(CI)F" -f $identity) /T /C /Q 2>&1
    Write-Host "          ACL reset done (inheritance restored)" -ForegroundColor DarkGray
} catch {
    Write-Host "          WARNING: could not reset ACLs: $($_.Exception.Message)" -ForegroundColor Yellow
}

# ------------------------------------------------------------- sanity checks

Write-Host "  [4/5] Validating the staging tree ..." -ForegroundColor Yellow
$required = @("Start.cmd", "launcher.ps1", "setup.ps1", "requirements.txt", "app.py", "bootstrap.py")
foreach ($item in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $stagingBase $item))) {
        throw "Required file missing from the package: $item"
    }
}
foreach ($banned in @(".venv", ".setup.lock")) {
    if (Test-Path -LiteralPath (Join-Path $stagingBase $banned)) {
        throw "Forbidden path ended up in the package: $banned"
    }
}
$stagedFiles = @(Get-ChildItem -LiteralPath $stagingBase -Recurse -File -Force -ErrorAction SilentlyContinue)
$stagedBytes = ($stagedFiles | Measure-Object -Property Length -Sum).Sum
if (-not $stagedBytes) { $stagedBytes = 0 }

# ---------------------------------------------------------------- compress

Write-Host "  [5/5] Compressing ..." -ForegroundColor Yellow
Add-Type -AssemblyName System.IO.Compression.FileSystem
# ZipArchive / ZipArchiveMode live in System.IO.Compression, which Windows
# PowerShell 5.1 does not load by default.
try { Add-Type -AssemblyName System.IO.Compression -ErrorAction Stop } catch { }
if (Test-Path -LiteralPath $Output) { Remove-Item -LiteralPath $Output -Force }

# Written entry by entry (instead of CreateFromDirectory) so the archive has
# exactly one top level folder - $Name - and never leaks the staging directory
# name or a timestamp into the path the receiver sees after extraction.
$zipStream = [System.IO.File]::Open($Output, [System.IO.FileMode]::Create)
try {
    $archive = New-Object System.IO.Compression.ZipArchive($zipStream, [System.IO.Compression.ZipArchiveMode]::Create)
    try {
        $prefixLength = $stagingBase.Length + 1
        $added = 0
        foreach ($file in (Get-ChildItem -LiteralPath $stagingBase -Recurse -File -Force -ErrorAction SilentlyContinue)) {
            $relative = $file.FullName.Substring($prefixLength).Replace("\", "/")
            $entry = $archive.CreateEntry(($Name + "/" + $relative), [System.IO.Compression.CompressionLevel]::Optimal)
            $entry.LastWriteTime = $file.LastWriteTime
            $entryStream = $entry.Open()
            try {
                $sourceStream = [System.IO.File]::OpenRead($file.FullName)
                try { $sourceStream.CopyTo($entryStream) } finally { $sourceStream.Dispose() }
            } finally { $entryStream.Dispose() }
            $added++
        }
        Write-Host ("          {0} file(s) written into the archive" -f $added) -ForegroundColor DarkGray
    } finally {
        $archive.Dispose()
    }
} finally {
    $zipStream.Dispose()
}

# ------------------------------------------------------------------ verify

$zip = [System.IO.Compression.ZipFile]::OpenRead($Output)
try {
    # Some .NET Framework builds store entry names with a backslash separator,
    # so normalise before matching.
    $entryNames = @($zip.Entries | ForEach-Object { $_.FullName.Replace("\", "/") })
} finally {
    $zip.Dispose()
}
$hasVenv    = @($entryNames | Where-Object { $_ -match '(^|/)\.venv(/|$)' }).Count
$hasLock    = @($entryNames | Where-Object { $_ -match '(^|/)\.setup\.lock$' }).Count
$hasStart   = @($entryNames | Where-Object { $_ -match "(^|/)$Name/Start\.cmd$" }).Count
if ($hasVenv -gt 0) { throw "Verification failed: .venv is present inside the zip." }
if ($hasLock -gt 0) { throw "Verification failed: .setup.lock is present inside the zip." }
if ($hasStart -lt 1) { throw "Verification failed: Start.cmd is not at the archive root." }

$hash = (Get-FileHash -LiteralPath $Output -Algorithm SHA256).Hash.ToLowerInvariant()
$zipSize = (Get-Item -LiteralPath $Output).Length

if (-not $KeepStaging) {
    Remove-Item -LiteralPath $stagingRoot -Recurse -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "  Staging kept at: $stagingRoot" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "  Package ready." -ForegroundColor Green
Write-Host "  ---------------------------------------------------------" -ForegroundColor Cyan
Write-Host ("  Files      : {0}" -f $stagedFiles.Count)
Write-Host ("  Raw size   : {0:N1} MB" -f ($stagedBytes / 1MB))
Write-Host ("  Zip size   : {0:N2} MB" -f ($zipSize / 1MB))
Write-Host ("  SHA256     : {0}" -f $hash)
Write-Host ("  Archive    : {0}" -f $Output)
Write-Host ""
Write-Host "  Receiver: extract the zip to a writable local folder, then double" -ForegroundColor DarkGray
Write-Host "  click Start.cmd. No .venv, no lock file, no read-only leftovers." -ForegroundColor DarkGray
Write-Host ""
exit 0
