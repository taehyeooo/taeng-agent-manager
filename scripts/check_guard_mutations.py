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
        ("baseline", "    base = _baseline(top)\n    rels = sorted(set(rels))",
         "    base = _git(top, \"rev-parse\", \"HEAD\")\n    rels = sorted(set(rels))",
         "test_review_committed_script_and_index_flags"),
        ("baseline reflog", 'return base if sha == base and TRUSTED_REFLOG.match(subject) else ""',
         "return base", "test_reverify_b_forged_baseline_is_not_trusted"),
        ("baseline origin url", 'if not base or not re.match(r"^(?:https|ssh|git)://|^[\\w.-]+@[\\w.-]+:", url):',
         "if not base:", "test_reverify_b_forged_baseline_is_not_trusted"),
        ("replace objects", 'NO_REPLACE = "--no-replace-objects"', 'NO_REPLACE = "--no-pager"',
         "test_reverify_b_forged_baseline_is_not_trusted"),
        ("ref commands", "if _rewrites_refs(sub, sargs, args[:len(args) - len(rest)]):", "if False:",
         "test_reverify_b_baseline_ref_commands_need_a_person"),
        (".git writes", 'elif ".git" in [part.lower() for part in (*p.parts, *Path(os.path.realpath(str(p))).parts)]:', "elif False:",
         "test_reverify_b_baseline_ref_commands_need_a_person"),
        ("index flags", ' and flags.get(r) == "H"', "", "test_review_committed_script_and_index_flags"),
        ("build inputs", 'if not _clean_build_inputs(cwd, "gradle") or not _clean_script(toks[0], cwd, None):', "if False:", "test_review_changed_build_inputs"),
        ("ignored npm inputs", 'NPM_INPUTS = ("package.json", "package-lock.json", "npm-shrinkwrap.json", ".npmrc")',
         'NPM_INPUTS = ("package.json", "package-lock.json", "npm-shrinkwrap.json")',
         "test_reverify_f_trusted_npm_script_env_and_ignored_inputs"),
        ("environment", 'out.append(_finding("opaque", seg, reason="환경 변수 접두가 실행 동작을 바꿀 수 있음"))',
         "pass", "test_review_environment_prefixes"),
        ("same-line environment", "if not trusted and _changes_env(words):", "if False:",
         "test_reverify_c_environment_changed_on_the_same_line"),
        ("cd after keywords", "words = _lead(tokens(seg))", "words = tokens(seg)", "test_reverify_d_cd_variants"),
        ("package runners", 'out.append(_finding("opaque", seg, reason="허용 목록 밖 패키지 실행기"))', "pass",
         "test_reverify_e_package_runners_and_npm_options"),
        ("npm global options", "i += 2 if args[i] in NPM_OPTS_WITH_ARG else 1", "break",
         "test_reverify_e_package_runners_and_npm_options"),
        ("npm name", 'if re.search(r"deploy|release|publish|push", name, re.I):',
         "if False:", "test_review_npm_deploy_scripts"),
        ("npm body", "out.extend(classify_command(body, cwd, root, depth + 1, trusted=bool(allowed)))",
         "out.extend([])", "test_review_npm_deploy_scripts"),
        ("unittest paths", "return _unittest_args(args[isolated + 2:], cwd) and", "return True and",
         "test_reverify_g_h_unittest_paths_interpreter_names_and_shadowing"),
        ("script siblings", 'if _git(top, "ls-files", "--others", "-z", "--", spec):', "if False:",
         "test_reverify_g_h_unittest_paths_interpreter_names_and_shadowing"),
        # (interpreter names: removed in round 4 — an unrecognised pythonw is now opaque by default ask, so the
        #  mutation only makes the guard stricter)
        # 4차: 기본 ask 구조
        ("default ask", "if not explained and not trusted:", "if False:", "test_r4_unlisted_programs_modules_and_scripts_ask"),
        ("python -m other modules", 'reason = f"python -m {module}은 자동 허용 목록 밖(unittest만 허용)"', "ok = True",
         "test_r4_unlisted_programs_modules_and_scripts_ask"),
        ("unknown git subcommand", 'if not re.fullmatch(r"-.*", sub):', "if False:", "test_r4_unlisted_programs_modules_and_scripts_ask"),
        ("gh groups", 'if pos and (pos[0] not in GH_GROUPS or pos[0] == "auth" and pos[1:2] != ["status"]):', "if False:",
         "test_r4_unlisted_programs_modules_and_scripts_ask"),
        ("wrappers", "toks = drop_opts(toks[1:], with_value)[positional:]", "break", "test_r4_wrappers_are_analysed_not_trusted"),
        ("xargs arguments", 'if ctx.get("stdin_args") and word not in STDIN_ARGS_SAFE:', "if False:", "test_r4_wrappers_are_analysed_not_trusted"),
        ("find -exec", "if _word(inner[0]) not in STDIN_ARGS_SAFE:", "if False:", "test_r4_wrappers_are_analysed_not_trusted"),
        ("escapes kept while lexing", "        cmd = bash_escapes(cmd)\n", "        cmd = restore(bash_escapes(cmd))\n",
         "test_r4_escaped_quote_does_not_hide_a_command"),
        ("unittest shadowing", '(isolated or not _shadowed(cwd, "unittest"))', "True", "test_r4_unittest_shadowing"),
        ("read-only npm first", "allowed = not readonly and ", "allowed = ", "test_r4_read_only_npm_runs_no_git_and_npm_test_few"),
        ("variable arguments", "if not trusted and word not in READ_ONLY and re.search(", "if False and re.search(",
         "test_r4_git_default_ask"),
        ("git config keys", "            if not GIT_SAFE_CONFIG.match(key):", "            if False:", "test_r4_git_default_ask"),
        ("in-tree hooks", "if sub in GIT_RUNS_HOOKS and not _hooks_clean(where):", "if False:", "test_r4_in_tree_hooks_must_match_baseline"),
        ("git option analysis", 'out += _git_options(sub, sargs, seg, cwd, where, root)',
         'out += []', 'test_t4_git_execution_options'),
        ("git output targets", 'targets.append((out_long[name], val))',
         'pass', 'test_t4_output_paths'),
        ("read tool output targets", 'targets += _output_operands(word, args)',
         'pass', 'test_t4_output_paths'),
        ("cmd secret expansion", 'out += _cmd_variables(" ".join(args), seg)',
         'out += []', 'test_t4_secret_expansions'),
        ("powershell env drive", 'if m and (not m.group(1) or re.search(r"[*?\\[]", m.group(1)) or SECRET_NAME.search(m.group(1))):',
         'if False:', 'test_t4_secret_expansions'),
        ("sibling explanation", 'if blockers:', 'if False:', 'test_t4_sibling_message'),
        ("stdin output tools", 'STDIN_ARGS_SAFE = READ_ONLY - {"sort", "uniq", "tree", "rg", "diff", "date", "file"}',
         'STDIN_ARGS_SAFE = READ_ONLY', 'test_t4_hooks_and_unknown_outputs'),
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
