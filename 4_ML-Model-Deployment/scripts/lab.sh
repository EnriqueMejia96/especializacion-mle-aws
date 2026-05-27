#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ $# -eq 0 ]]; then
  python -m src.lab_runner list
  exit 0
fi

case "$1" in
  all)
    python -m src.lab_runner all
    ;;
  list)
    python -m src.lab_runner list
    ;;
  cleanup)
    python -m src.lab_runner cleanup
    ;;
  full-cleanup)
    python -m src.lab_runner full-cleanup
    ;;
  step)
    shift
    python -m src.lab_runner step "$@"
    ;;
  *)
    python -m src.lab_runner step "$1"
    ;;
esac
