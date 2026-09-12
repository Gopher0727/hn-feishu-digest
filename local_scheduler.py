"""Run the HN digest locally at fixed Asia/Shanghai times."""

import argparse
from datetime import datetime, timedelta
import os
from pathlib import Path
import subprocess
import sys
import time
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent
TZ = ZoneInfo("Asia/Shanghai")
RUN_TIMES = ((9, 27), (10, 27), (11, 27), (12, 27))
REQUIRED_KEYS = (
    "FEISHU_WEBHOOK",
    "FEISHU_SIGN_SECRET",
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
)


def next_slot(now):
    """Return the next strictly-future scheduled time."""
    for hour, minute in RUN_TIMES:
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate > now:
            return candidate
    tomorrow = now + timedelta(days=1)
    hour, minute = RUN_TIMES[0]
    return tomorrow.replace(hour=hour, minute=minute, second=0, microsecond=0)


def load_env_file(path):
    values = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise RuntimeError(f"Invalid environment line in {path.name}")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def require_configuration(env):
    missing = [key for key in REQUIRED_KEYS if not env.get(key)]
    if missing:
        raise RuntimeError("Missing local configuration: " + ", ".join(missing))


def configured_environment():
    env = os.environ.copy()
    for key, value in load_env_file(ROOT / ".env.local").items():
        env.setdefault(key, value)
    require_configuration(env)
    return env


def run_digest(env):
    started = datetime.now(TZ)
    print(f"[{started.isoformat(timespec='seconds')}] Starting digest", flush=True)
    result = subprocess.run(
        [sys.executable, str(ROOT / "digest.py"), "run", "--send"],
        cwd=ROOT,
        env=env,
        check=False,
    )
    finished = datetime.now(TZ)
    print(
        f"[{finished.isoformat(timespec='seconds')}] Digest exited {result.returncode}",
        flush=True,
    )
    return result.returncode


def serve(env):
    while True:
        target = next_slot(datetime.now(TZ))
        print(f"Next run: {target.isoformat(timespec='minutes')}", flush=True)
        delay = max(0, (target - datetime.now(TZ)).total_seconds())
        time.sleep(delay)
        run_digest(env)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run immediately once")
    args = parser.parse_args()
    env = configured_environment()
    if args.once:
        raise SystemExit(run_digest(env))
    serve(env)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
