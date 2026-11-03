"""Tests for scripts/check-live.py with a fake live site and a fake flatpak."""

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_live", ROOT / "scripts" / "check-live.py")
check_live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_live)

URL = "https://frostyard.github.io/flatpak-index/"
IGLOO = ("app/io.github.frostyard.Igloo/x86_64/master", hashlib.sha256(b"igloo").hexdigest())
IGLOO_ARM = ("app/io.github.frostyard.Igloo/aarch64/master", hashlib.sha256(b"igloo-arm").hexdigest())
NOTES = ("app/org.frostyard.Frostnotes/x86_64/master", hashlib.sha256(b"notes").hexdigest())
STALE = hashlib.sha256(b"stale").hexdigest()


def image(pair):
    return {"Digest": "sha256:" + pair[1], "Labels": {"org.flatpak.ref": pair[0]}}


def index_body(*pairs):
    """An index/static document that lists these images."""
    return json.dumps({"Registry": "https://ghcr.io",
                       "Results": [{"Name": "frostyard/apps",
                                    "Images": [image(pair) for pair in pairs]}]}, indent=2).encode() + b"\n"


def listing(*pairs, commit=None):
    """The output of flatpak remote-ls --columns=ref,commit."""
    return "".join(f"{ref}\t{(commit or digest)[:12]}\n" for ref, digest in pairs)


class FakeLive:
    """The live site: for each path, the bodies or errors that the next attempts get."""

    def __init__(self):
        self.answers = {}
        self.urls = []

    def serve(self, path, *items):
        self.answers[path] = list(items)

    def fetch(self, url):
        self.urls.append(url)
        path = url.removeprefix(URL).split("?", 1)[0]
        items = self.answers.get(path)
        if not items:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO())
        item = items.pop(0) if len(items) > 1 else items[0]
        if isinstance(item, Exception):
            raise item
        return item


class FakeFlatpak:
    """A fake flatpak: records each command and the environment that it got."""

    def __init__(self):
        self.stdout = ""
        self.stderr = ""
        self.exit_code = 0
        self.missing = False
        self.calls = []

    def run(self, command, env=None, **kwargs):
        self.calls.append((command, env, kwargs))
        if self.missing:
            raise FileNotFoundError(command[0])
        if command[1] == "remote-ls":
            return subprocess.CompletedProcess(command, self.exit_code, self.stdout, self.stderr)
        return subprocess.CompletedProcess(command, 0, "", "")

    def commands(self, verb):
        return [command for command, _, _ in self.calls if command[1] == verb]


class FakeClock:
    """A clock that moves only when the script sleeps."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class CheckLiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.site = Path(self.tmp.name) / "site"
        self.live = FakeLive()
        self.flatpak = FakeFlatpak()
        self.clock = FakeClock()
        for patcher in (mock.patch.object(check_live, "fetch", self.live.fetch),
                        mock.patch.object(check_live.subprocess, "run", self.flatpak.run),
                        mock.patch.object(check_live, "sleep", self.clock.sleep),
                        mock.patch.object(check_live, "monotonic", self.clock.monotonic)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.write_site(IGLOO)

    def write_site(self, *pairs, repo=b"[Flatpak Repo]\n"):
        (self.site / "index").mkdir(parents=True, exist_ok=True)
        (self.site / "frostyard.flatpakrepo").write_bytes(repo)
        (self.site / "index" / "static").write_bytes(index_body(*pairs))

    def built(self, rel):
        return (self.site / rel).read_bytes()

    def serve_built(self):
        self.live.serve("frostyard.flatpakrepo", self.built("frostyard.flatpakrepo"))
        self.live.serve("index/static", self.built("index/static"))

    def run_main(self, url=URL, timeout=60, interval=30):
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
            code = 0
            try:
                check_live.main(["--site", str(self.site), "--url", url,
                                 "--timeout", str(timeout), "--interval", str(interval)])
            except SystemExit as e:
                code = e.code
        return code, stdout.getvalue(), stderr.getvalue()

    def test_files_and_listing_match(self):
        self.serve_built()
        self.flatpak.stdout = listing(IGLOO)
        code, out, err = self.run_main()
        self.assertEqual(code, 0, err)
        self.assertEqual(out, f"The live remote at {URL} serves the built index: 1 image(s), "
                              f"and flatpak remote-ls lists them.\n")
        self.assertEqual(self.clock.sleeps, [])
        self.assertEqual(self.flatpak.commands("remote-add"), [[
            "flatpak", "remote-add", "--user", "--if-not-exists", "frostyard",
            URL + "frostyard.flatpakrepo"]])
        self.assertEqual(self.flatpak.commands("remote-ls"), [[
            "flatpak", "remote-ls", "--user", "frostyard", "--arch=*", "--columns=ref,commit"]])
        _, _, kwargs = self.flatpak.calls[0]
        self.assertNotIn("shell", kwargs)

    def test_stale_index_becomes_fresh(self):
        self.live.serve("frostyard.flatpakrepo", self.built("frostyard.flatpakrepo"))
        self.live.serve("index/static", index_body((IGLOO[0], STALE)), self.built("index/static"))
        self.flatpak.stdout = listing(IGLOO)
        code, out, err = self.run_main()
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.clock.sleeps), 1)
        urls = [url for url in self.live.urls if "index/static" in url]
        self.assertEqual(len(urls), 2)
        self.assertNotEqual(urls[0], urls[1], "each attempt needs a new token")

    def test_files_never_match(self):
        self.live.serve("frostyard.flatpakrepo", self.built("frostyard.flatpakrepo"))
        self.live.serve("index/static", index_body((IGLOO[0], STALE)))
        code, out, err = self.run_main(timeout=90)
        self.assertEqual(code, 1)
        self.assertIn("error: the files check did not pass within 90 seconds", err)
        self.assertEqual(self.clock.sleeps, [30, 30, 30])
        self.assertIn("index/static: built sha256:", err)
        self.assertEqual(self.flatpak.calls, [], "the client check must not run")

    def test_http_error_then_success(self):
        self.live.serve("frostyard.flatpakrepo",
                        urllib.error.HTTPError(URL + "frostyard.flatpakrepo", 503, "Service Unavailable",
                                               {}, io.BytesIO()),
                        self.built("frostyard.flatpakrepo"))
        self.live.serve("index/static", self.built("index/static"))
        self.flatpak.stdout = listing(IGLOO)
        code, out, err = self.run_main()
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.clock.sleeps), 1)
        self.assertIn("cannot fetch it", err)

    def test_index_differences_are_reported(self):
        self.live.serve("frostyard.flatpakrepo", self.built("frostyard.flatpakrepo"))
        self.live.serve("index/static", index_body((IGLOO[0], STALE), NOTES))
        code, out, err = self.run_main(timeout=60)
        self.assertEqual(code, 1)
        self.assertIn(f"only in the built index: {IGLOO[0]} {IGLOO[1]}", err)
        self.assertIn(f"only in the live index: {IGLOO[0]} {STALE}", err)
        self.assertIn(f"only in the live index: {NOTES[0]} {NOTES[1]}", err)

    def test_listing_with_a_missing_ref(self):
        self.write_site(IGLOO, NOTES)
        self.serve_built()
        self.flatpak.stdout = listing(IGLOO)
        code, out, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("error: the client check did not pass", err)
        self.assertIn(f"flatpak remote-ls lists no {NOTES[0]} {NOTES[1]}", err)

    def test_listing_with_an_extra_ref(self):
        self.serve_built()
        self.flatpak.stdout = listing(IGLOO, NOTES)
        code, out, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn(f"flatpak remote-ls lists an extra line: {NOTES[0]}", err)

    def test_listing_with_a_commit_that_is_no_prefix(self):
        self.serve_built()
        self.flatpak.stdout = listing((IGLOO[0], STALE))
        code, out, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn(f"flatpak remote-ls lists no {IGLOO[0]} {IGLOO[1]}", err)
        self.assertIn("lists an extra line", err)

    def test_a_short_commit_matches_no_pair(self):
        self.serve_built()
        self.flatpak.stdout = f"{IGLOO[0]}\t{IGLOO[1][:11]}\n"
        code, out, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("lists an extra line", err)

    def test_multi_arch_index(self):
        self.write_site(IGLOO, IGLOO_ARM)
        self.serve_built()
        self.flatpak.stdout = listing(IGLOO, IGLOO_ARM)
        code, out, err = self.run_main()
        self.assertEqual(code, 0, err)
        self.assertIn("2 image(s)", out)

    def test_empty_index_and_empty_listing(self):
        self.write_site()
        self.serve_built()
        self.flatpak.stdout = ""
        code, out, err = self.run_main()
        self.assertEqual(code, 0, err)
        self.assertIn("0 image(s)", out)

    def test_flatpak_not_found_fails_at_once(self):
        self.serve_built()
        self.flatpak.missing = True
        code, out, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("error: the flatpak command was not found", err)
        self.assertEqual(self.clock.sleeps, [])
        self.assertEqual(len(self.flatpak.calls), 1)

    def test_failed_flatpak_exit_is_retried(self):
        self.serve_built()
        self.flatpak.exit_code = 1
        self.flatpak.stderr = "a\nb\nc\nd\n"
        code, out, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("flatpak remote-ls exited with 1: b | c | d", err)
        self.assertTrue(self.clock.sleeps)

    def test_the_environment_points_into_a_temporary_folder(self):
        self.serve_built()
        self.flatpak.stdout = listing(IGLOO)
        exists = []
        run = self.flatpak.run

        def record(command, env=None, **kwargs):
            exists.append(os.path.isdir(env["XDG_DATA_HOME"]))
            return run(command, env=env, **kwargs)

        with mock.patch.object(check_live.subprocess, "run", record):
            code, out, err = self.run_main()
        self.assertEqual(code, 0, err)
        env = self.flatpak.calls[0][1]
        home = str(Path.home())
        for name in check_live.XDG:
            with self.subTest(name=name):
                path = env[name]
                self.assertTrue(path.startswith(tempfile.gettempdir()), path)
                self.assertFalse(path.startswith(home + os.sep), path)
                self.assertNotEqual(path, os.environ.get(name))
                self.assertFalse(os.path.exists(os.path.dirname(path)), "the folder must be gone")
        self.assertEqual(exists, [True, True])

    def test_the_trailing_slash_of_the_url_does_not_matter(self):
        self.serve_built()
        self.flatpak.stdout = listing(IGLOO)
        code, _, err = self.run_main(url=URL.rstrip("/"))
        self.assertEqual(code, 0, err)
        without = [url.split("?", 1)[0] for url in self.live.urls]
        self.live.urls.clear()
        code, _, err = self.run_main(url=URL)
        self.assertEqual(code, 0, err)
        self.assertEqual([url.split("?", 1)[0] for url in self.live.urls], without)
        self.assertEqual(without, [URL + rel for rel in check_live.FILES])

    def test_a_site_without_the_built_files_fails_at_once(self):
        self.site = Path(self.tmp.name) / "empty"
        self.site.mkdir()
        code, out, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("error:", err)
        self.assertEqual(self.clock.sleeps, [])
        self.assertEqual(self.live.urls, [])


if __name__ == "__main__":
    unittest.main()
