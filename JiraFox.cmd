@echo off
rem Ouvre la fenetre de pilotage de JiraFox (demarrer, arreter, mettre a jour, migrer).
start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0tools\jirafox-gui.ps1"
