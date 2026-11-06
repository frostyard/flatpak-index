"""The repository baseline: core ADR-0071, rows B1 to B17 that apply here.

Each check is a function that takes a repository root. It raises BaselineError
with the row ID and what is wrong, or returns None. The tests at the bottom run
each check on this tree, then break each row in a copy outside the repository
and assert that the check fails (a guard needs a negative test of its own).

The SHA pins and the version comments of every action are checked by
tests/test_workflows.py; this file does not repeat that.
"""

import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ALIASES = ("CLAUDE.md", "GEMINI.md", ".cursorrules", ".github/copilot-instructions.md")
CORRECTION_KEYS = {"date", "scope", "correction", "evidence", "promoted_to"}
DOCS_PATHS = (
    "docs/README.md",
    "docs/org-adrs.md",
    "docs/adr/TEMPLATE.md",
    "docs/design/TEMPLATE.md",
    "docs/specs/TEMPLATE.md",
    "docs/plans/TEMPLATE.md",
)
FRAMEWORK_TREES = ("docs/superpowers", ".planning", ".cursor/rules", ".gaai", "yeti", "cairn")
MARKER = "<!-- readme-kind: packaging -->"
CORE_RISK_PATHS = re.compile(
    r"uses:\s+frostyard/core/\.github/workflows/risk-paths\.yml@[0-9a-f]{40}(?:\s|$)")
TOP_PERMISSIONS = re.compile(r"^permissions:(.*)$")
PERMISSION_ITEM = re.compile(r"^(\s+)([\w-]+):\s*(\S+)\s*$")
LIST_ITEM = re.compile(r"^(\s*)-(?:\s(.*))?$")
YES = re.compile(r"^(?:yes|true)$")


class BaselineError(AssertionError):
    """A baseline row fails."""


def fail(row, message):
    raise BaselineError(f"{row}: {message}")


def read(root, name):
    """The text of a file under root; a missing file fails the row."""
    path = Path(root) / name
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def workflows(root):
    """The path and text of each workflow."""
    folder = Path(root) / ".github" / "workflows"
    if not folder.is_dir():
        return []
    return [(path, path.read_text(encoding="utf-8")) for path in sorted(folder.glob("*.yml"))]


def indented_block(lines, start):
    """The lines of the list item that starts at start, without its siblings.

    A step can start with a bare "-" line, with "- uses: ...", or with
    "- name: ..." followed by its other keys, so the block is found by
    indentation, not by the item's first key.
    """
    indent = len(LIST_ITEM.match(lines[start]).group(1))
    block = [lines[start]]
    for line in lines[start + 1:]:
        if not line.strip():
            block.append(line)
            continue
        current = len(line) - len(line.lstrip())
        if current < indent:
            break
        item = LIST_ITEM.match(line)
        if item and len(item.group(1)) == indent:
            break
        block.append(line)
    return block


def check_b1(root):
    """AGENTS.md is a regular file; the 4 aliases are relative symlinks to it."""
    path = Path(root) / "AGENTS.md"
    if path.is_symlink() or not path.is_file():
        fail("B1", "AGENTS.md is not a regular file")
    for name in ALIASES:
        alias = Path(root) / name
        if not alias.is_symlink():
            fail("B1", f"{name} is not a symlink to AGENTS.md")
        target = os.readlink(alias)
        if os.path.isabs(target):
            fail("B1", f"{name} has the absolute target {target}; use a relative one")
        if (alias.parent / target).resolve() != path.resolve():
            fail("B1", f"{name} resolves to {target}, not to AGENTS.md")


def check_b2(root):
    """.memory/corrections.jsonl: each line is an object with the 5 fields."""
    text = read(root, ".memory/corrections.jsonl")
    if text is None:
        fail("B2", ".memory/corrections.jsonl is missing")
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError as error:
            fail("B2", f"line {number} is not JSON: {error}")
        if not isinstance(entry, dict):
            fail("B2", f"line {number} is not a JSON object")
        if set(entry) != CORRECTION_KEYS:
            fail("B2", f"line {number} has the keys {sorted(entry)}, not the 5 required ones")
        for key in sorted(CORRECTION_KEYS):
            if not isinstance(entry[key], str) or not entry[key].strip():
                fail("B2", f"line {number}: {key} is not a non-empty string")


def check_b3(root):
    """The docs shape, each category with its template, and the org ADR list."""
    for name in DOCS_PATHS:
        if not (Path(root) / name).is_file():
            fail("B3", f"{name} is missing")
    index = read(root, "docs/README.md")
    if not re.search(r"\]\(\.?/?org-adrs\.md\)", index):
        fail("B3", "docs/README.md does not link org-adrs.md")


def check_b4(root):
    """The Makefile has verify, check and ci, ci runs verify, mise pins the tools."""
    text = read(root, "Makefile")
    if text is None:
        fail("B4", "Makefile is missing")
    rules = {}
    for line in text.splitlines():
        match = re.match(r"^([\w-]+):\s*([^=#]*)(?:#.*)?$", line)
        if match:
            rules[match.group(1)] = match.group(2).split()
    for target in ("verify", "check", "ci"):
        if target not in rules:
            fail("B4", f"the Makefile has no {target} rule")
    if "verify" not in rules["ci"]:
        fail("B4", f"ci depends on {rules['ci'] or 'nothing'}, not on verify")
    for name in ("mise.toml", "mise.lock"):
        if not (Path(root) / name).is_file():
            fail("B4", f"{name} is missing")


def check_b5(root):
    """Least privilege: checkouts keep no credentials, top-level permissions are read-only.

    The SHA pins and the version comments are tests/test_workflows.py.
    """
    files = workflows(root)
    if not files:
        fail("B5", "there is no .github/workflows/*.yml")
    for path, text in files:
        lines = text.splitlines()
        top = None
        for line in lines:
            match = TOP_PERMISSIONS.match(line)
            if match:
                top = match
                break
        if top is None:
            fail("B5", f"{path.name} has no top-level permissions")
        value = top.group(1).strip()
        if value != "{}":
            name = path.name
            if not value:
                index = lines.index(top.group(0))
                granted = []
                for line in lines[index + 1:]:
                    item = PERMISSION_ITEM.match(line)
                    if not item:
                        break
                    granted.append((item.group(2), item.group(3)))
                if not granted:
                    fail("B5", f"{name} has a top-level permissions key with no value")
                for scope, level in granted:
                    if level not in ("read", "none"):
                        fail("B5", f"{name} grants {scope}: {level} at the top level")
            elif value not in ("read-all", "none"):
                fail("B5", f"{path.name} has the top-level permissions {value}")
        for number, line in enumerate(lines):
            item = LIST_ITEM.match(line)
            if not item:
                continue
            block = "\n".join(indented_block(lines, number))
            if "actions/checkout" not in block:
                continue
            if not re.search(r"^\s*persist-credentials:\s*false\s*$", block, re.M):
                fail("B5", f"{path.name} line {number + 1}: a checkout does not set persist-credentials: false")


def check_b6(root):
    """.github/risk-paths.json and the caller workflow, covering the deploy and the trust list."""
    text = read(root, ".github/risk-paths.json")
    if text is None:
        fail("B6", ".github/risk-paths.json is missing")
    try:
        document = json.loads(text)
    except ValueError as error:
        fail("B6", f".github/risk-paths.json is not JSON: {error}")
    if document.get("schema_version") != 1:
        fail("B6", "schema_version is not 1")
    entries = document.get("paths")
    if not isinstance(entries, list) or not entries:
        fail("B6", "paths is not a non-empty list")
    globs = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"glob", "why"}:
            fail("B6", f"an entry is {entry}, not a glob and a why")
        if not entry["glob"].strip() or not entry["why"].strip():
            fail("B6", f"the entry {entry} has an empty glob or why")
        if not re.match(r"^(?:boot chain|signing and keys|update verification|auth|publication|"
                        r"secrets and credentials|rulesets and settings|destructive operations|"
                        r"governance): ", entry["why"]):
            fail("B6", f"the why of {entry['glob']} does not name a category of the spec")
        globs.append(entry["glob"])
    for required in (".github/workflows/publish.yml", "scripts/build-index.py", "apps.json",
                     "frostyard.flatpakrepo"):
        if not any(match(glob, required) for glob in globs):
            fail("B6", f"no glob matches {required}")
    if not any(CORE_RISK_PATHS.search(text) for _, text in workflows(root)):
        fail("B6", "no workflow calls frostyard/core/.github/workflows/risk-paths.yml at a commit SHA")


def match(glob, path):
    """Match a path against a risk-path glob: *, ** and {a,b}, as the spec says."""
    if "{" in glob and "}" in glob:
        head, rest = glob.split("{", 1)
        middle, tail = rest.split("}", 1)
        return any(match(head + alternative + tail, path) for alternative in middle.split(","))
    pattern = ""
    for index, part in enumerate(glob.split("/")):
        if index:
            pattern += "/"
        pattern += ".*" if part == "**" else re.escape(part).replace(r"\*", "[^/]*")
    return re.fullmatch(pattern, path) is not None


def check_b7(root):
    """.github/pull_request_template.md has a risk tier section and names the gates."""
    text = read(root, ".github/pull_request_template.md")
    if text is None:
        fail("B7", ".github/pull_request_template.md is missing")
    if not re.search(r"(?m)^#{1,6} .*Risk tier", text):
        fail("B7", "the pull request template has no Risk tier heading")
    for command in ("make verify", "make ci"):
        if command not in text:
            fail("B7", f"the pull request template does not name {command}")


def check_b12(root):
    """ci.yml runs make ci on pull requests."""
    text = read(root, ".github/workflows/ci.yml")
    if text is None:
        fail("B12", ".github/workflows/ci.yml is missing")
    if "pull_request" not in text:
        fail("B12", "ci.yml does not run on pull_request")
    if not re.search(r"(?m)^\s*-?\s*(?:- )?run: make ci\s*$", text):
        fail("B12", "ci.yml has no run: make ci step")


def check_b13(root):
    """.github/dependabot.yml has a github-actions entry."""
    text = read(root, ".github/dependabot.yml")
    if text is None:
        fail("B13", ".github/dependabot.yml is missing")
    if not re.search(r"(?m)^\s*-?\s*package-ecosystem:\s*[\"']?github-actions[\"']?\s*$", text):
        fail("B13", "dependabot.yml has no github-actions entry")


def check_b14(root):
    """SECURITY.md names private vulnerability reporting and the versions that get fixes."""
    text = read(root, "SECURITY.md") or read(root, ".github/SECURITY.md")
    if text is None:
        fail("B14", "SECURITY.md is missing")
    if "https://github.com/frostyard/flatpak-index/security" not in text:
        fail("B14", "SECURITY.md does not link https://github.com/frostyard/flatpak-index/security")
    if "private vulnerability reporting" not in text.lower():
        fail("B14", "SECURITY.md does not name private vulnerability reporting")
    if "fixes" not in text.lower():
        fail("B14", "SECURITY.md does not say which versions get fixes")


def check_b15(root):
    """No CODEOWNERS at the root, in .github/ or in docs/."""
    for name in ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS"):
        if os.path.lexists(Path(root) / name):
            fail("B15", f"{name} exists")


def check_b16(root):
    """No leftover agent-framework trees, and .claude/ holds only its 3 names."""
    for name in FRAMEWORK_TREES:
        if os.path.lexists(Path(root) / name):
            fail("B16", f"{name} exists")
    claude = Path(root) / ".claude"
    if claude.is_dir():
        for child in sorted(claude.iterdir()):
            if child.name not in ("settings.json", "skills", "session-summary.md"):
                fail("B16", f".claude/{child.name} is not allowed")


def check_b17(root):
    """README.md is the README spec's kind packaging, at most 1000 words."""
    text = read(root, "README.md")
    if text is None:
        fail("B17", "README.md is missing")
    markers = [line.strip() for line in text.splitlines() if line.strip().startswith("<!-- readme-kind:")]
    if markers != [MARKER]:
        fail("B17", f"the kind marker is {markers}, not 1 line {MARKER}")
    words = len(text.split())
    if words > 1000:
        fail("B17", f"README.md has {words} words (at most 1000)")


CHECKS = {
    "B1": check_b1,
    "B2": check_b2,
    "B3": check_b3,
    "B4": check_b4,
    "B5": check_b5,
    "B6": check_b6,
    "B7": check_b7,
    "B12": check_b12,
    "B13": check_b13,
    "B14": check_b14,
    "B15": check_b15,
    "B16": check_b16,
    "B17": check_b17,
}

IGNORED = (".git", "__pycache__", "_site", "_staged", "_built", "_deployed")


def copy_tree():
    """A copy of this repository outside it, with a cleanup, for a negative test."""
    folder = Path(tempfile.mkdtemp(prefix="flatpak-index-baseline-"))
    root = ROOT.resolve()
    if folder.resolve() == root or root in folder.resolve().parents:
        shutil.rmtree(folder, ignore_errors=True)
        raise RuntimeError(f"refusing to copy the tree to {folder}, which is inside {root}")
    shutil.copytree(ROOT, folder, symlinks=True, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(*IGNORED))
    return folder


class BaselineTest(unittest.TestCase):
    def test_every_row_passes(self):
        for row, check in CHECKS.items():
            with self.subTest(row=row):
                try:
                    check(ROOT)
                except BaselineError as error:
                    self.fail(str(error))


class NegativeTest(unittest.TestCase):
    """Break each row in a copy of the tree; the row's check must fail and name it."""

    def setUp(self):
        self.root = copy_tree()
        self.addCleanup(shutil.rmtree, self.root, True)

    def assert_broken(self, row):
        with self.assertRaises(BaselineError) as caught:
            CHECKS[row](self.root)
        self.assertTrue(str(caught.exception).startswith(f"{row}:"), str(caught.exception))

    def break_file(self, name, old, new):
        path = self.root / name
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text, f"{name} does not hold {old}")
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def test_b1_a_regular_alias_file(self):
        path = self.root / "CLAUDE.md"
        path.unlink()
        path.write_text("not a symlink\n", encoding="utf-8")
        self.assert_broken("B1")

    def test_b1_an_alias_to_another_target(self):
        path = self.root / ".cursorrules"
        path.unlink()
        os.symlink("README.md", path)
        self.assert_broken("B1")

    def test_b2_a_line_with_a_missing_field(self):
        path = self.root / ".memory" / "corrections.jsonl"
        path.write_text(path.read_text(encoding="utf-8")
                        + '{"date": "2026-11-06", "scope": "x", "correction": "y"}\n', encoding="utf-8")
        self.assert_broken("B2")

    def test_b3_a_missing_template(self):
        (self.root / "docs" / "specs" / "TEMPLATE.md").unlink()
        self.assert_broken("B3")

    def test_b3_an_index_without_the_org_adrs(self):
        self.break_file("docs/README.md", "](org-adrs.md)", "](org-adrs.md.bak)")
        shutil.copy(self.root / "docs" / "org-adrs.md", self.root / "docs" / "org-adrs.md.bak")
        self.assert_broken("B3")

    def test_b4_ci_that_does_not_run_verify(self):
        self.break_file("Makefile", "ci: verify", "ci: help")
        self.assert_broken("B4")

    def test_b4_verify_named_only_in_the_comment(self):
        self.break_file("Makefile", "ci: verify ## CI gate", "ci: help ## runs verify")
        self.assert_broken("B4")

    def test_b5_a_checkout_that_keeps_credentials(self):
        self.break_file(".github/workflows/ci.yml",
                        "          persist-credentials: false\n", "")
        self.assert_broken("B5")

    def test_b5_write_permission_at_the_top_level(self):
        self.break_file(".github/workflows/ci.yml", "permissions: {}", "permissions:\n  contents: write")
        self.assert_broken("B5")

    def test_b6_a_glob_that_misses_apps_json(self):
        self.break_file(".github/risk-paths.json", '"glob": "apps.json"', '"glob": "apps/*.json"')
        self.assert_broken("B6")

    def test_b6_a_why_without_a_category(self):
        self.break_file(".github/risk-paths.json", '"why": "governance: the verify gate"',
                        '"why": "the verify gate"')
        self.assert_broken("B6")

    def test_b7_a_template_without_a_risk_tier(self):
        self.break_file(".github/pull_request_template.md", "## Risk tier", "## Risks")
        self.assert_broken("B7")

    def test_b12_ci_without_make_ci(self):
        self.break_file(".github/workflows/ci.yml", "- run: make ci", "- run: python3 -m unittest discover -s tests")
        self.assert_broken("B12")

    def test_b13_a_dependabot_without_the_actions_entry(self):
        self.break_file(".github/dependabot.yml", "package-ecosystem: github-actions", "package-ecosystem: gomod")
        self.assert_broken("B13")

    def test_b14_a_security_policy_without_the_link(self):
        self.break_file("SECURITY.md", "https://github.com/frostyard/flatpak-index/security",
                        "https://example.org/security")
        self.assert_broken("B14")

    def test_b15_a_codeowners_file(self):
        (self.root / "CODEOWNERS").write_text("* @frostyard/owners\n", encoding="utf-8")
        self.assert_broken("B15")

    def test_b16_a_framework_tree(self):
        (self.root / "docs" / "superpowers").mkdir()
        self.assert_broken("B16")

    def test_b16_a_name_in_claude(self):
        (self.root / ".claude").mkdir()
        (self.root / ".claude" / "settings.local.json").write_text("{}\n", encoding="utf-8")
        self.assert_broken("B16")

    def test_b17_a_readme_over_the_word_limit(self):
        path = self.root / "README.md"
        path.write_text(path.read_text(encoding="utf-8") + "\nword " * 1001, encoding="utf-8")
        self.assert_broken("B17")

    def test_b17_a_readme_of_another_kind(self):
        self.break_file("README.md", MARKER, "<!-- readme-kind: tool -->")
        self.assert_broken("B17")


class StepTest(unittest.TestCase):
    """The checkout scan finds a step by indentation, not by its first key."""

    HEAD = (
        "name: CI\n"
        "on: [pull_request]\n"
        "permissions: {}\n"
        "jobs:\n"
        "  test:\n"
        "    permissions:\n"
        "      contents: read\n"
        "    steps:\n"
    )

    def write(self, steps):
        folder = Path(tempfile.mkdtemp(prefix="flatpak-index-baseline-steps-"))
        self.addCleanup(shutil.rmtree, folder, True)
        (folder / ".github" / "workflows").mkdir(parents=True)
        (folder / ".github" / "workflows" / "ci.yml").write_text(self.HEAD + steps, encoding="utf-8")
        return folder

    def test_a_step_that_starts_with_the_name(self):
        root = self.write("      - name: Checkout\n"
                          "        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
                          "        with:\n"
                          "          persist-credentials: false\n")
        check_b5(root)

    def test_a_step_that_starts_with_a_bare_dash(self):
        root = self.write("      -\n"
                          "        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
                          "        with:\n"
                          "          persist-credentials: false\n")
        check_b5(root)

    def test_a_step_that_starts_with_the_name_and_keeps_credentials(self):
        root = self.write("      - name: Checkout\n"
                          "        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
                          "\n"
                          "      - name: Test\n"
                          "        run: make ci\n")
        with self.assertRaises(BaselineError) as caught:
            check_b5(root)
        self.assertIn("persist-credentials", str(caught.exception))

    def test_a_write_permission_below_the_top_level_is_allowed(self):
        root = self.write("      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
                          "        with:\n"
                          "          persist-credentials: false\n")
        path = root / ".github" / "workflows" / "ci.yml"
        path.write_text(path.read_text().replace("      contents: read\n", "      contents: write\n"))
        check_b5(root)


class CopyTest(unittest.TestCase):
    def test_the_copy_is_outside_the_repository(self):
        folder = copy_tree()
        self.addCleanup(shutil.rmtree, folder, True)
        root = ROOT.resolve()
        self.assertNotEqual(folder.resolve(), root)
        self.assertNotIn(root, folder.resolve().parents)
        self.assertTrue((folder / "AGENTS.md").is_file())
        self.assertTrue((folder / "CLAUDE.md").is_symlink())
        self.assertFalse((folder / ".git").exists())


if __name__ == "__main__":
    unittest.main()
