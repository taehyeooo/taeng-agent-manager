"""Read-only checks for the manager's local tools."""
import json
import shutil
import subprocess
import sys


def run_checks(which=shutil.which, run=subprocess.run):
    results = []

    def execute(args):
        try:
            result = run(args, capture_output=True, text=True, encoding='utf-8',
                         errors='replace', timeout=15)
            return result.returncode == 0, result.stdout or result.stderr or ''
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False, ''

    present = {}
    for name in ('git', 'gh', 'orca', 'claude', 'codex'):
        present[name] = bool(which(name))
        ok, output = execute([name, '--version']) if present[name] else (False, '')
        results.append(dict(name=name, ok=present[name] and ok,
                            detail=output.splitlines()[0] if ok and output else '',
                            required=name in ('git', 'gh')))

    results.append(dict(name='python', ok=sys.version_info >= (3, 10),
                        detail='.'.join(map(str, sys.version_info[:3])), required=True))
    if not present['claude'] and not present['codex']:
        results.append(dict(name='agent', ok=False, detail='claude or codex required', required=True))

    ok, _ = execute(['gh', 'auth', 'status'])
    results.append(dict(name='gh auth', ok=ok, detail='logged in' if ok else '', required=True))
    ok, output = execute(['orca', 'status'])
    reachable = ok and 'runtimeReachable: true' in output
    results.append(dict(name='orca runtime', ok=reachable,
                        detail='runtimeReachable: true' if reachable else '', required=False))
    return results


def cmd_doctor(args):
    results = run_checks()
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for row in results:
            print(f"{'✓' if row['ok'] else '✗'} {row['name']:<14} {row['detail']}")
    if any(row['required'] and not row['ok'] for row in results):
        sys.exit(1)
