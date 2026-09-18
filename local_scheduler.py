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


def next_slot(now, run_times=None):
    """Return the next strictly-future scheduled time."""
    slots = run_times or RUN_TIMES
    for hour, minute in slots:
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate > now:
            return candidate
    tomorrow = now + timedelta(days=1)
    hour, minute = slots[0]
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


def task_role(env):
    mode = env.get('PUSH_MODE', 'local')
    if mode == 'local':
        return 'local'
    if mode != 'company' or env.get('TASK_ROLE') not in ('collector', 'consumer'):
        raise RuntimeError('Use PUSH_MODE=local or company with TASK_ROLE=collector/consumer')
    return env['TASK_ROLE']


def require_configuration(env):
    role = task_role(env)
    keys = [] if role == 'collector' else list(REQUIRED_KEYS)
    if env.get('LLM_PROVIDER') == 'llamacpp' and 'LLM_API_KEY' in keys:
        keys.remove('LLM_API_KEY')
    if role != 'local':
        keys.extend(('DATA_REPO', 'DATA_TOKEN'))
    missing = [key for key in keys if not env.get(key)]
    if missing:
        raise RuntimeError('Missing local configuration: ' + ', '.join(missing))


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
        [sys.executable, str(ROOT / "pipeline.py"), task_role(env)],
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
        slots = ((8, 30), (9, 30), (10, 30), (11, 30)) if task_role(env) == 'collector' else RUN_TIMES
        target = next_slot(datetime.now(TZ), slots)
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
