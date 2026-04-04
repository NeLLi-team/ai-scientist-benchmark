#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${SKILL_DIR}/../.." && pwd)"
BACKEND_DIR="${REPO_ROOT}/s2orc-doc2json"
VENV_DIR="${S2ORC_VENV_DIR:-${BACKEND_DIR}/.venv}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required but was not found on PATH" >&2
  exit 1
fi

if [ ! -d "${BACKEND_DIR}" ]; then
  echo "Vendored backend not found at ${BACKEND_DIR}" >&2
  exit 1
fi

python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/python" -m pip install --upgrade pip setuptools wheel
"${VENV_DIR}/bin/python" -m pip install -r "${BACKEND_DIR}/requirements.txt"

# Imported by the vendored code but not pinned in upstream requirements.
"${VENV_DIR}/bin/python" -m pip install PyPDF2

echo "Created local venv: ${VENV_DIR}"
echo "Activate it with:"
echo "  source ${VENV_DIR}/bin/activate"
