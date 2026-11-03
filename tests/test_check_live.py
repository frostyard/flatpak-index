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
CANARY = "/tmp/ghp-check/canary"


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


class FakeClock:
    """A clock that moves only when the script sleeps or a fake operation runs."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def spend(self, seconds):
        """The time that a fake fetch or flatpak command takes."""
        self.now += seconds


class FakeLive:
    """The live site: for each path, the bodies or errors that the next attempts get."""

    def __init__(self, clock):
        self.clock = clock
        self.answers = {}
        self.urls = []
        self.timeouts = []
        self.cost = 0.0  # the seconds that each fetch takes

    def serve(self, path, *items):
        self.answers[path] = list(items)

    def fetch(self, url, timeout=check_live.HTTP_TIMEOUT):
        self.urls.append(url)
        self.timeouts.append(timeout)
        self.clock.spend(self.cost)
        path = url.removeprefix(URL).split("?", 1)[0]
        items = self.answers.get(path)
        if not items:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO())
        item = items.pop(0) if len(items) > 1 else items[0]
        if isinstance(item, Exception):
            raise item
        return item


class FakeFlatpak:
    """A fake flatpak: records each command, its timeout and the environment that it got."""

    def __init__(self, clock):
        self.clock = clock
        self.stdout = ""
        self.stderr = ""
        self.exit_code = 0
        self.missing = False
        self.cost = 0.0  # the seconds that each command takes
        self.calls = []
        self.timeouts = []

    def run(self, command, env=None, timeout=None, **kwargs):
        self.calls.append((command, env, kwargs))
        self.timeouts.append(timeout)
        self.clock.spend(self.cost)
        if self.missing:
            raise FileNotFoundError(command[0])
        if command[1] == "remote-ls":
            return subprocess.CompletedProcess(command, self.exit_code, self.stdout, self.stderr)
        return subprocess.CompletedProcess(command, 0, "", "")

    def commands(self, verb):
        return [command for command, _, _ in self.calls if command[1] == verb]


class CheckLiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.site = Path(self.tmp.name) / "site"
        self.clock = FakeClock()
        self.live = FakeLive(self.clock)
        self.flatpak = FakeFlatpak(self.clock)
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

        def record(command, env=None, timeout=None, **kwargs):
            exists.append(os.path.isdir(env["XDG_DATA_HOME"]))
            return run(command, env=env, timeout=timeout, **kwargs)

        with mock.patch.object(check_live.subprocess, "run", record):
            code, out, err = self.run_main()
        self.assertEqual(code, 0, err)
        env = self.flatpak.calls[0][1]
        home = str(Path.home())
        for name in check_live.PRIVATE:
            with self.subTest(name=name):
                path = env[name]
                self.assertTrue(path.startswith(tempfile.gettempdir() + os.sep), path)
                self.assertFalse(path.startswith(home + os.sep), path)
                self.assertNotEqual(path, os.environ.get(name))
                self.assertFalse(os.path.exists(os.path.dirname(path)), "the folder must be gone")
        self.assertEqual(exists, [True, True])

    def test_an_inherited_flatpak_user_dir_is_replaced(self):
        self.serve_built()
        self.flatpak.stdout = listing(IGLOO)
        with mock.patch.dict(os.environ, {"FLATPAK_USER_DIR": CANARY}):
            code, out, err = self.run_main()
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.flatpak.calls), 2, "both commands need the variable")
        for command, env, _ in self.flatpak.calls:
            with self.subTest(command=command[1]):
                user_dir = env["FLATPAK_USER_DIR"]
                self.assertNotEqual(user_dir, CANARY)
                self.assertTrue(user_dir.startswith(tempfile.gettempdir() + os.sep), user_dir)
                self.assertEqual(os.path.dirname(user_dir), os.path.dirname(env["XDG_DATA_HOME"]))
                self.assertFalse(os.path.exists(user_dir), "the folder must be gone")
        first, second = (env["FLATPAK_USER_DIR"] for _, env, _ in self.flatpak.calls)
        self.assertEqual(first, second, "1 attempt uses 1 folder for both commands")

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

    def test_an_attempt_does_not_start_after_the_deadline(self):
        self.live.serve("frostyard.flatpakrepo", self.built("frostyard.flatpakrepo"))
        self.live.serve("index/static", index_body((IGLOO[0], STALE)))
        self.live.cost = 50  # each fetch takes 50 of the 90 seconds
        code, out, err = self.run_main(timeout=90)
        self.assertEqual(code, 1)
        self.assertIn("error: the files check did not pass within 90 seconds", err)
        self.assertEqual(len(self.live.urls), 2, "only the first attempt ran")
        self.assertEqual(self.clock.sleeps, [], "no sleep, because no time was left")
        self.assertGreater(self.clock.now, 90)

    def test_the_client_check_does_not_start_without_time(self):
        self.serve_built()  # the files match, but they use all the time
        self.live.cost = 60
        code, out, err = self.run_main(timeout=100)
        self.assertEqual(code, 1)
        self.assertIn("error: the client check did not pass", err)
        self.assertIn("no time is left to start it", err)
        self.assertEqual(self.flatpak.calls, [], "no flatpak command may run")

    def test_fetch_and_run_get_at_most_the_time_that_is_left(self):
        self.serve_built()
        self.flatpak.exit_code = 1  # so the client check retries
        code, out, err = self.run_main(timeout=45)
        self.assertEqual(code, 1)
        self.assertTrue(self.live.timeouts)
        self.assertTrue(self.flatpak.timeouts)
        for timeout in self.live.timeouts + self.flatpak.timeouts:
            with self.subTest(timeout=timeout):
                self.assertGreater(timeout, 0)
                self.assertLessEqual(timeout, 45)
        self.assertEqual(self.live.timeouts[0], 45, "min(60, 45)")
        self.assertEqual(self.flatpak.timeouts[0], 45, "min(300, 45)")

        # with more time left, the 60 and 300 second caps still apply
        self.live.timeouts.clear()
        self.flatpak.timeouts.clear()
        code, out, err = self.run_main(timeout=600)
        self.assertEqual(code, 1)
        self.assertEqual(self.live.timeouts[0], check_live.HTTP_TIMEOUT)
        self.assertEqual(self.flatpak.timeouts[0], min(check_live.RUN_TIMEOUT, 600))

    def test_an_attempt_that_passes_after_the_deadline_passes(self):
        self.serve_built()
        self.flatpak.stdout = listing(IGLOO)
        self.flatpak.cost = 60  # the 2 commands end after the 100 seconds
        code, out, err = self.run_main(timeout=100)
        self.assertEqual(code, 0, err)
        self.assertIn("1 image(s)", out)
        self.assertGreater(self.clock.now, 100)

    def test_an_invalid_live_index_is_a_failed_attempt(self):
        self.live.serve("frostyard.flatpakrepo", self.built("frostyard.flatpakrepo"))
        bodies = (b"[]",
                  b"null",
                  b'{"Results": 5}',
                  b'{"Results": [{"Images": [{"Digest": 5}]}]}',
                  b'{"Results": [{"Images": [{"Digest": "sha256:0", "Labels": 5}]}]}',
                  b'{"Results": [{"Images": [{"Digest": "sha256:0", "Labels": {"org.flatpak.ref": 5}}]}]}',
                  b"not json")
        for body in bodies:
            with self.subTest(body=body):
                self.live.serve("index/static", body)
                self.flatpak.calls.clear()
                self.clock.sleeps.clear()
                code, out, err = self.run_main(timeout=30)
                self.assertEqual(code, 1)
                self.assertIn("index/static: the live body is not an index:", err)
                self.assertNotIn("Traceback", err)
                self.assertEqual(self.flatpak.calls, [])
                with self.assertRaises(ValueError):
                    check_live.index_pairs(body)

    def test_an_invalid_built_index_fails_before_any_fetch(self):
        (self.site / "index" / "static").write_bytes(b"null")
        code, out, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("error: the built index", err)
        self.assertIn("not an index", err)
        self.assertEqual(self.live.urls, [])
        self.assertEqual(self.flatpak.calls, [])

    def test_index_pairs_reads_a_built_index(self):
        self.assertEqual(check_live.index_pairs(index_body(IGLOO, IGLOO_ARM)), {IGLOO, IGLOO_ARM})


class FetchTest(unittest.TestCase):
    """The real fetch, with a fake urlopen and a fake clock."""

    def slow_response(self, chunks, seconds):
        clock = {"now": 0.0}
        parts = list(chunks)

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size):
                clock["now"] += seconds
                return parts.pop(0) if parts else b""

        return clock, Response()

    def test_a_body_that_arrives_in_time_is_returned(self):
        clock, response = self.slow_response([b"ab", b"cd"], 1)
        with mock.patch.object(check_live, "monotonic", lambda: clock["now"]), \
                mock.patch.object(check_live.urllib.request, "urlopen", return_value=response) as urlopen:
            self.assertEqual(check_live.fetch("https://example.invalid/x", 10), b"abcd")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 10)

    def test_a_body_that_keeps_arriving_past_the_timeout_fails(self):
        clock, response = self.slow_response([b"x"] * 100, 5)
        with mock.patch.object(check_live, "monotonic", lambda: clock["now"]), \
                mock.patch.object(check_live.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(TimeoutError):
                check_live.fetch("https://example.invalid/x", 12)
        # 1 read can start before the limit and end after it, but no more
        self.assertLessEqual(clock["now"], 12 + 5)


if __name__ == "__main__":
    unittest.main()
