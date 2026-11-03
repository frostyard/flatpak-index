#!/usr/bin/env python3
"""Check the live GitHub Pages remote after a deploy.

GitHub Pages caches a site for 10 minutes, so a new deploy can take that long
to show, a deploy can publish a site that no client can read, and a broken
frostyard.flatpakrepo leaves every user without the remote. Nothing else sees
those failures: the deploy job is green, and the next publish can be hours
away. So this script reads the live site back. It fetches
frostyard.flatpakrepo and index/static and compares their sha256 digests with
the built files, then adds the live remote in a temporary folder with
`flatpak remote-add --user` and checks that `flatpak remote-ls --user` lists
every built ref with its digest. Both checks retry until 1 deadline passes.

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
import time
import urllib.error
import urllib.request
from pathlib import Path

REMOTE = "frostyard"
FILES = ("frostyard.flatpakrepo", "index/static")
XDG = ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME")
COMMIT = 12
RUN_TIMEOUT = 300


class Missing(Exception):
    """The flatpak command is not installed, so no attempt can pass."""


def fetch(url):
    """Return the body of a GET; each URL carries a token, so no cache answers it twice."""
    request = urllib.request.Request(url, headers={
        "User-Agent": "frostyard-flatpak-index",
        "Cache-Control": "no-cache",
    })
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def run(command, env):
    """Run a command with a copy of the environment, without a shell."""
    try:
        return subprocess.run(command, env=env, capture_output=True, text=True, timeout=RUN_TIMEOUT)
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
    """The (ref, digest) pairs of an index/static document; the digest has no sha256: prefix."""
    pairs = set()
    for result in json.loads(body).get("Results") or []:
        for image in result.get("Images") or []:
            pairs.add((image["Labels"]["org.flatpak.ref"], image["Digest"].removeprefix("sha256:")))
    return pairs


def describe_index(live, built):
    """The images that only 1 of the 2 index documents lists."""
    try:
        live_pairs = index_pairs(live)
    except (ValueError, KeyError, TypeError):
        return ["index/static: the live body is not an index"]
    built_pairs = index_pairs(built)
    lines = [f"index/static: only in the built index: {ref} {digest}"
             for ref, digest in sorted(built_pairs - live_pairs)]
    lines += [f"index/static: only in the live index: {ref} {digest}"
              for ref, digest in sorted(live_pairs - built_pairs)]
    return lines


def check_files(site, url, attempt):
    """Compare the live files with the built ones; return the lines that describe each difference."""
    lines = []
    for rel in FILES:
        built = (site / rel).read_bytes()
        # the token changes on each attempt, so a stale CDN copy cannot answer
        try:
            live = fetch(f"{url}{rel}?check={attempt}-{time.time_ns()}")
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


def check_client(site, url):
    """Add the live remote in a temporary folder and compare its listing with the built index."""
    expected = index_pairs((site / "index" / "static").read_bytes())
    folder = tempfile.mkdtemp(prefix="check-live-")
    listing = []
    try:
        env = dict(os.environ)
        for name in XDG:
            env[name] = os.path.join(folder, name.lower())
            os.mkdir(env[name])
        try:
            add = run(["flatpak", "remote-add", "--user", "--if-not-exists", REMOTE,
                       f"{url}frostyard.flatpakrepo"], env)
            if add.returncode != 0:
                return [f"flatpak remote-add exited with {add.returncode}: {tail(add.stderr)}"]
            listed = run(["flatpak", "remote-ls", "--user", REMOTE, "--arch=*",
                          "--columns=ref,commit"], env)
        except subprocess.TimeoutExpired:
            return [f"flatpak did not finish within {RUN_TIMEOUT} seconds"]
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
    """Call attempt(n) until it returns no lines or the deadline passes; return the last lines."""
    number = 0
    while True:
        number += 1
        lines = attempt(number)
        if not lines:
            return []
        for line in lines:
            print(f"attempt {number}: {line}", file=sys.stderr)
        left = deadline - monotonic()
        if left <= 0:
            return lines
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

    deadline = monotonic() + args.timeout
    checks = (
        ("files", lambda number: check_files(args.site, url, number)),
        ("client", lambda number: check_client(args.site, url)),
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

    count = len(index_pairs((args.site / "index" / "static").read_bytes()))
    print(f"The live remote at {url} serves the built index: {count} image(s), "
          f"and flatpak remote-ls lists them.")


if __name__ == "__main__":
    main()
