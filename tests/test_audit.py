import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from retry_claim_audit.audit import audit, extract_claims, extract_evidence, PHRASES
from retry_claim_audit.__main__ import main


class AuditTests(unittest.TestCase):
    def tree(self, docs='retries on failure', source='fetch()', extra=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for name, text in dict({'README.md': docs, 'main.py': source}, **(extra or {})).items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        return root

    def test_fences(self):
        self.assertEqual(extract_claims('```md\nwith retries\n```\n~~~\nsingle attempt\n~~~', 'README.md'), [])

    def test_inline(self):
        self.assertEqual(len(extract_claims('Use `with retries`.', 'x')), 1)

    def test_multiline_claim(self):
        self.assertEqual(extract_claims('Hello.\nWith retries.', 'x')[0]['line'], 2)

    def test_sentence_negation(self):
        self.assertEqual(len(extract_claims('Never with retries. Single attempt.', 'x')), 1)

    def test_no_source_both(self):
        root = self.tree('with retries. Single attempt.')
        (root/'main.py').unlink()
        result = audit(root)
        self.assertEqual(len(result['findings']), 2)
        self.assertTrue(all(f['evidence'] == [{'kind': 'no-source'}] for f in result['findings']))

    def test_both_independent(self):
        result = audit(self.tree('with retries. Single attempt.', extra={'tests/test_x.py': 'import backoff'}))
        self.assertEqual(len(result['findings']), 2)
        self.assertTrue(all(f['severity'] == 'MEDIUM' for f in result['findings']))

    def test_ignore(self):
        root = self.tree(source='import tenacity')
        self.assertEqual(audit(root, ['main.py'])['findings'][0]['evidence'], [{'kind': 'no-source'}])

    def test_ignore_evidence(self):
        root = self.tree(extra={'other.py': 'import backoff'})
        self.assertEqual(audit(root, ['other.py'])['findings'][0]['rule'], 'documented-retry-claim-false')

    def test_no_claim(self):
        self.assertEqual(audit(self.tree('Hello', 'import tenacity'))['findings'], [])

    def test_docs_claim(self):
        self.assertEqual(len(audit(self.tree('Hello', extra={'docs/nested/a.md':'with retries'}))['findings']), 1)

    def test_other_docs_ignored(self):
        self.assertEqual(audit(self.tree('Hello', extra={'other.md':'with retries'}))['findings'], [])

    def test_symlink(self):
        root = self.tree()
        (root/'linked.py').symlink_to(root/'main.py')
        self.assertEqual(len(audit(root)['findings']), 1)

    def test_external_symlink(self):
        root = self.tree()
        (root/'outside.py').symlink_to('/does/not/exist')
        self.assertEqual(len(audit(root)['findings']), 1)

    def test_env_never_opened(self):
        root = self.tree(extra={'.env': 'SECRET_MARKER'})
        original = Path.open
        def guarded(path, *args, **kwargs):
            self.assertNotEqual(path.name, '.env')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', guarded):
            report = audit(root)
        self.assertNotIn('SECRET_MARKER', json.dumps(report))

    def test_invalid_source(self):
        self.assertEqual(len(audit(self.tree(source='def !'))['errors']), 1)

    def test_dedup(self):
        self.assertEqual(len(extract_evidence('import backoff, tenacity', 'x')), 1)

    def test_all_evidence(self):
        report = audit(self.tree('single attempt', 'import backoff\nretry()\nwait_fixed(1)'))
        self.assertEqual(len(report['findings'][0]['evidence']), 3)

    def test_verified_retry(self):
        self.assertEqual(audit(self.tree(source='retry()'))['verified_claims'][0]['family'], 'retry')

    def test_verified_single(self):
        self.assertEqual(audit(self.tree('single attempt'))['verified_claims'][0]['family'], 'single-attempt')

    def test_json_cli(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main([str(self.tree()), '--json']), 2)
        self.assertIn('verified_claims', json.loads(output.getvalue()))

    def test_threshold(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main([str(self.tree(extra={'tests/test_x.py':'import backoff'})), '--fail-on', 'HIGH']), 0)
        self.assertIn('MEDIUM', output.getvalue())

    def test_version(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as caught:
            main(['--version'])
        self.assertEqual(caught.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), '0.1.0')

    def test_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            main(['--unknown'])
        self.assertEqual(caught.exception.code, 1)

    def test_missing_path(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            main(['/nonexistent/retry-audit-tree'])
        self.assertEqual(caught.exception.code, 1)


def claim_test(phrase):
    def test(self):
        self.assertEqual(len(extract_claims(phrase.upper(), 'x')), 1)
    return test

for index, phrase in enumerate(sum((list(v) for v in PHRASES.values()), [])):
    setattr(AuditTests, 'test_exact_phrase_%02d' % index, claim_test(phrase))


def no_claim_test(text):
    def test(self):
        self.assertEqual(extract_claims(text, 'x'), [])
    return test

for index, text in enumerate(['retry', 'retries are configurable', 'retry button', 'no retries', 'does not retry', 'automatic-retries', 'retries on transient failure'] + [word+' with retries.' for word in ['no', 'not', 'never', 'without', "doesn't", "don't", "isn't"]]):
    setattr(AuditTests, 'test_nonclaim_%02d' % index, no_claim_test(text))


def evidence_test(source, expected):
    def test(self):
        self.assertEqual(bool(extract_evidence(source, 'x.py')), expected)
    return test

cases = [
 ('import tenacity as t', True), ('from backoff import on_exception as oe', True),
 ('import retrying', True), ('import requests', False), ('import urllib3', False),
 ('import httpx', False), ('time.sleep(3)', False),
 ('stop_after_attempt(1)', False), ('stop_after_attempt(3)', True),
 ('stop_after_attempt(limit)', False), ('stop_after_attempt(True)', False),
 ('HTTPAdapter(max_retries=0)', False), ('HTTPAdapter(max_retries=3)', True),
 ('s.mount(max_retries=2)', True), ('s.mount(max_retries=limit)', False),
 ('Retry(total=3)', True), ('Retry(max_retries=0)', False),
 ('try:\n fetch()\nexcept Exception:\n raise', False),
 ('for item in items:\n try:\n  fetch()\n except Exception:\n  pass', False),
 ('for item in items:\n try:\n  fetch()\n except Exception:\n  continue', True),
 ('for attempt in range(3):\n try:\n  fetch()\n except Exception:\n  pass', True),
 ('while retries:\n try:\n  fetch()\n except Exception:\n  pass', True),
 ('for i in range(max_attempts):\n try:\n  fetch()\n except Exception:\n  pass', True),
 ('@retry\ndef f():\n pass', True),
 ('async def f():\n async for attempt in items:\n  try:\n   await fetch()\n  except Exception:\n   pass', True),
 ('def unused():\n retry()', True),
]
cases += [(name+'()', True) for name in ['Retrying','wait_exponential','wait_fixed','wait_random','wait_random_exponential','retry_if_exception','retry_if_exception_type','retry_if_result','on_exception','on_predicate']]
for index, (source, expected) in enumerate(cases):
    setattr(AuditTests, 'test_evidence_%02d' % index, evidence_test(source, expected))


def excluded_test(name):
    def test(self):
        root = self.tree(extra={name:'import backoff'})
        report = audit(root)
        self.assertEqual(report['findings'][0]['severity'], 'MEDIUM' if 'docs/' not in name and 'examples/' not in name else 'HIGH')
    return test
for index, name in enumerate(['tests/work.py', 'fixtures/work.py', 'test_work.py', 'work_test.py', 'docs/work.py', 'examples/work.py']):
    setattr(AuditTests, 'test_exclusions_%02d' % index, excluded_test(name))

if __name__ == '__main__':
    unittest.main()
