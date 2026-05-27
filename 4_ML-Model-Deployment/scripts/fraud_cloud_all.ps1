$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

python -m src.lab_runner all

Write-Host "Flujo fraude cloud completado. Ejecuta python -m src.lab_runner cleanup para borrar endpoint/model/Feature Groups."
Write-Host "Para teardown total opcional: python -m src.lab_runner full-cleanup"
