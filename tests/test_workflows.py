"""Tests for the GitHub Actions workflows."""

import re
import unittest
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)(.*)$", re.M)
PINNED = re.compile(r"^[\w.-]+/[\w.-]+(/[\w./-]+)?@[0-9a-f]{40}$")
VERSION = re.compile(r"\s+# v\d[\w.-]*\s*")


class WorkflowsTest(unittest.TestCase):
    def test_actions_are_pinned_to_commits(self):
        files = sorted(WORKFLOWS.glob("*.yml"))
        self.assertTrue(files)
        count = 0
        for path in files:
            for target, rest in USES.findall(path.read_text()):
                if target.startswith("./"):
                    continue
                count += 1
                self.assertRegex(target, PINNED, f"{path.name}: {target} is not pinned to a commit")
                self.assertRegex(rest, VERSION, f"{path.name}: {target} needs a '# vX.Y.Z' comment")
        self.assertGreater(count, 0)


if __name__ == "__main__":
    unittest.main()
