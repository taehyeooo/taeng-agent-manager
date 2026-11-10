"""Run focused guard mutations in memory; never change the working tree."""
import importlib.util
import io
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from am import guard  # noqa: E402


def main():
    spec = importlib.util.spec_from_file_location("guard_cases", ROOT / "tests/test_guard.py")
    cases = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cases)
    source = (ROOT / "am/guard.py").read_text(encoding="utf-8")
    mutations = [
        ("heredoc body", "out.extend(_check_script(body, header, cwd, root, python=_word(match.group(\"lang\")) != \"node\"))",
         "out.extend([])", "test_review_heredoc_interpreter_variants"),
        ("exact terminator", r"(?P=tag)(?:\n|$)", r"(?P=tag)[ \t]*(?:\n|$)", "test_review_heredoc_exact_terminator"),
        ("baseline", 'base = _git(top, "rev-parse", "--verify", "refs/remotes/origin/main^{commit}")',
         'base = _git(top, "rev-parse", "HEAD")', "test_review_committed_script_and_index_flags"),
        ("index flags", ' or _git(top, "ls-files", "-v", "--", rel) != "H " + rel',
         "", "test_review_committed_script_and_index_flags"),
        ("build inputs", 'if not _clean_build_inputs(cwd, "gradle") or not _clean_script(toks[0], cwd, None):', "if False:", "test_review_changed_build_inputs"),
        ("environment", 'out.append(_finding("opaque", seg, reason="환경 변수 접두가 실행 동작을 바꿀 수 있음"))',
         "pass", "test_review_environment_prefixes"),
        ("npm name", 'if re.search(r"deploy|release|publish|push", name, re.I):',
         "if False:", "test_review_npm_deploy_scripts"),
        ("npm body", "out.extend(classify_command(body, cwd, root, depth + 1))",
         "out.extend([])", "test_review_npm_deploy_scripts"),
    ]
    killed = 0
    for label, old, new, test in mutations:
        if source.count(old) != 1:
            raise RuntimeError("Mutation anchor changed: " + label)
        try:
            exec(compile(source.replace(old, new), str(ROOT / "am/guard.py"), "exec"), guard.__dict__)
            result = unittest.TextTestRunner(stream=io.StringIO()).run(
                unittest.TestSuite([cases.BuildAllowlistTest(test)]))
            caught = bool(result.failures) and not result.errors
            killed += caught
            print(label + (": killed" if caught else ": SURVIVED/ERROR"), flush=True)
        finally:
            exec(compile(source, str(ROOT / "am/guard.py"), "exec"), guard.__dict__)
    print(f"{killed}/{len(mutations)} killed")
    return 0 if killed == len(mutations) else 1


if __name__ == "__main__":
    sys.exit(main())
