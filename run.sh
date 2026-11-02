#!/bin/zsh
# Запуск JobHunt.  ./run.sh — ваши данные в data/;  ./run.sh --demo — демо с вымышленным кандидатом.
cd "$(dirname "$0")"
PORT=${PORT:-8765}
if [[ "$1" == "--demo" ]]; then
  export JOBHUNT_DATA="$PWD/demo-data"
  .venv/bin/python -m app.demo >/dev/null
fi
echo "JobHunt → http://localhost:$PORT"
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
