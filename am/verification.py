"""Recognize the small Bash language used to inspect review artifacts. Never evaluate it."""
import re


# Keep the spelling of words until expansion is checked. Unsupported syntax ends the proof.
TOKEN = re.compile(r"""[ \t]+|&&|\|\||[;|)]|[A-Za-z_]\w*=\$\(|2>&1|[012]?>[ \t]*/dev/null|(?:[\w./:=,%+@~*$\[\]-]|'[^'\n]*'|"[^"\n\\!]*")+""")
SEPARATORS = {";", "&&", "||", "|"}
NULL_REDIRECT = re.compile(r"(?:2>&1|[012]?>[ \t]*/dev/null)")
PATH = re.compile(r"(?!-)[\w./~:][\w./: -]*")
# Shells have writable special parameters (including lowercase names in zsh).
PATH_NAMES = {"R", "r", "f", "d", "p", "file", "dir", "root", "src", "folder", "filename", "target", "report"}

# Positive option sets for the commands with output operands, helpers or script options.
SHORT_OPTIONS = {
    "grep": "abcEFHhIiLlmnorRsVvwxABCq", "rg": "abcEFHhIilnorsSuvwxABC",
    "ls": "1aAbBcCdFfGghHilLmnpqRrSstTuUX", "cat": "AbEnstTuv", "wc": "clLmw",
    "head": "cnqv", "tail": "cnqv", "sort": "bdfghiMnRrstuV", "uniq": "cdiu",
    "cut": "bcdfs", "tr": "cdstC",
}
GIT_FLAGS = {"--oneline", "--stat", "--shortstat", "--name-only", "--name-status", "--no-ext-diff",
             "--no-textconv", "--no-color", "--color=never", "--short", "--porcelain", "--cached", "--staged"}
AWK_ATOM = r"(?:NR|FNR|NF|\$\d+|\d+|[a-z_][a-z_0-9]*)"
COMPARISON = rf"{AWK_ATOM}\s*(?:>=?|<=?|==|!=)\s*{AWK_ATOM}"
AWK_TEST = re.compile(rf"{COMPARISON}(?:\s*&&\s*{COMPARISON})*")
AWK_ACTION = re.compile(rf"(?:(?:BEGIN|END)\s*)?\{{\s*(?:print(?:\s+{AWK_ATOM}(?:\s*,\s*{AWK_ATOM})*)?|"
                        rf"[a-z_][a-z_0-9]*\s*\+?=\s*{AWK_ATOM})\s*;?\s*\}}")


def _awk(args):
    if args[:1] == ["-F"] and len(args) >= 2:
        args = args[2:]
    elif args and args[0].startswith("-F") and len(args[0]) > 2:
        args = args[1:]
    if not args or any(not PATH.fullmatch(path) for path in args[1:]):
        return False
    program = args[0].strip()
    if re.search(r"\b(?:getline|system|close)\b", program):
        return False
    if not program:
        return False
    if AWK_TEST.fullmatch(program):
        return True
    # Comparisons may use >, but a print redirection can never match an action.
    while program:
        predicate = AWK_TEST.match(program)
        if predicate:
            program = program[predicate.end():].lstrip()
        action = AWK_ACTION.match(program)
        if not action:
            return False
        program = program[action.end():].strip()
    return True


def _reads(word, args):
    if word == "awk":
        return _awk(args)
    if word == "sed":
        address = r"(?:\d+|\$|/[^/\\;\n]*/)"
        command = rf"(?:{address}(?:,{address})?)?\s*(?:[pPdqn=]|s/[^/\\;\n]*/[^/\\;\n]*/[gpi0-9]*)"
        return (len(args) >= 2 and args[0] == "-n" and re.fullmatch(command, args[1])
                and all(PATH.fullmatch(a) for a in args[2:]))
    if word == "git":
        return args == ["branch", "--show-current"] or (args and args[0] in {"log", "show", "diff", "status", "grep"}
                and all(not a.startswith("-") or a in GIT_FLAGS or re.fullmatch(r"-(?:-|[pinUq]+|\d+)", a)
                        for a in args[1:]))
    if word == "find":
        return all(not a.startswith("-") or a in {"-type", "-name", "-iname", "-path", "-maxdepth", "-mindepth",
                                                  "-print", "-print0", "-size", "-mtime", "-o", "-a"} for a in args)
    if word in {"test", "["}:
        expr = args[:-1] if word == "[" and args[-1:] == ["]"] else args
        return ((len(expr) == 2 and expr[0] in {"-d", "-f", "-e", "-s", "-r", "-z", "-n"})
                or (len(expr) == 3 and expr[1] in {"=", "==", "!="}) or len(expr) == 1)
    if word in {"echo", "printf"}:
        return word == "echo" or bool(args) and not args[0].startswith("-") and re.fullmatch(r"(?:[^%]|%[%s])*", args[0])
    if word not in SHORT_OPTIONS:
        return False
    if word == "uniq" and sum(not a.startswith("-") for a in args) > 1:
        return False
    flags = SHORT_OPTIONS[word]
    return all(not a.startswith("-") or a in ("-", "--") or re.fullmatch(rf"-[{flags}0-9]+", a) for a in args)


def _argument(raw, bindings, word, index, operands):
    if raw.startswith("'") and raw.endswith("'") and raw.count("'") == 2:
        return raw[1:-1]
    quoted = raw.startswith('"') and raw.endswith('"') and raw.count('"') == 2
    if quoted:
        raw = raw[1:-1]
    elif raw.startswith("-F'") and raw.endswith("'") and raw.count("'") == 2:
        return "-F" + raw[3:-1]
    elif "'" in raw or '"' in raw:
        return None
    elif "*" in raw or "[" in raw:
        # A glob can expand into options. Only plain grep takes unquoted filename globs here.
        if word != "grep" or not re.fullmatch(r"\*\.[\w]+", raw) or not index:
            return None
    if "$" not in raw:
        return raw
    if not raw.startswith("$") or raw[1:] not in bindings:
        return None
    path = bindings[raw[1:]]
    if word == "awk":
        program_index = 2 if operands[0] == "-F" else int(operands[0].startswith("-F"))
        if index <= program_index:
            return None
    if word == "sed" and index < 2 or word == "git" and index == 0:
        return None
    if path is None:
        # Unquoted output may split into several words. Only grep's trailing input slot
        # accepts that shape: never feed it to an option that runs code or names an output.
        has_pattern = any(not a.strip("'\"").startswith(("-", "$")) for a in operands[:index])
        return "input" if word == "grep" and index == len(operands) - 1 and has_pattern else None
    return path if quoted or " " not in path else None


def _pipeline(items, cwd, cd_target, bindings, allow_bindings):
    start = 0
    has_read = False
    while start < len(items):
        end = start
        while end < len(items) and items[end] not in SEPARATORS:
            if items[end].endswith("=$("):
                if not allow_bindings or end != start:
                    return False
                try:
                    end = items.index(")", end + 1)
                except ValueError:
                    return False
            end += 1
        stage = items[start:end]
        before = items[start - 1] if start else None
        after = items[end] if end < len(items) else None
        start = end + 1
        if not stage or after and start == len(items):
            return False
        word = stage[0]
        name, eq, value = word.partition("=")
        if eq:
            if not allow_bindings or name not in PATH_NAMES or before not in (None, ";") or after != ";":
                return False
            if value == "$(" and stage[-1] == ")":
                if not _pipeline(stage[1:-1], cwd, cd_target, bindings.copy(), False):
                    return False
                bindings[name] = None
            elif len(stage) == 1:
                path = _argument(value, {}, "", 0, [])
                if path is None or not PATH.fullmatch(path):
                    return False
                bindings[name] = path
            else:
                return False
            continue
        if any(NULL_REDIRECT.fullmatch(item) for item in stage[1:]):
            if word in {"echo", "printf"}:
                return False
            stage = [item for item in stage if not NULL_REDIRECT.fullmatch(item)]
        operands = stage[1:]
        args = [_argument(raw, bindings, word, i, operands) for i, raw in enumerate(operands)]
        if None in args or ")" in stage:
            return False
        if word == "cd":
            if (before in {"|", "||", "&&"} or after not in {";", "&&"} or len(args) != 1
                    or not PATH.fullmatch(args[0])):
                return False
            cwd = cd_target(args[0], cwd)
            if cwd is None:
                return False
        elif _reads(word, args):
            has_read = True
        else:
            return False
    return has_read


def readonly(command, cwd, cd_target):
    # Only the exit status of this exact, silent daemon probe is used in a verification line.
    # Reject it on its own, with options, in a substitution, or with a different output sink.
    command = command.strip(" \t").removesuffix("; docker info >/dev/null 2>&1 && echo docker=yes")
    if any(c in command for c in ("\\", "`", "\n", "\r", "\0")):
        return False
    items = []
    pos = 0
    while pos < len(command):
        match = TOKEN.match(command, pos)
        if not match:
            return False
        value = match.group()
        pos = match.end()
        if NULL_REDIRECT.fullmatch(value) and pos < len(command) and command[pos] not in " \t;&|":
            return False
        if not value.isspace():
            items.append(value)
    return _pipeline(items, cwd, cd_target, {}, True)
