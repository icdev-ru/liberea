#!/usr/bin/env bash
# Либерея — установка на Linux (Debian/Ubuntu/Fedora/Arch и т. п.)
#   ./packaging/linux/install.sh              — окружение Python + config.toml
#   ./packaging/linux/install.sh --service    — то же + служба systemd (автозапуск)
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT="$(pwd)"

PY="${PYTHON:-python3}"
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  echo "Нужен Python 3.11 или новее (сейчас: $("$PY" --version 2>&1 || echo нет))." >&2
  echo "Ubuntu/Debian: sudo apt install python3 python3-venv" >&2
  exit 1
fi

echo "[1/3] Виртуальное окружение…"
"$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip >/dev/null
echo "[2/3] Пакеты…"
.venv/bin/python -m pip install -r requirements.txt

[ -f config.toml ] || cp config.example.toml config.toml
echo "[3/3] Готово. Запуск:  ./packaging/linux/run.sh"

if [ "${1:-}" = "--service" ]; then
  USER_NAME="${SUDO_USER:-$(id -un)}"
  UNIT=/etc/systemd/system/liberea.service
  sed -e "s|@ROOT@|$ROOT|g" -e "s|@USER@|$USER_NAME|g" packaging/linux/liberea.service | sudo tee "$UNIT" >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl enable --now liberea
  echo "Служба установлена и запущена: sudo systemctl status liberea"
fi
PORT=$(.venv/bin/python -c 'from app.config import CONFIG; print(CONFIG.port)' 2>/dev/null || echo 5555)
echo "Откройте http://localhost:$PORT/setup — мастер первого запуска."
