#!/bin/sh
set -eu

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SESSION="hn-feishu-digest"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/scheduler.log"
UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/hn-feishu-uv-cache}"
export UV_CACHE_DIR

usage() {
  echo "Usage: ./manage.sh {start|status|logs|attach|run-now|stop}"
}

case "${1:-}" in
  start)
    mkdir -p "$LOG_DIR"
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      echo "Scheduler is already running in tmux session: $SESSION"
      exit 0
    fi
    tmux new-session -d -s "$SESSION" -c "$PROJECT_DIR" \
      "exec env UV_CACHE_DIR='$UV_CACHE_DIR' caffeinate -is uv run python local_scheduler.py >> '$LOG_FILE' 2>&1"
    sleep 1
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      echo "Scheduler started: $SESSION"
      echo "Log: $LOG_FILE"
    else
      echo "Scheduler failed to stay running. Check: $LOG_FILE" >&2
      exit 1
    fi
    ;;
  status)
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      echo "Scheduler is running: $SESSION"
    else
      echo "Scheduler is stopped"
      exit 1
    fi
    ;;
  logs)
    mkdir -p "$LOG_DIR"
    touch "$LOG_FILE"
    tail -n 100 -f "$LOG_FILE"
    ;;
  attach)
    exec tmux attach-session -t "$SESSION"
    ;;
  run-now)
    cd "$PROJECT_DIR"
    exec uv run python local_scheduler.py --once
    ;;
  stop)
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      tmux kill-session -t "$SESSION"
      echo "Scheduler stopped: $SESSION"
    else
      echo "Scheduler is already stopped"
    fi
    ;;
  *)
    usage
    exit 2
    ;;
esac
