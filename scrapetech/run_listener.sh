#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

REPO_DIR="${ROOT_DIR}"
if [[ -f "${ROOT_DIR}/__init__.py" ]]; then
  REPO_DIR="$(cd "${ROOT_DIR}/.." && pwd)"
fi

VENV_PATH="${REPO_DIR}/scrapetech/.venv/bin/activate"
if [[ ! -f "${VENV_PATH}" && -f "${REPO_DIR}/.venv/bin/activate" ]]; then
  VENV_PATH="${REPO_DIR}/.venv/bin/activate"
fi

if [[ ! -f "${VENV_PATH}" ]]; then
  echo "Venv not found at ${VENV_PATH}"
  echo "Create it in ${ROOT_DIR}/scrapetech or update this script."
  exit 1
fi

source "${VENV_PATH}"
cd "${REPO_DIR}"
python -m scrapetech.telethon_listener
