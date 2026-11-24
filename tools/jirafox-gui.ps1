# JiraFox - petite fenêtre Windows : démarrer, arrêter, ouvrir, mettre à jour (pull), migrer.
# Lancée par JiraFox.cmd ou le raccourci du bureau créé par install.txt.

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

. "$PSScriptRoot\jirafox-lib.ps1"

$form = New-Object System.Windows.Forms.Form
$form.Text = "JiraFox"
$form.Size = New-Object System.Drawing.Size(640, 460)
$form.MinimumSize = New-Object System.Drawing.Size(520, 360)
$form.StartPosition = "CenterScreen"
$form.Font = New-Object System.Drawing.Font("Segoe UI", 9)

$status = New-Object System.Windows.Forms.Label
$status.Location = New-Object System.Drawing.Point(12, 12)
$status.Size = New-Object System.Drawing.Size(600, 24)
$status.Anchor = "Top, Left, Right"
$status.Font = New-Object System.Drawing.Font("Segoe UI", 11, [System.Drawing.FontStyle]::Bold)
$form.Controls.Add($status)

$log = New-Object System.Windows.Forms.TextBox
$log.Multiline = $true
$log.ReadOnly = $true
$log.ScrollBars = "Vertical"
$log.Font = New-Object System.Drawing.Font("Consolas", 9)
$log.Location = New-Object System.Drawing.Point(12, 84)
$log.Size = New-Object System.Drawing.Size(600, 320)
$log.Anchor = "Top, Bottom, Left, Right"
$form.Controls.Add($log)

$global:JiraFoxLogger = {
    param($message)
    $log.AppendText("$(Get-Date -Format 'HH:mm:ss')  $message`r`n")
    [System.Windows.Forms.Application]::DoEvents()
}

$buttons = @()

function Update-Status {
    $text = Get-JFStatus
    $status.Text = "JiraFox : $text"
    $running = [bool](Get-JFProcess)
    $status.ForeColor = if ($running) { [System.Drawing.Color]::SeaGreen } else { [System.Drawing.Color]::DimGray }
    $startButton.Enabled = -not $running
    $stopButton.Enabled = $running
    $openButton.Enabled = $running
}

function Invoke-Busy([scriptblock]$Work) {
    # Long actions (pull, pip) block the window: say so and grey the buttons meanwhile.
    $form.UseWaitCursor = $true
    $buttons | ForEach-Object { $_.Enabled = $false }
    [System.Windows.Forms.Application]::DoEvents()
    try { & $Work } catch { Write-JF "Erreur : $($_.Exception.Message)" }
    finally {
        $form.UseWaitCursor = $false
        $buttons | ForEach-Object { $_.Enabled = $true }
        Update-Status
    }
}

function New-JFButton([string]$Text, [int]$X, [scriptblock]$OnClick) {
    $button = New-Object System.Windows.Forms.Button
    $button.Text = $Text
    $button.Location = New-Object System.Drawing.Point($X, 44)
    $button.Size = New-Object System.Drawing.Size(112, 30)
    $button.Add_Click($OnClick)
    $form.Controls.Add($button)
    $script:buttons += $button
    return $button
}

$startButton = New-JFButton "Démarrer" 12 { Invoke-Busy { Start-JiraFox } }
$stopButton = New-JFButton "Arrêter" 132 { Invoke-Busy { Stop-JiraFox } }
$openButton = New-JFButton "Ouvrir" 252 { Open-JiraFox }
$pullButton = New-JFButton "Mettre à jour" 372 { Invoke-Busy { Invoke-JFPull } }
$migrateButton = New-JFButton "Migrer la base" 492 { Invoke-Busy { [void](Invoke-JFMigrate) } }

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 3000
$timer.Add_Tick({ if (-not $form.UseWaitCursor) { Update-Status } })
$timer.Start()

$form.Add_FormClosing({
    param($sender, $eventArgs)
    if (Get-JFProcess) {
        $answer = [System.Windows.Forms.MessageBox]::Show(
            "JiraFox tourne encore. L'arrêter avant de fermer ?", "JiraFox",
            [System.Windows.Forms.MessageBoxButtons]::YesNoCancel, [System.Windows.Forms.MessageBoxIcon]::Question)
        if ($answer -eq [System.Windows.Forms.DialogResult]::Cancel) { $eventArgs.Cancel = $true; return }
        if ($answer -eq [System.Windows.Forms.DialogResult]::Yes) { Stop-JiraFox }
    }
    $timer.Stop()
})

Write-JF "Dossier : $($script:Root)"
if (Test-JFInstalled) { Write-JF "Prêt. Adresse : $($script:Url)" }
Update-Status
[void]$form.ShowDialog()
