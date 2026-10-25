"""Offline documentation and static retry evidence analysis."""
import ast
import fnmatch
import re
from pathlib import Path

PHRASES = {
    'retry': ('retries on failure', 'retries on error', 'retries transient errors',
              'retries failed requests', 'automatic retries', 'automatically retries',
              'retries automatically', 'with retries', 'retry logic'),
    'single-attempt': ('single attempt', 'one attempt', 'fails immediately'),
}
NEGATION = re.compile(r"\b(?:no|not|never|without|doesn't|don't|isn't)\b", re.I)
LIBRARIES = {'tenacity', 'backoff', 'retrying'}
CALLS = {'retry', 'Retrying', 'wait_exponential', 'wait_fixed', 'wait_random',
         'wait_random_exponential', 'retry_if_exception', 'retry_if_exception_type',
         'retry_if_result', 'on_exception', 'on_predicate'}


def extract_claims(content, path):
    """Return exact phrase claims, preserving source locations."""
    prose = []
    fence = None
    for line in content.splitlines():
        match = re.match(r'^\s{0,3}(`{3,}|~{3,})', line)
        if match:
            token = match.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            prose.append('')
        else:
            prose.append(line if fence is None else '')
    text = '\n'.join(prose)
    result = []
    for sentence in re.finditer(r'[^.!?]+(?:[.!?]|$)', text):
        if NEGATION.search(sentence.group()):
            continue
        for family, phrases in PHRASES.items():
            for phrase in phrases:
                for match in re.finditer(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', sentence.group(), re.I):
                    offset = sentence.start() + match.start()
                    result.append({'family': family, 'path': path,
                                   'line': text.count('\n', 0, offset) + 1,
                                   'text': match.group()})
    return result


def literal_above(node, minimum):
    return (isinstance(node, ast.Constant) and type(node.value) in (int, float)
            and node.value > minimum)


def extract_evidence(content, path):
    """Parse source without importing or executing it."""
    tree = ast.parse(content, filename=path)
    found = {}
    def add(node, kind):
        found[(node.lineno, kind)] = {'path': path, 'line': node.lineno, 'kind': kind}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) and getattr(decorator, 'id', getattr(decorator, 'attr', '')) in CALLS:
                    add(decorator, 'retry-call')
        elif isinstance(node, ast.Import):
            if any(alias.name.split('.')[0] in LIBRARIES for alias in node.names):
                add(node, 'retry-import')
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split('.')[0] in LIBRARIES:
                add(node, 'retry-import')
        elif isinstance(node, ast.Call):
            name = getattr(node.func, 'id', getattr(node.func, 'attr', ''))
            if name in CALLS:
                add(node, 'retry-call')
            elif name == 'stop_after_attempt':
                if node.args and literal_above(node.args[0], 1):
                    add(node, 'retry-call')
            elif name in {'Retry', 'HTTPAdapter', 'mount'}:
                if any(k.arg == 'max_retries' and literal_above(k.value, 0) for k in node.keywords):
                    add(node, 'retry-adapter')
                elif name == 'Retry' and not any(k.arg == 'max_retries' for k in node.keywords):
                    add(node, 'retry-call')
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            body_nodes = [child for statement in node.body for child in ast.walk(statement)]
            tries = [child for child in body_nodes if isinstance(child, ast.Try)]
            continues = any(isinstance(child, ast.Continue)
                            for tr in tries for handler in tr.handlers
                            for statement in handler.body for child in ast.walk(statement))
            expressions = ([node.target, node.iter] if isinstance(node, (ast.For, ast.AsyncFor))
                           else [node.test])
            named = any(any(word in child.id.lower() for word in ('attempt', 'retry', 'tries'))
                        for expr in expressions for child in ast.walk(expr) if isinstance(child, ast.Name))
            if tries and (continues or named):
                add(node, 'retry-loop')
    return sorted(found.values(), key=lambda e: (e['line'], e['kind']))


def audit(root, ignore=()):
    """Audit a tree, skipping symlinks and never reading environment files."""
    root = Path(root)
    if not root.is_dir():
        raise ValueError('audit path must be a directory')
    claims, production, testonly, errors = [], [], [], []
    source_count = 0
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        parts = relative.parts
        rel = relative.as_posix()
        if any((root.joinpath(*parts[:i])).is_symlink() for i in range(1, len(parts) + 1)):
            continue
        if not path.is_file() or any(fnmatch.fnmatchcase(rel, glob) for glob in ignore):
            continue
        is_doc = rel == 'README.md' or (parts[0] == 'docs' and path.suffix == '.md')
        is_test = any(p in {'tests', 'fixtures'} for p in parts[:-1]) or path.name.startswith('test_') or path.name.endswith('_test.py')
        if not is_doc and (path.suffix != '.py' or any(p in {'docs', 'examples'} for p in parts[:-1])):
            continue
        if not is_doc and not is_test:
            source_count += 1
        try:
            content = path.read_text(encoding='utf-8')
            if is_doc:
                claims.extend(extract_claims(content, rel))
            else:
                (testonly if is_test else production).extend(extract_evidence(content, rel))
        except (OSError, UnicodeError, SyntaxError) as exc:
            errors.append({'path': rel, 'error': str(exc)})
    findings, verified = [], []
    for family in PHRASES:
        selected = [c for c in claims if c['family'] == family]
        if not selected:
            continue
        severity = None
        evidence = []
        if source_count == 0:
            severity, evidence = 'HIGH', [{'kind': 'no-source'}]
        elif family == 'retry' and not production:
            severity, evidence = ('MEDIUM', testonly) if testonly else ('HIGH', [])
        elif family == 'single-attempt' and (production or testonly):
            severity, evidence = ('HIGH', production) if production else ('MEDIUM', testonly)
        if severity:
            rule = 'documented-' + family + '-claim-false'
            if severity == 'MEDIUM':
                rule += '-test-only'
            findings.append({'rule': rule, 'severity': severity, 'claims': selected, 'evidence': evidence})
        else:
            verified.append({'family': family, 'claims': selected, 'evidence': production if family == 'retry' else []})
    return {'findings': findings, 'verified_claims': verified, 'errors': errors}
