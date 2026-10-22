# Run from the extracted project directory. No token is stored by this script.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Assert-LastCommand([string]$Message) {
    if ($LASTEXITCODE -ne 0) { throw $Message }
}

Push-Location -LiteralPath $PSScriptRoot
try {
    Get-Command gh, git, python -ErrorAction Stop | Out-Null
    & gh auth status
    Assert-LastCommand 'GitHub login fehlt. Bitte zuerst gh auth login ausfuehren.'
    $role = (& gh api user/memberships/orgs/Tracht-Digital-Solutions --jq .role).Trim()
    Assert-LastCommand 'Mitgliedschaft in Tracht-Digital-Solutions konnte nicht ermittelt werden.'
    if ($role -ne 'admin') { throw 'Bitte mit einem Admin-Konto der Organisation Tracht-Digital-Solutions anmelden.' }
    & python -m unittest discover -s tests -v
    Assert-LastCommand 'Tests fehlgeschlagen.'
    & python scripts/marketplace.py validate
    Assert-LastCommand 'Katalogpruefung fehlgeschlagen.'
    if (Test-Path -LiteralPath '.git') { throw 'Hier existiert bereits ein Git-Repository. Bitte ein frisch entpacktes Projekt verwenden.' }
    & git init -b main
    Assert-LastCommand 'Git-Initialisierung fehlgeschlagen.'
    & git config user.name 'Julian Tracht'
    Assert-LastCommand 'Lokale Git-Konfiguration fehlgeschlagen.'
    & git config user.email 'contact@tracht-digital.de'
    Assert-LastCommand 'Lokale Git-Konfiguration fehlgeschlagen.'
    & git add -- .
    Assert-LastCommand 'Dateien konnten nicht vorgemerkt werden.'
    & git commit -m 'feat: add automated ChatGPT plugin marketplace'
    Assert-LastCommand 'Initialer Commit fehlgeschlagen.'
    & gh repo create Tracht-Digital-Solutions/chatgpt-plugin-marketplace --public --source . --remote origin --push --description 'Automatically assembled marketplace for Tracht Digital ChatGPT and Codex plugins'
    Assert-LastCommand 'Repository konnte nicht erstellt oder gepusht werden. Den vorhandenen lokalen Commit beibehalten; vor erneutem Versuch GitHub-Status pruefen.'
    Write-Host 'Erstellt: https://github.com/Tracht-Digital-Solutions/chatgpt-plugin-marketplace'
    Write-Host 'Die erste Sync-Pipeline startet nach dem Push automatisch.'
}
finally { Pop-Location }
