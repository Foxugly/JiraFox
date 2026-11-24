# JiraFox - fonctions communes au script en ligne de commande (jirafox.ps1) et a l'interface
# (jirafox-gui.ps1). A charger avec : . "$PSScriptRoot\jirafox-lib.ps1"

$script:Root = Split-Path -Parent $PSScriptRoot
$script:Python = Join-Path $script:Root ".venv\Scripts\python.exe"
$script:PidFile = Join-Path $script:Root ".jirafox.pid"
$script:LogDir = Join-Path $script:Root "logs"
$script:ServerLog = Join-Path $script:LogDir "server.log"
$script:ServerErr = Join-Path $script:LogDir "server-error.log"
$script:Port = if ($env:JIRAFOX_PORT) { [int]$env:JIRAFOX_PORT } else { 8000 }
$script:Url = "http://127.0.0.1:$($script:Port)/"

# Where messages go: the console by default, the GUI's log box when it sets this scriptblock.
$global:JiraFoxLogger = $null

function Write-JF([string]$Message) {
    if ($global:JiraFoxLogger) { & $global:JiraFoxLogger $Message } else { Write-Host $Message }
}

function Invoke-JFNative([string]$File, [string[]]$Arguments) {
    # Runs a program from the repo root, streams its output, returns its exit code. stderr is
    # read as plain text (pip and git write progress there), never as a PowerShell error.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        Push-Location $script:Root
        & $File @Arguments 2>&1 | ForEach-Object { Write-JF ("  " + "$_") }
        return $LASTEXITCODE
    } finally {
        Pop-Location
        $ErrorActionPreference = $previous
    }
}

function Test-JFInstalled {
    if (-not (Test-Path $script:Python)) {
        Write-JF "JiraFox n'est pas installe : suivez d'abord install.txt."
        return $false
    }
    return $true
}

function Get-JFProcess {
    if (-not (Test-Path $script:PidFile)) { return $null }
    $id = (Get-Content $script:PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if (-not $id) { return $null }
    $process = Get-Process -Id ([int]$id) -ErrorAction SilentlyContinue
    # A recycled PID belongs to another program: only a python.exe counts.
    if ($process -and $process.ProcessName -like "python*") { return $process }
    Remove-Item $script:PidFile -ErrorAction SilentlyContinue
    return $null
}

function Test-JFResponding {
    # A TCP connection is enough (and quick, so the window's status timer never hangs).
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        return $client.ConnectAsync("127.0.0.1", $script:Port).Wait(300) -and $client.Connected
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Get-JFStatus {
    $process = Get-JFProcess
    if (-not $process) { return "Arrete" }
    if (Test-JFResponding) { return "Demarre ($($script:Url))" }
    return "Demarrage en cours..."
}

function Start-JiraFox {
    if (-not (Test-JFInstalled)) { return }
    if (Get-JFProcess) {
        Write-JF "JiraFox tourne deja : $($script:Url)"
        return
    }
    New-Item -ItemType Directory -Force -Path $script:LogDir | Out-Null
    # --noreload: one process to stop; --insecure: static files served with DEBUG off (local use).
    $process = Start-Process -FilePath $script:Python `
        -ArgumentList @("manage.py", "runserver", "127.0.0.1:$($script:Port)", "--noreload", "--insecure") `
        -WorkingDirectory $script:Root -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $script:ServerLog -RedirectStandardError $script:ServerErr
    Set-Content -Path $script:PidFile -Value $process.Id
    Write-JF "Demarrage de JiraFox (PID $($process.Id))..."
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        if ($process.HasExited) {
            Remove-Item $script:PidFile -ErrorAction SilentlyContinue
            Write-JF "Echec du demarrage. Dernieres lignes de $($script:ServerErr) :"
            Get-Content $script:ServerErr -Tail 15 -ErrorAction SilentlyContinue | ForEach-Object { Write-JF "  $_" }
            return
        }
        if (Test-JFResponding) {
            Write-JF "JiraFox est demarre : $($script:Url)"
            return
        }
    }
    Write-JF "JiraFox ne repond pas encore ; voir $($script:ServerErr)."
}

function Stop-JiraFox {
    $process = Get-JFProcess
    if (-not $process) {
        Write-JF "JiraFox est deja arrete."
        return
    }
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    Remove-Item $script:PidFile -ErrorAction SilentlyContinue
    Write-JF "JiraFox est arrete."
}

function Invoke-JFMigrate {
    if (-not (Test-JFInstalled)) { return $false }
    Write-JF "Mise a jour de la base (migrate)..."
    $code = Invoke-JFNative $script:Python @("manage.py", "migrate", "--noinput")
    if ($code -ne 0) { Write-JF "Echec de migrate (code $code)."; return $false }
    Write-JF "Base a jour."
    return $true
}

function Invoke-JFPull {
    # git pull, then the dependencies and the database; restarts JiraFox if it was running.
    if (-not (Test-JFInstalled)) { return }
    $wasRunning = [bool](Get-JFProcess)
    Write-JF "Recuperation du code (git pull)..."
    $code = Invoke-JFNative "git" @("pull", "--ff-only")
    if ($code -ne 0) {
        Write-JF "Echec de git pull (modifications locales ?). Rien d'autre n'a ete fait."
        return
    }
    Write-JF "Dependances (pip install)..."
    $code = Invoke-JFNative $script:Python @("-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", "requirements.txt")
    if ($code -ne 0) { Write-JF "Echec de pip install (code $code)."; return }
    if (-not (Invoke-JFMigrate)) { return }
    if ($wasRunning) {
        Write-JF "Redemarrage pour charger le nouveau code..."
        Stop-JiraFox
        Start-JiraFox
    }
    Write-JF "Mise a jour terminee."
}

function Open-JiraFox {
    Start-Process $script:Url
}
