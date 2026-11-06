"""Tests for the GitHub Actions workflows."""

import re
import tempfile
import unittest
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)(.*)$", re.M)
PINNED = re.compile(r"^[\w.-]+/[\w.-]+(/[\w./-]+)?@[0-9a-f]{40}$")
VERSION = re.compile(r"\s+# v\d[\w.-]*\s*")
CORE_MAIN = re.compile(r"^\s+# frostyard/core main, \d{4}-\d{2}-\d{2}\b")
JOB = re.compile(r"^  ([\w-]+):\s*$", re.M)


def job_blocks(text):
    """The text of each job of a workflow, by job name; PyYAML is not available here."""
    _, jobs = text.split("\njobs:\n", 1)
    starts = list(JOB.finditer(jobs))
    return {match.group(1): jobs[match.end():starts[n + 1].start() if n + 1 < len(starts) else len(jobs)]
            for n, match in enumerate(starts)}


def workflow_files(folder):
    """Every workflow file; GitHub runs both .yml and .yaml."""
    return sorted([*folder.glob("*.yml"), *folder.glob("*.yaml")])


class WorkflowsTest(unittest.TestCase):
    def test_yaml_workflows_are_read_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "a.yml").write_text("")
            (folder / "b.yaml").write_text("")
            self.assertEqual([p.name for p in workflow_files(folder)], ["a.yml", "b.yaml"])

    def test_actions_are_pinned_to_commits(self):
        files = workflow_files(WORKFLOWS)
        self.assertTrue(files)
        count = 0
        for path in files:
            for target, rest in USES.findall(path.read_text()):
                if target.startswith("./"):
                    continue
                count += 1
                self.assertRegex(target, PINNED, f"{path.name}: {target} is not pinned to a commit")
                if target.startswith("frostyard/core/"):
                    # core has no release tags: its reusable workflows name the day of main they pin
                    self.assertRegex(rest, CORE_MAIN, f"{path.name}: {target} needs a '# frostyard/core main, YYYY-MM-DD' comment")
                else:
                    self.assertRegex(rest, VERSION, f"{path.name}: {target} needs a '# vX.Y.Z' comment")
        self.assertGreater(count, 0)

    def test_publish_verifies_the_live_site(self):
        jobs = job_blocks((WORKFLOWS / "publish.yml").read_text())
        self.assertIn("verify", jobs)
        self.assertRegex(jobs["deploy"], r"(?m)^    outputs:\n      page_url: \$\{\{ steps\.deployment\.outputs\.page_url \}\}$")
        verify = jobs["verify"]
        self.assertRegex(verify, r"(?m)^    needs: deploy$")
        self.assertRegex(verify, r"(?m)^        run: python3 scripts/check-live\.py --site \S+ --url \"\$PAGE_URL\"$")
        self.assertIn("${{ needs.deploy.outputs.page_url }}", verify)
        # an expression in the run text would be pasted in, not passed as a value
        self.assertNotIn("${{ needs.deploy", verify.split("Check the live site", 1)[1].split("run:", 1)[1])


if __name__ == "__main__":
    unittest.main()
