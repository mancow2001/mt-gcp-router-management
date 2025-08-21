#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="mt-gcp-daemon"
INSTALL_DIR="/opt/${SERVICE_NAME}"
VENV_DIR="${INSTALL_DIR}/venv"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
ENV_FILE="${INSTALL_DIR}/.env"

# ---- Uninstall mode ---------------------------------------------------------
if [[ "${1:-}" == "--uninstall" || "${1:-}" == "-u" ]]; then
  echo "[*] Uninstalling ${SERVICE_NAME} ..."
  systemctl stop "${SERVICE_NAME}" 2>/dev/null || true
  systemctl disable "${SERVICE_NAME}" 2>/dev/null || true
  rm -f "${SERVICE_FILE}" || true
  rm -rf "${INSTALL_DIR}" || true
  systemctl daemon-reload || true
  echo "[✓] Uninstalled ${SERVICE_NAME}."
  exit 0
fi

# ---- Helpers ----------------------------------------------------------------
need_cmd() { command -v "$1" >/dev/null 2>&1; }

py_ok() {
  local bin="$1"
  if ! need_cmd "$bin"; then return 1; fi
  local ver major minor
  ver="$("$bin" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" || return 1
  major="${ver%%.*}"; minor="${ver##*.}"
  [[ "$major" == "3" && "$minor" -ge 10 ]]
}

install_python() {
  echo "[-] Python 3.10+ not found. Installing system defaults..."
  if need_cmd apt; then
    export DEBIAN_FRONTEND=noninteractive
    apt update -y
    apt install -y python3 python3-venv python3-dev rsync
  elif need_cmd dnf; then
    dnf -y install python3 python3-venv python3-devel rsync
  elif need_cmd yum; then
    yum -y install python3 python3-venv python3-devel rsync
  else
    echo "[-] Unsupported distro (no apt/dnf/yum). Install Python ≥3.10 manually and re-run."
    exit 1
  fi
}

require_root() {
  if [[ $EUID -ne 0 ]]; then
    echo "[-] This script must be run as root (or via sudo)."
    exit 1
  fi
}

# ---- Begin Install -----------------------------------------------------------
require_root

if ! py_ok python3; then
  install_python
fi

if ! py_ok python3; then
  echo "[-] Python 3.10+ not available after install."
  exit 1
fi

PY_BIN="python3"
echo "[+] Using Python: ${PY_BIN} ($("${PY_BIN}" -V 2>&1))"

echo "[*] Creating install dir: ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"
rsync -a --delete --exclude 'venv' ./ "${INSTALL_DIR}/"

# Discover the src directory
echo "[*] Locating source directory (src/mt_gcp_daemon) ..."
PKG_DIR="$(find "${INSTALL_DIR}" -type d -path '*/src/mt_gcp_daemon' -print -quit || true)"
if [[ -z "${PKG_DIR}" ]]; then
  echo "[-] Could not find src/mt_gcp_daemon in ${INSTALL_DIR}."
  exit 1
fi
SRC_ROOT="$(dirname "${PKG_DIR}")"
echo "[+] Found src directory: ${SRC_ROOT}"

# Create venv
echo "[*] Creating virtual environment ..."
"${PY_BIN}" -m venv "${VENV_DIR}"
# shellcheck disable=SC1090
source "${VENV_DIR}/bin/activate"

echo "[*] Upgrading pip and installing dependencies ..."
python -m pip install --upgrade pip wheel setuptools

REQ_FILE="$(find "${INSTALL_DIR}" -maxdepth 3 -iname 'requirements.txt' -print -quit || true)"
if [[ -n "${REQ_FILE}" ]]; then
  pip install -r "${REQ_FILE}"
else
  echo "[!] No requirements.txt found. Skipping dependency install."
fi

# Ensure .env exists
if [[ ! -f "${ENV_FILE}" ]]; then
  if [[ -f "${INSTALL_DIR}/.env.sample" ]]; then
    cp "${INSTALL_DIR}/.env.sample" "${ENV_FILE}"
  elif [[ -f "${INSTALL_DIR}/.env.example" ]]; then
    cp "${INSTALL_DIR}/.env.example" "${ENV_FILE}"
  else
    touch "${ENV_FILE}"
  fi
fi

# Write systemd unit
echo "[*] Writing systemd unit: ${SERVICE_FILE}"
cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=MT GCP Healthcheck & Cloudflare Failover Daemon
After=network.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
Environment=PYTHONPATH=${SRC_ROOT}:\$PYTHONPATH
EnvironmentFile=${ENV_FILE}
ExecStart=${VENV_DIR}/bin/python -u -m mt_gcp_daemon
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

chmod 0644 "${SERVICE_FILE}"

echo "[*] Reloading systemd, enabling and starting service ..."
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

echo "[✓] ${SERVICE_NAME} installed and running."
echo "    - Service: systemctl status ${SERVICE_NAME}"
echo "    - Logs:    journalctl -u ${SERVICE_NAME} -f"
