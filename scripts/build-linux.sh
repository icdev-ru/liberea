#!/usr/bin/env bash
# Либерея — архив для Linux (исходники + install.sh). Использование: scripts/build-linux.sh 1.0.0
set -euo pipefail
cd "$(dirname "$0")/.."
VER="${1:-dev}"
NAME="Liberea-$VER-linux"
rm -rf dist/"$NAME" && mkdir -p dist/"$NAME"
cp -r app fonts packaging manage.py config.example.toml requirements.txt LICENSE README.md README.en.md CHANGELOG.md \
      Dockerfile docker-compose.yml dist/"$NAME"/
find dist/"$NAME" -name __pycache__ -prune -exec rm -rf {} +
tar -C dist -czf dist/"$NAME".tar.gz "$NAME"
echo "Готово: dist/$NAME.tar.gz"
