$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if ($args.Count -eq 0) {
    python -m src.lab_runner list
    exit 0
}

switch ($args[0]) {
    "all" {
        python -m src.lab_runner all
    }
    "list" {
        python -m src.lab_runner list
    }
    "cleanup" {
        python -m src.lab_runner cleanup
    }
    "full-cleanup" {
        python -m src.lab_runner full-cleanup
    }
    "step" {
        if ($args.Count -lt 2) {
            throw "Use: scripts\lab.ps1 step <step-id>"
        }
        python -m src.lab_runner step $args[1]
    }
    default {
        python -m src.lab_runner step $args[0]
    }
}
