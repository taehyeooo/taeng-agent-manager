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
    # (label, guard.py text, mutated text, BuildAllowlistTest test that must fail) — one allow condition removed at a time
    mutations = [
        ("heredoc body", "out += _check_script(body, head, cwd, root, python=python)",
         "out += []", "test_review_heredoc_interpreter_variants"),
        ("exact terminator", r"(?P=tag)(?:\n|$)", r"(?P=tag)[ \t]*(?:\n|$)", "test_review_heredoc_exact_terminator"),
        ("heredoc stdin shape", 'return word in ("python", "py", "node") and args in ([], ["-"])',
         'return word in ("python", "py", "node")', "test_reverify_a_heredoc_head_is_split_like_any_line"),
        ("baseline", "        base = _baseline(top)\n        if not base or _git(top, \"ls-files\"",
         "        base = _git(top, \"rev-parse\", \"HEAD\")\n        if not base or _git(top, \"ls-files\"",
         "test_review_committed_script_and_index_flags"),
        ("baseline reflog", 'return base if sha == base and TRUSTED_REFLOG.match(subject) else ""',
         "return base", "test_reverify_b_forged_baseline_is_not_trusted"),
        ("baseline origin url", 'if not base or not re.match(r"^(?:https|ssh|git)://|^[\\w.-]+@[\\w.-]+:", url):',
         "if not base:", "test_reverify_b_forged_baseline_is_not_trusted"),
        ("replace objects", 'NO_REPLACE = "--no-replace-objects"', 'NO_REPLACE = "--no-pager"',
         "test_reverify_b_forged_baseline_is_not_trusted"),
        ("ref commands", "if _rewrites_refs(sub, sargs, args[:len(args) - len(rest)]):", "if False:",
         "test_reverify_b_baseline_ref_commands_need_a_person"),
        (".git writes", 'elif ".git" in [part.lower() for part in p.parts]:', "elif False:",
         "test_reverify_b_baseline_ref_commands_need_a_person"),
        ("index flags", ' or _git(top, "ls-files", "-v", "--", rel) != "H " + rel',
         "", "test_review_committed_script_and_index_flags"),
        ("build inputs", 'if not _clean_build_inputs(cwd, "gradle") or not _clean_script(toks[0], cwd, None):', "if False:", "test_review_changed_build_inputs"),
        ("ignored build inputs", '("ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z"),', "",
         "test_reverify_f_trusted_npm_script_env_and_ignored_inputs"),
        ("environment", 'out.append(_finding("opaque", seg, reason="환경 변수 접두가 실행 동작을 바꿀 수 있음"))',
         "pass", "test_review_environment_prefixes"),
        ("same-line environment", "if not trusted and _changes_env(words):", "if False:",
         "test_reverify_c_environment_changed_on_the_same_line"),
        ("cd after keywords", "words = _lead(tokens(seg))", "words = tokens(seg)", "test_reverify_d_cd_variants"),
        ("package runners", "if _package_runner(toks) and not _build_allowed(toks, cwd):", "if False:",
         "test_reverify_e_package_runners_and_npm_options"),
        ("npm global options", "i += 2 if args[i] in NPM_OPTS_WITH_ARG else 1", "break",
         "test_reverify_e_package_runners_and_npm_options"),
        ("npm name", 'if re.search(r"deploy|release|publish|push", name, re.I):',
         "if False:", "test_review_npm_deploy_scripts"),
        ("npm body", "out.extend(classify_command(body, cwd, root, depth + 1, trusted=bool(allowed)))",
         "out.extend([])", "test_review_npm_deploy_scripts"),
        ("unittest paths", "return _unittest_args(args[2:], cwd)", "return True",
         "test_reverify_g_h_unittest_paths_interpreter_names_and_shadowing"),
        ("script siblings", ' and _clean_siblings(args[0], cwd))', ")",
         "test_reverify_g_h_unittest_paths_interpreter_names_and_shadowing"),
        ("interpreter names", r'(?:python|pypy)[\w.-]*', r'(?:python|pypy)[\d.]*',
         "test_reverify_g_h_unittest_paths_interpreter_names_and_shadowing"),
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
