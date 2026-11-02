#!/bin/zsh
# Установка: виртуальное окружение, зависимости и браузер для бота.
set -e
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
.venv/bin/python -m playwright install chromium
chmod +x run.sh
echo "Готово. Запуск: ./run.sh  (или ./run.sh --demo, чтобы посмотреть на примере)"
