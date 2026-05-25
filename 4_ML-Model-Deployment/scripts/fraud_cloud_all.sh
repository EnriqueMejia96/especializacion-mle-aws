#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

python -m src.lab_runner all

echo "Flujo fraude cloud completado. Ejecuta python -m src.lab_runner cleanup para borrar endpoint/model/Feature Groups."
echo "Para teardown total opcional: python -m src.lab_runner full-cleanup"
