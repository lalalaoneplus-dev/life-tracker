# One-line Windows installer. Safe when piped (irm URL | iex) and when run from a checkout.
# Windows PowerShell 5.1 and PowerShell 7. No administrator rights.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
if (Test-Path variable:PSNativeCommandUseErrorActionPreference) {
  $PSNativeCommandUseErrorActionPreference = $false
}

$DefaultInstallDir = Join-Path $env:USERPROFILE "life-tracker"
$DefaultRepoUrl = "https://github.com/lalalaoneplus-dev/life-tracker.git"
$DefaultZipUrl = "https://github.com/lalalaoneplus-dev/life-tracker/archive/refs/heads/main.zip"

function Die([string]$Message) {
  [Console]::Error.WriteLine($Message)
  exit 1
}

function Test-Repo([string]$Dir) {
  if (-not $Dir) { return $false }
  $run = Join-Path $Dir "run.sh"
  $req = Join-Path $Dir "requirements.txt"
  $app = Join-Path $Dir "app"
  return ((Test-Path -LiteralPath $run) -and (Test-Path -LiteralPath $req) -and (Test-Path -LiteralPath $app))
}

function Test-SamePath([string]$A, [string]$B) {
  $fa = [System.IO.Path]::GetFullPath($A).TrimEnd('\', '/')
  $fb = [System.IO.Path]::GetFullPath($B).TrimEnd('\', '/')
  return [string]::Equals($fa, $fb, [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-ArchiveUrl([string]$RepoUrl, [string]$Fallback) {
  if ($RepoUrl -match '^https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$') {
    return ("https://github.com/" + $Matches[1] + "/" + $Matches[2] + "/archive/refs/heads/main.zip")
  }
  return $Fallback
}

function Copy-Tree([string]$Src, [string]$Dest) {
  if (-not (Test-Path -LiteralPath $Dest)) {
    New-Item -ItemType Directory -Path $Dest | Out-Null
  }
  & robocopy $Src $Dest /E /XD .git .venv node_modules .node .run .next __pycache__ .pytest_cache /XF *.pyc *.log .env /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
  if ($LASTEXITCODE -ge 8) {
    Die ("Could not copy sources into " + $Dest)
  }
}

function Sync-Source([string]$Dest) {
  if ($env:REPO_URL) { $url = $env:REPO_URL } else { $url = $DefaultRepoUrl }
  if (-not (Test-Path -LiteralPath $Dest)) {
    New-Item -ItemType Directory -Path $Dest | Out-Null
  }
  if (Test-Path -LiteralPath $url -PathType Container) {
    if (Test-SamePath $url $Dest) { return }
    Copy-Tree $url $Dest
    return
  }
  $git = Get-Command git -ErrorAction SilentlyContinue
  if ($git) {
    $gitDir = Join-Path $Dest ".git"
    if (Test-Path -LiteralPath $gitDir) {
      & git -C $Dest pull --ff-only
      if ($LASTEXITCODE -ne 0) { Die ("git pull failed in " + $Dest) }
      return
    }
    $children = @(Get-ChildItem -Force -LiteralPath $Dest -ErrorAction SilentlyContinue)
    if ($children.Count -eq 0) {
      & git clone $url $Dest
      if ($LASTEXITCODE -ne 0) { Die ("git clone failed for " + $url) }
      return
    }
    return
  }
  $zipUrl = Get-ArchiveUrl $url $DefaultZipUrl
  $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("src-" + [guid]::NewGuid().ToString("n"))
  New-Item -ItemType Directory -Path $tmp | Out-Null
  try {
    $zip = Join-Path $tmp "src.zip"
    Invoke-WebRequest -Uri $zipUrl -OutFile $zip -UseBasicParsing
    $unpack = Join-Path $tmp "unpack"
    Expand-Archive -LiteralPath $zip -DestinationPath $unpack -Force
    $inner = @(Get-ChildItem -LiteralPath $unpack -Directory)
    if ($inner.Count -lt 1) { Die "Downloaded archive was empty." }
    Copy-Tree $inner[0].FullName $Dest
  } finally {
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
  }
}

function Ensure-Uv {
  $bin = Join-Path (Join-Path $env:USERPROFILE ".local") "bin"
  $uvExe = Join-Path $bin "uv.exe"
  if (Test-Path -LiteralPath $uvExe) {
    $env:PATH = $bin + ";" + $env:PATH
  }
  if (Get-Command uv -ErrorAction SilentlyContinue) { return }
  irm https://astral.sh/uv/install.ps1 | iex
  if ($env:XDG_BIN_HOME) {
    $xdgUv = Join-Path $env:XDG_BIN_HOME "uv.exe"
    if (Test-Path -LiteralPath $xdgUv) {
      $env:PATH = $env:XDG_BIN_HOME + ";" + $env:PATH
    }
  }
  if (Test-Path -LiteralPath $uvExe) {
    $env:PATH = $bin + ";" + $env:PATH
  }
  if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Die ("uv is required. It installs into " + $bin + ".")
  }
}

function Test-PortListen([string]$Port) {
  $pattern = ":" + $Port + "\s"
  $lines = netstat -ano | Select-String -Pattern $pattern
  if (-not $lines) { return $false }
  foreach ($line in @($lines)) {
    if ($line -and ($line.Line -match "LISTENING")) { return $true }
  }
  return $false
}

function Wait-Http([string]$Url) {
  $i = 0
  while ($i -lt 90) {
    try {
      $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
      if ([int]$resp.StatusCode -eq 200) { return }
    } catch {
    }
    Start-Sleep -Milliseconds 500
    $i++
  }
  Die ("Timed out waiting for " + $Url)
}

function Write-EnvIfMissing([string]$Repo, [string]$Python) {
  if (-not $env:LOCALAPPDATA) {
    $env:LOCALAPPDATA = Join-Path (Join-Path $env:USERPROFILE "AppData") "Local"
  }
  $envDir = Join-Path (Join-Path $env:USERPROFILE ".config") "life-tracker"
  $envFile = Join-Path $envDir "env"
  $dbDir = Join-Path $env:LOCALAPPDATA "life-tracker"
  $dbPath = Join-Path $dbDir "tracker.db"
  if (-not (Test-Path -LiteralPath $envDir)) { New-Item -ItemType Directory -Path $envDir | Out-Null }
  if (-not (Test-Path -LiteralPath $dbDir)) { New-Item -ItemType Directory -Path $dbDir | Out-Null }
  if (Test-Path -LiteralPath $envFile) { return }
  $token = (& $Python -c "import secrets; print(secrets.token_urlsafe(32))")
  $secret = (& $Python -c "import secrets; print(secrets.token_urlsafe(32))")
  if ($LASTEXITCODE -ne 0) { Die "Could not generate secrets." }
  $token = ([string]$token).Trim()
  $secret = ([string]$secret).Trim()
  $content = "LIFE_TRACKER_AGENT_TOKEN=" + $token + "`nLIFE_TRACKER_SESSION_SECRET=" + $secret + "`nLIFE_TRACKER_DB=" + $dbPath + "`n"
  $utf8 = New-Object System.Text.UTF8Encoding $false
  [System.IO.File]::WriteAllText($envFile, $content, $utf8)
}

if ($env:PORT) { $AppPort = [string]$env:PORT } else { $AppPort = "8787" }
if ($AppPort -notmatch '^[0-9]+$') { Die "PORT must be a number." }
$AppUrl = "http://127.0.0.1:" + $AppPort

$scriptPath = $PSCommandPath
if (-not $scriptPath) { $scriptPath = $MyInvocation.MyCommand.Path }
$checkout = ""
if ($scriptPath) {
  $checkout = Split-Path -Parent $scriptPath
  if (-not (Test-Repo $checkout)) { $checkout = "" }
}

if ($checkout -and -not $env:INSTALL_DIR) {
  $RepoDir = $checkout
} elseif (-not $env:INSTALL_DIR -and -not $env:REPO_URL -and (Test-Repo (Get-Location).Path)) {
  $RepoDir = (Get-Location).Path
} else {
  if ($env:INSTALL_DIR) { $RepoDir = $env:INSTALL_DIR } else { $RepoDir = $DefaultInstallDir }
  Sync-Source $RepoDir
}

if (-not (Test-Repo $RepoDir)) { Die ("life-tracker files were not found in " + $RepoDir) }

Ensure-Uv

$py = Join-Path (Join-Path $RepoDir ".venv") "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $py)) {
  & uv venv --python 3.12 (Join-Path $RepoDir ".venv")
  if ($LASTEXITCODE -ne 0) { Die "uv venv failed." }
}
& uv pip install -r (Join-Path $RepoDir "requirements.txt") --python $py
if ($LASTEXITCODE -ne 0) { Die "uv pip install failed." }

Write-EnvIfMissing $RepoDir $py

if ($env:NO_START -ne "1") {
  & (Join-Path $RepoDir "stop.ps1")
  Start-Sleep -Milliseconds 300
  if (Test-PortListen $AppPort) { Start-Sleep -Milliseconds 500 }
  if (Test-PortListen $AppPort) {
    Die ("Port " + $AppPort + " is in use. Choose another with PORT=<port>.")
  }
  $runDir = Join-Path $RepoDir ".run"
  if (-not (Test-Path -LiteralPath $runDir)) { New-Item -ItemType Directory -Path $runDir | Out-Null }
  $env:LIFE_TRACKER_PIDFILE = Join-Path $runDir "life-tracker.pid"
  $env:PORT = $AppPort
  $shell = (Get-Process -Id $PID).Path
  Start-Process -FilePath $shell -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $RepoDir "run.ps1")
  ) -WorkingDirectory $RepoDir -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $RepoDir "life-tracker.log") `
    -RedirectStandardError (Join-Path $RepoDir "life-tracker.err.log") | Out-Null
  Wait-Http ($AppUrl + "/")
  if ($env:NO_OPEN -ne "1") {
    Start-Process $AppUrl | Out-Null
  }
}

Write-Host ("life-tracker is at " + $AppUrl)
Write-Host ("Start again with: " + (Join-Path $RepoDir "run.ps1"))
Write-Host ("Stop with: " + (Join-Path $RepoDir "stop.ps1"))
Write-Host ("Config: " + (Join-Path (Join-Path (Join-Path $env:USERPROFILE ".config") "life-tracker") "env"))
