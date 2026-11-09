import contextlib
import io
import json
import subprocess
import unittest
from unittest.mock import patch

from am import cli, doctor


class DoctorTest(unittest.TestCase):
    def test_resolved_paths_used_for_every_command(self):
        calls = []

        def run(args, **kwargs):
            calls.append(args)
            self.assertTrue(args[0].startswith('C:/x/'), args)
            output = 'runtimeReachable: true' if args[1:] == ['status'] else 'version'
            return subprocess.CompletedProcess(args, 0, output, '')

        doctor.run_checks(which=lambda name: f'C:/x/{name}.CMD', run=run)
        self.assertEqual(calls, [
            [f'C:/x/{name}.CMD', '--version']
            for name in ('git', 'gh', 'orca', 'claude', 'codex')
        ] + [['C:/x/gh.CMD', 'auth', 'status'], ['C:/x/orca.CMD', 'status']])

    def test_missing_commands_are_not_executed(self):
        def run(args, **kwargs):
            self.fail(f'Missing command executed: {args}')

        results = doctor.run_checks(which=lambda name: None, run=run)
        self.assertTrue(all(not row['ok'] for row in results if row['name'] != 'python'))

    def checks(self, missing=(), failed=(), error=None):
        def run(args, **kwargs):
            self.assertEqual(kwargs['timeout'], 15)
            if error:
                raise error(args, 15) if error is subprocess.TimeoutExpired else error()
            key = ' '.join(args)
            output = 'runtimeReachable: true' if args == ['orca', 'status'] else args[0] + ' 1.0\nsecond'
            return subprocess.CompletedProcess(args, int(key in failed), output, '')
        return doctor.run_checks(which=lambda name: None if name in missing else name, run=run)

    def invoke(self, results, *args):
        out = io.StringIO()
        code = 0
        with patch.object(doctor, 'run_checks', return_value=results), contextlib.redirect_stdout(out):
            try:
                cli.main(['doctor', *args])
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue()

    def test_all_present(self):
        results = self.checks()
        self.assertTrue(all(row['ok'] for row in results))
        self.assertEqual(self.invoke(results)[0], 0)
        self.assertEqual(json.loads(self.invoke(results, '--json')[1]), results)
        self.assertEqual(results[0]['detail'], 'git 1.0')

    def test_git_missing(self):
        self.assertEqual(self.invoke(self.checks(missing=['git']))[0], 1)

    def test_codex_optional(self):
        code, out = self.invoke(self.checks(missing=['codex']))
        self.assertEqual(code, 0)
        self.assertIn('✗ codex', out)

    def test_both_agents_missing(self):
        results = self.checks(missing=['claude', 'codex'])
        self.assertIn(dict(name='agent', ok=False, detail='claude or codex required', required=True), results)
        self.assertEqual(self.invoke(results)[0], 1)

    def test_auth_failure(self):
        self.assertEqual(self.invoke(self.checks(failed=['gh auth status']))[0], 1)

    def test_subprocess_exceptions(self):
        for error in (subprocess.TimeoutExpired, FileNotFoundError):
            with self.subTest(error=error):
                results = self.checks(error=error)
                self.assertFalse(next(row for row in results if row['name'] == 'git')['ok'])
                self.assertEqual(results[0]['detail'], '')
                self.assertEqual(self.invoke(results)[0], 1)

    def test_runtime_unreachable(self):
        self.assertFalse(next(row for row in self.checks(failed=['orca status']) if row['name'] == 'orca runtime')['ok'])

    def test_old_python(self):
        with patch.object(doctor.sys, 'version_info', (3, 9, 0)):
            self.assertEqual(self.invoke(self.checks())[0], 1)
