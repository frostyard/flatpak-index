#!/usr/bin/env python3
"""Check the live GitHub Pages remote after a deploy.

GitHub Pages caches a site for 10 minutes, so a new deploy can take that long
to show, a deploy can publish a site that no client can read, and a broken
frostyard.flatpakrepo leaves every user without the remote. Nothing else sees
those failures: the deploy job is green, and the next publish can be hours
away. So this script reads the live site back. It fetches
frostyard.flatpakrepo and index/static and compares their sha256 digests with
the built files, then adds the live remote in a temporary folder with
`flatpak remote-add --user` and checks that `flatpak remote-ls --user --all` lists
every built ref with its digest. Both checks retry until 1 deadline passes, and
every operation gets only the time that is left of that deadline.

A failing check does not undo the deploy. It only makes the run that calls
this script red.

Usage: check-live.py --site DIR --url URL [--timeout SECONDS] [--interval SECONDS]
"""

import argparse
import hashlib
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REMOTE = "frostyard"
FILES = ("frostyard.flatpakrepo", "index/static")
# flatpak reads FLATPAK_USER_DIR before $XDG_DATA_HOME/flatpak, so it needs a
# subfolder of the temporary folder too: a value inherited from the caller
# would make remote-add change the real user installation
PRIVATE = {
    "XDG_DATA_HOME": "data",
    "XDG_CONFIG_HOME": "config",
    "XDG_CACHE_HOME": "cache",
    "FLATPAK_USER_DIR": "flatpak-user",
}
COMMIT = 12
HTTP_TIMEOUT = 60
RUN_TIMEOUT = 300


class Missing(Exception):
    """The flatpak command is not installed, so no attempt can pass."""


def fetch_once(url, timeout):
    """Return the body of a GET; timeout limits each socket operation only."""
    request = urllib.request.Request(url, headers={
        "User-Agent": "frostyard-flatpak-index",
        "Cache-Control": "no-cache",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch(url, timeout=HTTP_TIMEOUT):
    """Return the body of a GET; each URL carries a token, so no cache answers it twice.

    timeout limits the whole fetch. A socket timeout limits only 1 socket
    operation, and a server that sends 1 byte at a time can keep a read going
    for hours, so the fetch runs in a daemon thread. When the thread is not
    done after timeout seconds, the fetch is a TimeoutError; the thread is left
    behind and ends with its socket timeout or with the process.
    """
    outcome = {}

    def work():
        try:
            outcome["body"] = fetch_once(url, timeout)
        except BaseException as e:  # handed to the caller below
            outcome["error"] = e

    thread = threading.Thread(target=work, name="fetch", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f"the fetch did not finish within {timeout:g} seconds")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["body"]


def run(command, env, timeout=RUN_TIMEOUT):
    """Run a command with a copy of the environment, without a shell."""
    try:
        return subprocess.run(command, env=env, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        raise Missing(f"the {command[0]} command was not found; install flatpak to run this check") from e


def sleep(seconds):
    """Wait between 2 attempts."""
    time.sleep(seconds)


def monotonic():
    """The clock that the deadline is measured with."""
    return time.monotonic()


def tail(text):
    """The last 3 lines of a command's output, on 1 line."""
    return " | ".join((text or "").strip().splitlines()[-3:])


def sha256(body):
    """The hex sha256 digest of a body."""
    return hashlib.sha256(body).hexdigest()


def index_pairs(body):
    """The (ref, digest) pairs of an index/static document; the digest has no sha256: prefix.

    Raise ValueError with a short reason when the document is not the index that
    build-index.py writes: a body from a broken deploy must not crash a check.
    """
    try:
        document = json.loads(body)
    except ValueError as e:
        raise ValueError(f"it is not JSON: {e}") from e
    if not isinstance(document, dict):
        raise ValueError("the document is not a JSON object")
    results = document.get("Results")
    if not isinstance(results, list):
        raise ValueError('it has no "Results" list')
    pairs = set()
    for n, result in enumerate(results):
        if not isinstance(result, dict):
            raise ValueError(f"Results[{n}] is not an object")
        images = result.get("Images")
        if not isinstance(images, list):
            raise ValueError(f'Results[{n}] has no "Images" list')
        for m, image in enumerate(images):
            where = f"Results[{n}].Images[{m}]"
            if not isinstance(image, dict):
                raise ValueError(f"{where} is not an object")
            digest = image.get("Digest")
            if not isinstance(digest, str) or not digest:
                raise ValueError(f'{where} has no "Digest" string')
            labels = image.get("Labels")
            if not isinstance(labels, dict):
                raise ValueError(f'{where} has no "Labels" object')
            ref = labels.get("org.flatpak.ref")
            if not isinstance(ref, str) or not ref:
                raise ValueError(f'{where} has no "org.flatpak.ref" string')
            pairs.add((ref, digest.removeprefix("sha256:")))
    return pairs


def describe_index(live, built):
    """The images that only 1 of the 2 index documents lists."""
    try:
        live_pairs = index_pairs(live)
    except ValueError as e:
        return [f"index/static: the live body is not an index: {e}"]
    built_pairs = index_pairs(built)
    lines = [f"index/static: only in the built index: {ref} {digest}"
             for ref, digest in sorted(built_pairs - live_pairs)]
    lines += [f"index/static: only in the live index: {ref} {digest}"
              for ref, digest in sorted(live_pairs - built_pairs)]
    return lines


def check_files(site, url, attempt, deadline):
    """Compare the live files with the built ones; return the lines that describe each difference."""
    lines = []
    for rel in FILES:
        left = deadline - monotonic()
        if left <= 0:
            lines.append(f"{rel}: no time is left to fetch it")
            break
        built = (site / rel).read_bytes()
        # the token changes on each attempt, so a stale CDN copy cannot answer
        try:
            live = fetch(f"{url}{rel}?check={attempt}-{time.time_ns()}", min(HTTP_TIMEOUT, left))
        except (OSError, ValueError, http.client.HTTPException) as e:
            lines.append(f"{rel}: cannot fetch it: {e}")
            continue
        if sha256(live) == sha256(built):
            continue
        lines.append(f"{rel}: built sha256:{sha256(built)}, live sha256:{sha256(live)}")
        if rel == "index/static":
            lines.extend(describe_index(live, built))
    return lines


def match_listing(lines, expected):
    """The pairs that no listed line matches, and the lines that match 0 or 2+ pairs."""
    matched = set()
    extra = []
    for line in lines:
        ref, _, commit = line.partition("\t")
        hits = [pair for pair in expected
                if pair[0] == ref and len(commit) >= COMMIT and pair[1].startswith(commit)]
        if len(hits) == 1:
            matched.update(hits)
        else:
            extra.append(line)
    return expected - matched, extra


def check_client(site, url, deadline):
    """Add the live remote in a temporary folder and compare its listing with the built index."""
    expected = index_pairs((site / "index" / "static").read_bytes())
    folder = tempfile.mkdtemp(prefix="check-live-")
    listing = []
    try:
        env = dict(os.environ)
        for name, sub in PRIVATE.items():
            env[name] = os.path.join(folder, sub)
            os.mkdir(env[name])
        try:
            left = deadline - monotonic()
            if left <= 0:
                return ["no time is left to add the remote"]
            timeout = min(RUN_TIMEOUT, left)
            add = run(["flatpak", "remote-add", "--user", "--if-not-exists", REMOTE,
                       f"{url}frostyard.flatpakrepo"], env, timeout)
            if add.returncode != 0:
                return [f"flatpak remote-add exited with {add.returncode}: {tail(add.stderr)}"]
            left = deadline - monotonic()
            if left <= 0:
                return ["no time is left to list the remote"]
            timeout = min(RUN_TIMEOUT, left)
            # --all, because remote-ls hides end-of-life refs without it
            listed = run(["flatpak", "remote-ls", "--user", REMOTE, "--all", "--arch=*",
                          "--columns=ref,commit"], env, timeout)
        except subprocess.TimeoutExpired:
            return [f"flatpak did not finish within {timeout:g} seconds"]
        if listed.returncode != 0:
            return [f"flatpak remote-ls exited with {listed.returncode}: {tail(listed.stderr)}"]
        listing = [line for line in listed.stdout.splitlines() if line.strip()]
    finally:
        shutil.rmtree(folder, ignore_errors=True)

    missing, extra = match_listing(listing, expected)
    lines = [f"flatpak remote-ls lists no {ref} {digest}" for ref, digest in sorted(missing)]
    lines += [f"flatpak remote-ls lists an extra line: {line}" for line in extra]
    if not lines and len(listing) != len(expected):
        lines.append(f"flatpak remote-ls lists {len(listing)} line(s), "
                     f"but the built index has {len(expected)} image(s)")
    return lines


def retry(attempt, deadline, interval):
    """Call attempt(n) until it returns no lines or the deadline passes; return the last lines.

    A new attempt starts only while time is left. The deadline is a budget for
    retries, not a window in which a result is valid: an attempt that passes
    shows that the live site is right, even when it ends after the deadline.
    Each operation gets only the time that is left, so the run ends soon after
    the deadline.
    """
    number = 0
    lines = ["no time is left to start it"]
    while True:
        left = deadline - monotonic()
        if left <= 0:
            return lines
        number += 1
        lines = attempt(number)
        if not lines:
            return []
        for line in lines:
            print(f"attempt {number}: {line}", file=sys.stderr)
        left = deadline - monotonic()
        if left > 0:
            sleep(min(interval, left))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--site", type=Path, required=True,
                        help="the built site, such as the extracted Pages artifact")
    parser.add_argument("--url", required=True, help="the live base URL")
    parser.add_argument("--timeout", type=float, default=900,
                        help="seconds for the whole run, for both checks (default: 900)")
    parser.add_argument("--interval", type=float, default=30,
                        help="seconds between 2 attempts (default: 30)")
    args = parser.parse_args(argv)

    url = args.url.rstrip("/") + "/"
    missing = [rel for rel in FILES if not (args.site / rel).is_file()]
    if missing:
        print(f"error: {args.site} has no {', '.join(missing)}; --site needs a built site", file=sys.stderr)
        sys.exit(1)

    try:
        pairs = index_pairs((args.site / "index" / "static").read_bytes())
    except (OSError, ValueError) as e:
        print(f"error: the built index at {args.site} is not an index: {e}", file=sys.stderr)
        sys.exit(1)

    deadline = monotonic() + args.timeout
    checks = (
        ("files", lambda number: check_files(args.site, url, number, deadline)),
        ("client", lambda number: check_client(args.site, url, deadline)),
    )
    try:
        for name, attempt in checks:
            lines = retry(attempt, deadline, args.interval)
            if lines:
                print(f"error: the {name} check did not pass within {args.timeout:g} seconds: "
                      f"{lines[-1]}", file=sys.stderr)
                sys.exit(1)
    except Missing as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"The live remote at {url} serves the built index: {len(pairs)} image(s), "
          f"and flatpak remote-ls lists them.")


if __name__ == "__main__":
    main()
