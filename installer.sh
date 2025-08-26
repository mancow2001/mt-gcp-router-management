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

# Require Python 3.10+ (no longer accept 3.9)
py_ok() {
  local bin="$1"
  if ! need_cmd "$bin"; then return 1; fi
  local ver major minor
  ver="$("$bin" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" || return 1
  major="${ver%%.*}"; minor="${ver##*.}"
  [[ "$major" == "3" && "$minor" -ge 10 ]]
}

detect_like_rhel() {
  if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    [[ "${ID_LIKE:-}" =~ (rhel|fedora|centos) ]] || [[ "${ID:-}" =~ (rhel|centos|rocky|almalinux) ]]
  else
    return 1
  fi
}

require_root() {
  if [[ $EUID -ne 0 ]]; then
    echo "[-] This script must be run as root (or via sudo)."
    exit 1
  fi
}

# ---- Updated: prefer direct versioned RPMs (3.12/3.11/3.10), then modules, then generic ----
install_python_rpm() {
  local candidates=("3.12" "3.11" "3.10")

  if need_cmd dnf; then
    echo "[*] Detected dnf-based system."
    dnf -y install dnf-plugins-core >/dev/null 2>&1 || true
    dnf -y config-manager --set-enabled appstream >/dev/null 2>&1 || true
    dnf -y config-manager --set-enabled crb >/dev/null 2>&1 || true
    dnf -y config-manager --set-enabled powertools >/dev/null 2>&1 || true

    # 1) Direct versioned packages (works on Rocky 9.6 for 3.11/3.12)
    for s in "${candidates[@]}"; do
      if dnf list -q available "python3.${s#3.}" >/dev/null 2>&1 || dnf list -q available "python${s}" >/dev/null 2>&1; then
        if dnf -y install "python3.${s#3.}" "python3.${s#3.}-devel" rsync >/dev/null 2>&1 \
           || dnf -y install "python${s}" "python${s}-devel" rsync >/dev/null 2>&1; then
          echo "[+] Installed Python ${s} via direct RPMs."
          return 0
        fi
      fi
    done

    # 2) AppStream modules (if present)
    if dnf module list python3 >/dev/null 2>&1; then
      echo "[*] AppStream modules detected. Attempting module enable..."
      dnf -y module reset python3 || true
      for s in "${candidates[@]}"; do
        if dnf -y module enable "python3:${s}" >/dev/null 2>&1; then
          if dnf -y install "python3.${s#3.}" "python3.${s#3.}-devel" rsync >/dev/null 2>&1 \
             || dnf -y install "python${s}" "python${s}-devel" rsync >/dev/null 2>&1 \
             || dnf -y install python3 python3-devel rsync >/dev/null 2>&1; then
            echo "[+] Installed Python ${s} via module."
            return 0
          fi
        fi
      done
    fi

    # 3) Fallback: generic python3 (only if it's 3.10+)
    echo "[!] Versioned Python not found via RPMs/modules; trying generic python3."
    if dnf -y install python3 python3-devel rsync >/dev/null 2>&1; then
      if py_ok python3; then
        echo "[+] Generic python3 is suitable (3.10+)."
        return 0
      else
        echo "[-] Generic python3 is too old (< 3.10). Removing..."
        dnf -y remove python3 python3-devel >/dev/null 2>&1 || true
        return 1
      fi
    fi
    return 1

  elif need_cmd yum; then
    echo "[*] Detected yum-based system."
    if yum -y install python3 python3-devel rsync; then
      if py_ok python3; then
        echo "[+] Installed suitable Python via yum."
        return 0
      else
        echo "[-] Installed python3 is too old (< 3.10). Removing..."
        yum -y remove python3 python3-devel >/dev/null 2>&1 || true
        return 1
      fi
    fi
    return 1
  fi

  return 1
}

install_python() {
  echo "[-] Suitable Python (3.10+) not found. Installing best available..."
  if need_cmd apt; then
    export DEBIAN_FRONTEND=noninteractive
    apt update -y
    
    # Try to install specific versions first
    local installed=false
    for version in python3.12 python3.11 python3.10; do
      if apt install -y "${version}" "${version}-venv" "${version}-dev" rsync 2>/dev/null; then
        echo "[+] Installed ${version} via apt."
        installed=true
        break
      fi
    done
    
    if [[ "$installed" == "false" ]]; then
      # Fallback to generic python3
      if apt install -y python3 python3-venv python3-dev rsync; then
        if py_ok python3; then
          echo "[+] Generic python3 is suitable (3.10+)."
        else
          echo "[-] Generic python3 is too old (< 3.10)."
          return 1
        fi
      else
        return 1
      fi
    fi
    
  elif detect_like_rhel || need_cmd dnf || need_cmd yum; then
    install_python_rpm || {
      echo "[-] Could not install suitable Python (3.10+) via RPM paths."
      return 1
    }
  else
    echo "[-] Unsupported distro (no apt/dnf/yum). Install Python ≥3.10 manually and re-run."
    return 1
  fi
}

# Prefer highest version; removed 3.9 support
pick_python_bin() {
  local bins=(python3.12 python3.11 python3.10 python3)
  for b in "${bins[@]}"; do
    if py_ok "$b"; then
      echo "$b"; return 0
    fi
  done
  echo ""; return 1
}

# ---- Begin Install -----------------------------------------------------------
require_root

# Ensure Python present / install if needed
if ! py_ok python3 && ! py_ok python3.12 && ! py_ok python3.11 && ! py_ok python3.10; then
  install_python || { echo "[-] Failed to install Python 3.10+. Aborting."; exit 1; }
fi

PY_BIN="$(pick_python_bin || true)"
if [[ -z "${PY_BIN}" ]]; then
  echo "[-] Python 3.10+ not available after install."
  exit 1
fi
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
echo "[*] Creating virtual environment with ${PY_BIN} ..."
"${PY_BIN}" -m venv "${VENV_DIR}"

# shellcheck disable=SC1090
source "${VENV_DIR}/bin/activate"

# Ensure pip exists (minimal installs)
python -m ensurepip --upgrade >/dev/null 2>&1 || true

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