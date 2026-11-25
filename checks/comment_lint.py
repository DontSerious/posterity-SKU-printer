"""Comment ratchet: no source file may gain comment lines.

  comment_lint.py check  [--root R]          exit 1 when a file holds more comment lines than its baseline
  comment_lint.py update [--root R]          lower baseline entries to today's counts; never raises one
  comment_lint.py init   [--root R]          write the first baseline (refuses when one exists)
  comment_lint.py rename OLD NEW [--root R]  carry an entry over to a renamed file
  comment_lint.py stats  [--root R]          lines and comment lines per language
  comment_lint.py selftest                   the controls: a known-bad and a known-good sample per language

A comment line is a line any part of a comment lies on. Files absent from the baseline may have none.
Exempt: lines a tool reads (suppressions, PEP 723, licence headers, plus "allow" regexes in the config)
and a one-line pointer to a decision record, "ADR-0012" or "ADR 0012", alone in its comment.
Python docstrings are not comments here: argparse prints them.

Config: comment-lint.json at the root: {"include": ["src/**"], "exclude": [...], "allow": ["regex"]}.
Baseline: comment-lint.baseline.json, one file per line so that branches conflict only on the same file.
Stdlib only: it runs inside PlatformIO's own interpreter.

Canonical copy: ~/.claude/skills/delete-comment/scripts/comment_lint.py. Repos carry a byte-identical copy.
"""
import fnmatch
import io
import json
import os
import re
import subprocess
import sys
import tokenize

CONFIG = "comment-lint.json"
BASELINE = "comment-lint.baseline.json"
BUILTIN_ALLOW = [
    r"clang-format (on|off)", r"NOLINT", r"\bignore(_for_file)?: ", r"\bnoqa\b", r"type: ?ignore", r"pylint:",
    r"fmt: ?(on|off)", r"eslint-", r"@ts-(ignore|expect-error|nocheck)", r"prettier-ignore", r"pragma:",
    r"coverage:ignore", r"shellcheck disable", r"-\*- coding", r"SPDX-License-Identifier", r"Copyright",
    r"^/// ?<reference", r"GENERATED (CODE|FILE)", r"@dart ?=",
]
ADR_POINTER = re.compile(r"^[/*#<!\-\s;]*(see )?ADR[- ]\d{4}([,\s]+ADR[- ]\d{4})*[\s.*/\->]*$", re.I)
C_LIKE = {".cpp": "c", ".cc": "c", ".c": "c", ".h": "c", ".hpp": "c", ".ino": "c", ".java": "java", ".kt": "kt",
          ".kts": "kt", ".swift": "swift", ".gradle": "groovy", ".dart": "dart", ".js": "js", ".mjs": "js",
          ".cjs": "js", ".ts": "js", ".tsx": "js", ".m": "c", ".mm": "c", ".css": "css"}
HASH = {".sh": "sh", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".ini": "ini", ".service": "ini", ".ps1": "ps1"}
OTHER = {".py": "py", ".html": "html", ".sql": "sql", ".bat": "bat"}
LANG = {**C_LIKE, **HASH, **OTHER}
REGEX_BEFORE = set("(,=:[!&|?{};+-*%<>~^\n")
REGEX_WORDS = ("return", "typeof", "case", "in", "of", "delete", "void", "throw", "new", "else", "do")


def _end_of_line(text, i):
    j = text.find("\n", i)
    return len(text) if j < 0 else j


def _c_like(text, lang):
    spans, i, n = [], 0, len(text)
    stack = []
    last = "\n"
    word = ""
    nested = lang in ("kt", "swift", "dart")
    while i < n:
        c = text[i]
        if stack and stack[-1][0] == "str":
            _, quote, raw, interp = stack[-1]
            if not raw and c == "\\":
                if interp == "\\(" and text.startswith("\\(", i):
                    stack.append(["code", "(", 1])
                    i += 2
                    continue
                i += 2
                continue
            if interp == "$" and text.startswith("$" + "{", i):
                stack.append(["code", "{", 1])
                i += 2
                continue
            if text.startswith(quote, i):
                i += len(quote)
                stack.pop()
                last = '"'
                continue
            if c == "\n" and len(quote) == 1 and quote != "`":
                stack.pop()
            i += 1
            continue
        if text.startswith("/*", i):
            j, depth = i + 2, 1
            while j < n and depth:
                if text.startswith("*/", j):
                    depth -= 1
                    j += 2
                elif nested and text.startswith("/*", j):
                    depth += 1
                    j += 2
                else:
                    j += 1
            spans.append((i, j))
            i = j
            continue
        if lang != "css" and text.startswith("//", i):
            j = _end_of_line(text, i)
            while lang == "c" and text[i:j].endswith("\\") and j < n:
                j = _end_of_line(text, j + 1)
            spans.append((i, j))
            i = j
            continue
        if lang == "c" and c == '"' and re.search(r'(?:^|[^A-Za-z0-9_])(?:u8|u|U|L)?R$', text[max(0, i - 4):i]):
            m = re.compile(r'"([^()\\\s]{0,16})\(').match(text, i)
            if m:
                j = text.find(")" + m.group(1) + '"', m.end())
                i = n if j < 0 else j + len(m.group(1)) + 2
                last = '"'
                continue
        if c in "\"'" or (c == "`" and lang == "js"):
            if lang == "c" and c == "'" and i and text[i - 1].isalnum() and re.search(r"(?:^|[^\w'])[0-9][\w']*$", text[max(0, i - 24):i]):
                i += 1
                continue
            quote = c
            if lang in ("dart", "kt", "java", "swift", "groovy") and text.startswith(c * 3, i):
                quote = c * 3
            raw = lang == "dart" and i and text[i - 1] == "r" and not (i > 1 and (text[i - 2].isalnum() or text[i - 2] in "_$"))
            interp = None
            if lang in ("dart", "kt", "groovy") and not raw:
                interp = "$"
            if lang == "js" and c == "`":
                interp = "$"
            if lang == "swift":
                interp = "\\("
            if lang == "kt" and quote == '"""':
                raw = True
                interp = "$"
            stack.append(["str", quote, raw, interp])
            i += len(quote)
            continue
        if lang == "js" and c == "/" and (last in REGEX_BEFORE or word in REGEX_WORDS):
            m = re.compile(r"/(?:\\.|\[(?:\\.|[^\]\\\n])*\]|[^/\\\[\n])+/[a-z]*").match(text, i)
            if m:
                i = m.end()
                last = '"'
                word = ""
                continue
        if stack and stack[-1][0] == "code":
            opener = stack[-1][1]
            closer = "}" if opener == "{" else ")"
            if c == opener:
                stack[-1][2] += 1
            elif c == closer:
                stack[-1][2] -= 1
                if not stack[-1][2]:
                    stack.pop()
                    i += 1
                    continue
        if c.isalnum() or c in "_$":
            word = word + c if last.isalnum() or last in "_$" else c
            last = c
        elif not c.isspace():
            last = c
            word = ""
        i += 1
    return spans


def _hash(text, lang):
    spans = []
    i, n = 0, len(text)
    block_indent = None
    quote = None
    for m in re.finditer(r"[^\n]*(?:\n|$)", text):
        line, start = m.group(0), m.start()
        if not line:
            break
        body = line.rstrip("\n")
        if start < i:
            continue
        if lang == "yaml" and block_indent is not None and quote is None:
            ind = len(body) - len(body.lstrip(" "))
            if not body.strip() or ind > block_indent:
                continue
            block_indent = None
        k = 0
        while k < len(body):
            ch = body[k]
            if quote:
                if ch == "\\" and quote in ('"', '"""') and lang not in ("ini", "ps1"):
                    k += 2
                    continue
                if lang == "ps1" and ch == "`" and quote == '"':
                    k += 2
                    continue
                if body.startswith(quote, k):
                    k += len(quote)
                    quote = None
                    continue
                k += 1
                continue
            if lang == "ps1" and body.startswith("<#", k):
                j = text.find("#>", start + k)
                j = n if j < 0 else j + 2
                spans.append((start + k, j))
                i = _end_of_line(text, j)
                break
            if ch == "#" and (k == 0 or body[k - 1].isspace() or (lang in ("toml", "ps1") and body[k - 1] != "$")) and (lang != "ini" or not body[:k].strip() or lang == "ini" and body[k - 1].isspace()):
                if not (start == 0 and body.startswith("#!")):
                    spans.append((start + k, start + len(body)))
                break
            if ch == ";" and lang == "ini" and (not body[:k].strip() or body[k - 1].isspace()):
                spans.append((start + k, start + len(body)))
                break
            if lang == "sh" and ch == "\\":
                k += 2
                continue
            if ch in "\"'" and lang != "ini" and not (lang == "yaml" and k and body[k - 1] not in " [{,:(-"):
                quote = ch * 3 if lang == "toml" and body.startswith(ch * 3, k) else ch
                k += len(quote)
                continue
            k += 1
        if quote and len(quote) == 1 and lang not in ("sh", "yaml", "ps1"):
            quote = None
        if lang == "yaml" and quote is None and re.search(r"(?:^|[:\-]\s+)[|>][+-]?\d?\s*(?:#.*)?$", body):
            block_indent = len(body) - len(body.lstrip(" "))
    return spans


def _py(text):
    starts = [0] + [m.end() for m in re.finditer("\n", text)]
    spans = []
    try:
        for t in tokenize.generate_tokens(io.StringIO(text).readline):
            if t.type == tokenize.COMMENT and not (t.start == (1, 0) and t.string.startswith("#!")):
                a = starts[t.start[0] - 1] + t.start[1]
                spans.append((a, a + len(t.string)))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        pass
    return _drop_pep723(text, spans)


def _drop_pep723(text, spans):
    m = re.search(r"(?m)^# /// script$(?:\n^#(?: .*)?$)*?\n^# ///$", text)
    return [s for s in spans if not (m and m.start() <= s[0] < m.end())] if m else spans


def _html(text):
    spans = []
    pattern = re.compile(r"<!--.*?-->|\{#.*?#\}|<script\b[^>]*>(.*?)</script>|<style\b[^>]*>(.*?)</style>", re.S | re.I)
    for m in pattern.finditer(text):
        if m.group(1) is not None:
            spans += [(a + m.start(1), b + m.start(1)) for a, b in _c_like(m.group(1), "js")]
        elif m.group(2) is not None:
            spans += [(a + m.start(2), b + m.start(2)) for a, b in _c_like(m.group(2), "css")]
        else:
            spans.append((m.start(), m.end()))
    return sorted(set(spans))


def _sql(text):
    return [(m.start(), m.end()) for m in re.finditer(r"'(?:[^']|'')*'|--[^\n]*|/\*.*?\*/", text, re.S) if m.group(0)[0] != "'"]


def _bat(text):
    return [(m.start(), m.end()) for m in re.finditer(r"(?im)^[ \t]*(?:@?rem\b|::)[^\n]*", text)]


def comment_spans(text, ext):
    """[(start, end)] of every comment in text, tool-read ones included."""
    lang = LANG[ext]
    if ext in C_LIKE:
        return _c_like(text, lang)
    if ext in HASH:
        return _hash(text, lang)
    return {"py": _py, "html": _html, "sql": _sql, "bat": _bat}[lang](text)


def counted_lines(text, ext, allow):
    """The line numbers that count, after exemptions."""
    starts = [m.end() for m in re.finditer("\n", text)]
    lines = set()
    import bisect
    for a, b in comment_spans(text, ext):
        body = text[a:b]
        if ADR_POINTER.match(body) or any(r.search(body) for r in allow):
            continue
        lines.update(range(bisect.bisect_right(starts, a), bisect.bisect_right(starts, max(a, b - 1)) + 1))
    return lines


def read(path):
    raw = open(path, "rb").read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return text.replace("\r\n", "\n")


def load(root, name):
    try:
        with open(os.path.join(root, name), encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None


def glob_re(g):
    return re.compile("^" + re.escape(g).replace(r"\*\*/", "(?:.*/)?").replace(r"\*\*", ".*").replace(r"\*", "[^/]*") + "$")


def files(root, cfg):
    out = subprocess.run(["git", "-C", root, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "."], capture_output=True)
    if out.returncode:
        sys.exit("comment_lint: not a git work tree: " + root)
    inc = [glob_re(g) for g in cfg.get("include", ["**"])]
    exc = [glob_re(g) for g in cfg.get("exclude", [])]
    for rel in sorted(set(out.stdout.decode("utf-8", "replace").split("\0"))):
        if os.path.splitext(rel)[1].lower() not in LANG or not os.path.isfile(os.path.join(root, rel)):
            continue
        if any(r.match(rel) for r in inc) and not any(r.match(rel) for r in exc):
            yield rel


def measure(root, cfg):
    allow = [re.compile(p) for p in BUILTIN_ALLOW + cfg.get("allow", [])]
    return {rel: len(counted_lines(read(os.path.join(root, rel)), os.path.splitext(rel)[1].lower(), allow)) for rel in files(root, cfg)}


def save(root, counts):
    body = ",\n".join(f"  {json.dumps(k)}: {v}" for k, v in sorted(counts.items()) if v)
    with open(os.path.join(root, BASELINE), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("{\n" + body + "\n}\n")


SAMPLES = [
    (".cpp", 'int a = 1\'000; // x\nauto s = R"(// no)";\nchar c = \'"\'; /* y\nz */\n', {1, 3, 4}),
    (".cpp", 'const char* u = "http://x"; // clang-format off\n// ADR-0012\n', set()),
    (".dart", "var s = '$" + "{m['// no']} // no';\n/// doc\nvar r = r'\\'; // t\n", {2, 3}),
    (".js", "const r = /\\/\\//; const t = `a // no $" + "{b /* c */}`;\n// d\n", {1, 2}),
    (".py", '# c\ns = "# no"\n"""doc\n# no"""\nx = 1  # noqa: F821\n', {1}),
    (".yaml", "a: '# no'\nb: |\n  # no\nc: 1 # yes\n", {4}),
    (".html", "<!-- a -->\n<script>var u='//no'; // b\n</script>{# c #}\n", {1, 2, 3}),
    (".css", "a{b:url(//no)} /* c */\n", {1}),
    (".ps1", '<# a\nb #>\n$x = "# no" # c\n', {1, 2, 3}),
    (".sql", "select '-- no'; -- c\n", {1}),
    (".toml", 'a = "#no" # c\n', {1}),
]


def selftest():
    bad = 0
    allow = [re.compile(p) for p in BUILTIN_ALLOW]
    for ext, text, want in SAMPLES:
        got = {x + 1 for x in counted_lines(text, ext, allow)}
        ok = got == want
        bad += not ok
        print("[CONTROL]", "OK  " if ok else "FAIL", ext, f"actual={sorted(got)} expected={sorted(want)}")
    return 2 if bad else 0


def check(root):
    """(failures, lowered) against the baseline."""
    cfg, base = load(root, CONFIG), load(root, BASELINE)
    if cfg is None or base is None:
        sys.exit(f"comment_lint: {CONFIG} or {BASELINE} is missing under {root}")
    now = measure(root, cfg)
    return [(f, v, base.get(f, 0)) for f, v in now.items() if v > base.get(f, 0)], [f for f, v in base.items() if now.get(f, 0) < v]


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    root = os.getcwd()
    if "--root" in argv:
        k = argv.index("--root")
        root = argv[k + 1]
        del argv[k:k + 2]
    cmd = argv[0] if argv else "check"
    if cmd == "selftest":
        return selftest()
    cfg = load(root, CONFIG) or {}
    if cmd == "init":
        if load(root, BASELINE) is not None:
            sys.exit("comment_lint: baseline exists; use update")
        save(root, measure(root, cfg))
        return 0
    if cmd == "update":
        base, now = load(root, BASELINE) or {}, measure(root, cfg)
        save(root, {f: min(v, now.get(f, 0)) for f, v in base.items()})
        return 0
    if cmd == "rename":
        base = load(root, BASELINE)
        base[argv[2]] = base.pop(argv[1])
        save(root, base)
        return 0
    if cmd == "stats":
        totals = {}
        for rel, v in measure(root, cfg).items():
            t = totals.setdefault(os.path.splitext(rel)[1].lower(), [0, 0, 0])
            t[0] += 1
            t[1] += read(os.path.join(root, rel)).count("\n")
            t[2] += v
        for ext, (f, l, c) in sorted(totals.items()):
            print(f"{ext:9}{f:6} files {l:8} lines {c:7} comment lines")
        return 0
    over, lowered = check(root)
    for f, v, b in over:
        print(f"{f}: {v} comment lines, baseline {b}. Put the decision in docs/adr/ or the code, not in a comment.")
    if lowered:
        print(f"comment_lint: {len(lowered)} file(s) are below baseline; run: comment_lint.py update")
    print("comment_lint:", "FAIL" if over else "ok", f"({len(over)} over)")
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(main())
