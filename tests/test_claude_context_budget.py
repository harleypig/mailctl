"""What an agent session loads on every turn stays under a budget (#467).

Claude Code loads ``.claude/CLAUDE.md`` at the start of every session in
this repo, with every file it ``@``-imports, recursively. It also loads
every ``.claude/rules/*.md`` that has no ``paths:`` frontmatter; one that
has it loads only when a matching file is read or edited. So the
always-loaded set is CLAUDE.md, its imports, and any unscoped rule, and
it costs context on every turn whatever the work.

That set grew a few sentences per change and nothing left it, until it
was about 190 KB. #467 moved the detail into path-scoped rules; this
holds the result, so growth is a decision someone makes by raising
``BUDGET``, never drift. A rule written without ``paths:`` would load
always and quietly undo the cut, so that fails too.

How Claude Code reads both is its memory documentation (Claude Code
2.1.303, docs mirror fetched 2026-10-30): an import is ``@path`` outside
a code span or fenced block, resolved from the importing file, up to
four hops deep; *"Rules without a ``paths`` field are loaded
unconditionally"*.
"""

import re
from pathlib import Path

CLAUDE_DIR = Path(__file__).resolve().parent.parent / ".claude"

# Bytes. The always-loaded set measured 46,136 when #467 landed: CLAUDE.md
# 3,440, CONVENTIONS.md 36,944, TESTS.md 5,752. 50,000 leaves about 3.9 KB,
# room for a few ordinary additions to the cores, and is small enough that
# a section's worth of new detail lands in a path-scoped rule instead. Do
# not raise it to make a change fit without asking whether the change
# belongs in the core at all.
BUDGET = 50_000

# Claude Code follows imports this many hops from CLAUDE.md, and no further.
MAX_HOPS = 4

FENCE = re.compile(r"^(`{3,}|~{3,}).*?^\1", re.M | re.S)
CODE_SPAN = re.compile(r"(`+).+?\1", re.S)
# An '@' after a word character is an e-mail address or a decorator, not
# an import.
IMPORT = re.compile(r"(?<![\w@])@([^\s`]+)")


def imports(path):
    """The files ``path`` imports that exist, resolved from its directory."""
    text = CODE_SPAN.sub("", FENCE.sub("", path.read_text()))
    found = []
    for raw in IMPORT.findall(text):
        for candidate in (raw, raw.rstrip(".,;:)")):
            target = (path.parent / candidate).resolve()
            if target.is_file():
                found.append(target)
                break
    return found


def unscoped_rules(claude_dir):
    """Every rule under ``claude_dir/rules`` with no ``paths:`` list."""
    unscoped = []
    for rule in sorted((claude_dir / "rules").glob("**/*.md")):
        text = rule.read_text()
        front = re.match(r"---\n(.*?)\n---\n", text, re.S)
        scoped = front and re.search(
            r"^paths:\s*(\[\s*\S|\n\s+-\s+\S)", front.group(1), re.M
        )
        if not scoped:
            unscoped.append(rule)
    return unscoped


def always_loaded(claude_dir):
    """CLAUDE.md, what it imports within ``MAX_HOPS``, and unscoped rules."""
    seen = []
    frontier = [(claude_dir / "CLAUDE.md").resolve()]
    for _ in range(MAX_HOPS + 1):
        nxt = []
        for path in frontier:
            if path in seen:
                continue
            seen.append(path)
            nxt.extend(imports(path))
        frontier = nxt
    for rule in unscoped_rules(claude_dir):
        if rule.resolve() not in seen:
            seen.append(rule.resolve())
    return seen


def total_bytes(paths):
    return sum(len(p.read_bytes()) for p in paths)


# ------------------------------------------------------------------- repo


def test_always_loaded_set_is_within_budget():
    loaded = always_loaded(CLAUDE_DIR)
    size = total_bytes(loaded)
    sizes = ", ".join(f"{p.name} {len(p.read_bytes())}" for p in loaded)
    assert size <= BUDGET, (
        f"{size} bytes load on every turn ({sizes}), over the budget of "
        f"{BUDGET}: move detail into a path-scoped rule under "
        ".claude/rules/, or raise BUDGET as a decision"
    )


def test_every_rule_is_path_scoped():
    unscoped = [
        str(p.relative_to(CLAUDE_DIR)) for p in unscoped_rules(CLAUDE_DIR)
    ]
    assert not unscoped, (
        f"{unscoped} carry no paths: frontmatter, so they load on every turn"
    )


def test_the_walk_reaches_both_cores():
    # The control for the budget: a walk that read nothing would pass it.
    names = {p.name for p in always_loaded(CLAUDE_DIR)}
    assert {"CLAUDE.md", "CONVENTIONS.md", "TESTS.md"} <= names


# ----------------------------------------------------------------- planted


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_a_nested_import_is_counted(tmp_path):
    write(tmp_path / "CLAUDE.md", "See @a.md.\n")
    write(tmp_path / "a.md", "@sub/b.md\n")
    write(tmp_path / "sub" / "b.md", "x" * 10)
    names = [p.name for p in always_loaded(tmp_path)]
    assert names == ["CLAUDE.md", "a.md", "b.md"]


def test_a_quoted_path_or_address_is_not_an_import(tmp_path):
    write(
        tmp_path / "CLAUDE.md",
        "`@a.md` and someone@a.md\n\n```text\n@a.md\n```\n",
    )
    write(tmp_path / "a.md", "x")
    assert [p.name for p in always_loaded(tmp_path)] == ["CLAUDE.md"]


def test_imports_stop_after_four_hops(tmp_path):
    write(tmp_path / "CLAUDE.md", "@1.md\n")
    for n in range(1, 6):
        write(tmp_path / f"{n}.md", f"@{n + 1}.md\n")
    names = [p.name for p in always_loaded(tmp_path)]
    assert names == ["CLAUDE.md", "1.md", "2.md", "3.md", "4.md"]


def test_a_rule_without_paths_is_unscoped_and_counted(tmp_path):
    write(tmp_path / "CLAUDE.md", "core\n")
    write(
        tmp_path / "rules" / "scoped.md", '---\npaths:\n  - "a/**"\n---\nx\n'
    )
    write(tmp_path / "rules" / "inline.md", '---\npaths: ["a/**"]\n---\nx\n')
    write(tmp_path / "rules" / "bare.md", "# no frontmatter\n")
    write(tmp_path / "rules" / "empty.md", "---\npaths:\n---\nx\n")
    write(tmp_path / "rules" / "other.md", "---\ndescription: x\n---\nx\n")

    unscoped = {p.name for p in unscoped_rules(tmp_path)}
    assert unscoped == {"bare.md", "empty.md", "other.md"}

    loaded = {p.name for p in always_loaded(tmp_path)}
    assert loaded == {"CLAUDE.md", "bare.md", "empty.md", "other.md"}
