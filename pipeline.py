"""Local or split HN pipeline. GitHub transports source data, never model secrets."""
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

import digest

DATA_DIR = digest.ROOT / 'data'


def data_dir():
    return Path(os.environ.get('DATA_DIR', str(DATA_DIR))).expanduser().resolve()


@contextmanager
def run_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        stream.seek(0)
        if not stream.read(1):
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('Another digest task is running on this computer') from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def make_package(day, items, backlog):
    payload = dict(schema_version=1, day=day, items=items, backlog=backlog)
    return dict(payload, fingerprint=fingerprint(payload))


def validate_package(package, day):
    if not isinstance(package, dict) or package.get('schema_version') != 1 or package.get('day') != day:
        raise ValueError('Invalid source package schema or date')
    payload = {key: value for key, value in package.items() if key != 'fingerprint'}
    if package.get('fingerprint') != fingerprint(payload):
        raise ValueError('Source package fingerprint mismatch')
    items, backlog = package.get('items'), package.get('backlog')
    if not isinstance(items, list) or not 1 <= len(items) <= 15 or not isinstance(backlog, list):
        raise ValueError('Invalid source package item count')
    seen = set()
    for row in items + backlog:
        if not isinstance(row, dict) or type(row.get('id')) is not int or row['id'] in seen:
            raise ValueError('Invalid or duplicate source ID')
        seen.add(row['id'])
        if any(not isinstance(row.get(key), str) or not row[key].strip() for key in ('title', 'url', 'excerpt', 'basis')):
            raise ValueError('Source package missing article data')
        if not digest.accessible_stories([row]):
            raise ValueError('Source package contains insufficient article text')
    if [row.get('rank') for row in items] != list(range(1, len(items)+1)):
        raise ValueError('Invalid source ordering')
    return package


def github(path='', body=None):
    repo = os.environ.get('DATA_REPO', '')
    token = os.environ.get('DATA_TOKEN', '')
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', repo) or not token:
        raise RuntimeError('Configure DATA_REPO and DATA_TOKEN')
    url = f'https://api.github.com/repos/{repo}' + path
    headers = {'Authorization':'Bearer ' + token, 'Accept':'application/vnd.github+json',
               'User-Agent':'HN-Feishu-Digest', 'Content-Type':'application/json'}
    request = Request(url, data=json.dumps(body).encode() if body is not None else None,
                      headers=headers, method='PUT' if body is not None else 'GET')
    for attempt in range(3):
        try:
            with build_opener(digest.NoRedirect).open(request, timeout=90) as response:
                raw = response.read(4_000_001)
                if len(raw) > 4_000_000:
                    raise RuntimeError('GitHub response exceeds limit')
                return json.loads(raw)
        except HTTPError as exc:
            if exc.code == 404 and body is None:
                return None
            if exc.code in (409, 422) and body is not None:
                return None  # Caller verifies whether another producer won.
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise RuntimeError(f'GitHub HTTP {exc.code}; check repository access') from None
        except (URLError, TimeoutError, OSError):
            if attempt == 2:
                raise RuntimeError('GitHub network failure') from None
        time.sleep(2 ** attempt)


def require_private_repo():
    info = github()
    if not info or info.get('private') is not True:
        raise RuntimeError('DATA_REPO must be an accessible private repository')


def download_package(day):
    entry = github(f'/contents/daily/{day}.json')
    if entry is None:
        return None
    if entry.get('encoding') != 'base64' or not entry.get('content'):
        raise RuntimeError('GitHub data file unavailable or too large')
    return validate_package(json.loads(base64.b64decode(entry['content'])), day)


def collect(day):
    digest.RUN_STAGE = 'github_source_lookup'
    require_private_repo()
    existing = download_package(day)
    if existing:
        digest.write_json(data_dir() / f'{day}.json', existing)
        print('Today source package already published; collection skipped.')
        return
    local = data_dir() / f'{day}.json'
    package = digest.read_json(local)
    if package is None:
        # Shared published backlog allows Actions to take over from the laptop.
        entries = github('/contents/daily') or []
        dates = sorted(entry['name'][:-5] for entry in entries
                       if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', entry.get('name', '')) and entry['name'][:-5] < day)
        previous = download_package(dates[-1]) if dates else None
        backlog = previous['backlog'] if previous else []
        excluded = {row['id'] for row in previous['items']} if previous else set()
        digest.RUN_STAGE = 'fetch_hn_and_articles'
        fresh = digest.fetch_stories(target=max(0, 15-len(backlog)),
                                     exclude_ids=excluded | {row['id'] for row in backlog}) if len(backlog) < 15 else []
        items, remaining = digest.select_daily_stories(backlog, fresh)
        if not items:
            raise RuntimeError('No accessible articles; source package not published')
        package = make_package(day, items, remaining)
        validate_package(package, day)
        digest.write_json(local, package)
    validate_package(package, day)
    content = json.dumps(package, ensure_ascii=False).encode()
    if len(content) > 950_000:
        raise RuntimeError('Source package too large for GitHub Contents transport')
    digest.RUN_STAGE = 'github_source_publish'
    github(f'/contents/daily/{day}.json', dict(message=f'Collect HN sources {day}', content=base64.b64encode(content).decode()))
    published = download_package(day)
    if not published:
        raise RuntimeError('Source package publication could not be verified')
    digest.write_json(local, published)
    print('Source package published and verified.')


def consume(day):
    digest.RUN_STAGE = 'import_source_package'
    work = digest.STATE / 'work-cache' / day
    report = digest.STATE / 'reports' / day
    sent = digest.read_json(report / 'sent.json', {})
    if sent.get('sent', 0) >= 1:
        print('Today digest already sent; skipped.')
        return
    package_path = data_dir() / f'{day}.json'
    package = digest.read_json(package_path)
    if package is None:
        require_private_repo()
        package = download_package(day)
        if package is None:
            print('Today source package is not available; waiting for next scheduled run.')
            return
        digest.write_json(package_path, package)
    validate_package(package, day)
    pinned = digest.read_json(report / 'input.json')
    if pinned and pinned.get('fingerprint') != package['fingerprint']:
        raise RuntimeError('Source package changed after summary started')
    old_sources = digest.read_json(work / 'sources.json')
    if old_sources is not None and fingerprint(old_sources) != fingerprint(package['items']):
        raise RuntimeError('Existing local sources differ; use a separate STATE_DIR for company mode')
    digest.write_json(report / 'input.json', {'fingerprint': package['fingerprint']})
    digest.write_json(work / 'sources.json', package['items'])
    digest.main(['run', '--offline', '--send'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('role', choices=['local', 'collector', 'consumer'])
    args = parser.parse_args()
    day = datetime.now(digest.ZoneInfo('Asia/Shanghai')).date().isoformat()
    try:
        with run_lock(digest.STATE / 'pipeline.lock'):
            if args.role == 'local':
                digest.main(['run', '--send'])
            elif args.role == 'collector':
                collect(day)
            else:
                consume(day)
    except Exception as exc:
        digest.write_json(digest.STATE / 'diagnostics' / f'{day}-pipeline.json',
                          {'role':args.role, 'stage':digest.RUN_STAGE, 'error_type':type(exc).__name__,
                           'error':str(exc) if isinstance(exc, (RuntimeError, ValueError)) else 'Details omitted',
                           'time':datetime.now().isoformat()})
        print(f'ERROR: {type(exc).__name__}; see local diagnostics')
        raise SystemExit(1)


if __name__ == '__main__':
    main()
