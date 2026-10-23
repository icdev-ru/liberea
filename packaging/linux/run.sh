#!/usr/bin/env bash
# Либерея — запуск сервера (после install.sh)
cd "$(dirname "$0")/../.." && exec .venv/bin/python manage.py serve
