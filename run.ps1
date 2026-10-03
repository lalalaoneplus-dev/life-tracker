# Load %USERPROFILE%\.config\life-tracker\env and start the app on 127.0.0.1.
# Foreground when run directly. The installer sets LIFE_TRACKER_PIDFILE.
$ErrorActionPreference = "Stop"

Set-Location -LiteralPath $PSScriptRoot

$envFile = Join-Path (Join-Path (Join-Path $env:USERPROFILE ".config") "life-tracker") "env"
if (-not (Test-Path -LiteralPath $envFile)) {
  [Console]::Error.WriteLine("missing $envFile")
  exit 1
}

Get-Content -LiteralPath $envFile | ForEach-Object {
  $line = [string]$_
  $line = $line.Trim()
  if ($line.Length -eq 0) { return }
  if ($line.StartsWith("#")) { return }
  $eq = $line.IndexOf("=")
  if ($eq -lt 1) { return }
  $name = $line.Substring(0, $eq).Trim()
  $value = $line.Substring($eq + 1)
  if ($name.StartsWith("LIFE_TRACKER_")) {
    Set-Item -Path ("Env:" + $name) -Value $value
  }
}

$root = (Get-Location).Path
$py = Join-Path (Join-Path $root ".venv") "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $py)) {
  $py = "python"
}
if (-not $env:PORT) {
  $env:PORT = "8787"
}

if ($env:LIFE_TRACKER_PIDFILE) {
  $pidDir = Split-Path -Parent $env:LIFE_TRACKER_PIDFILE
  if ($pidDir -and -not (Test-Path -LiteralPath $pidDir)) {
    New-Item -ItemType Directory -Path $pidDir | Out-Null
  }
  $ticks = [Diagnostics.Process]::GetCurrentProcess().StartTime.ToUniversalTime().Ticks
  $utf8 = New-Object System.Text.UTF8Encoding $false
  [System.IO.File]::WriteAllText($env:LIFE_TRACKER_PIDFILE, "$PID`n$ticks`n", $utf8)
}

& $py -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port $env:PORT @args
if ($null -eq $LASTEXITCODE) { exit 0 }
exit $LASTEXITCODE
