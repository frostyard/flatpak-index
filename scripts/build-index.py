#!/usr/bin/env python3
"""Build Frostyard's Flatpak remote from the images in GHCR.

Flatpak reads an OCI remote's catalogue from URL/index/static: a JSON document
that names a registry and lists, for each repository, its images with their
Flatpak labels (the ref, metadata, sizes, AppStream data and icons). Apps push
images built with `flatpak build-bundle --oci` to GHCR; this script asks GHCR
for the images of every app in apps.json and writes that document, so no app
repository needs credentials for this one.

The index lists only attested images. Before it reads an image, the script runs
`gh attestation verify` on the digest that the tag points to. The attestation
must come from the release workflow that apps.json names in "workflow", in the
app's own repository, on a v* tag, on a GitHub-hosted runner. Any digest
without a valid attestation stops the whole run with a non-zero exit code, so
nothing is deployed. The script also checks that every manifest and config
blob matches its sha256 digest, so the labels come from the verified image.
Images that are missing, private or not Flatpaks are skipped, in that order of
checks: a Flatpak check comes only after the verification.

Usage: build-index.py --apps apps.json --site _site
"""

import argparse
import html
import hashlib
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REGISTRY = "https://ghcr.io"

MANIFEST_TYPES = [
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
]
INDEX_TYPES = [
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
]


TAG_REF = re.compile(r"refs/tags/v[0-9][^/]*")


class Missing(Exception):
    """The registry has no public image for a repository and tag."""


class Unverified(Exception):
    """An image digest has no valid attestation, or its content does not match its digest."""


def fetch(url, token=None, accept=None):
    """Return the response headers and body of a GET."""
    request = urllib.request.Request(url, headers={"User-Agent": "frostyard-flatpak-index"})
    if token:
        request.add_header("Authorization", "Bearer " + token)
    if accept:
        request.add_header("Accept", accept)
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.headers, response.read()


def pull_token(repository):
    """An anonymous pull token; GHCR requires one even for public images."""
    query = urllib.parse.urlencode({"scope": f"repository:{repository}:pull", "service": "ghcr.io"})
    try:
        _, body = fetch(f"{REGISTRY}/token?{query}")
    except urllib.error.HTTPError as e:
        # GHCR refuses tokens for packages that are missing or private
        if e.code in (401, 403, 404):
            raise Missing(f"{repository}: HTTP {e.code}") from e
        raise
    return json.loads(body)["token"]


def sha256_digest(body):
    return "sha256:" + hashlib.sha256(body).hexdigest()


def check_digest(what, body, expected=None, header=None):
    """Return the sha256 digest of body; fail if it differs from the digest asked for or sent."""
    actual = sha256_digest(body)
    for source, digest in (("requested", expected), ("Docker-Content-Digest", header)):
        if digest and digest != actual:
            raise Unverified(f"{what}: content has digest {actual}, but the {source} digest is {digest}")
    return actual


def get_manifest(repository, reference, token):
    """Return the digest, media type and content of a manifest; the digest is computed from the body."""
    try:
        headers, body = fetch(f"{REGISTRY}/v2/{repository}/manifests/{reference}", token,
                              ", ".join(INDEX_TYPES + MANIFEST_TYPES))
    except urllib.error.HTTPError as e:
        # Private packages answer like missing ones: not installable either way.
        if e.code in (401, 403, 404):
            raise Missing(f"{repository}:{reference}: HTTP {e.code}") from e
        raise
    expected = reference if reference.startswith("sha256:") else None
    digest = check_digest(f"{repository}:{reference}", body, expected, headers.get("Docker-Content-Digest"))
    manifest = json.loads(body)
    media_type = manifest.get("mediaType") or headers.get("Content-Type", "").split(";")[0]
    return digest, media_type, manifest


def get_blob(repository, digest, token):
    _, body = fetch(f"{REGISTRY}/v2/{repository}/blobs/{digest}", token)
    check_digest(f"{repository} blob {digest}", body, expected=digest)
    return json.loads(body)


def verify_attestation(repository, digest, workflow):
    """Fail with Unverified unless the digest has an attestation from the workflow on a v* tag."""
    what = f"{repository}@{digest} (workflow {workflow})"
    command = [
        "gh", "attestation", "verify", f"oci://ghcr.io/{repository}@{digest}",
        "--repo", repository,
        "--signer-workflow", f"github.com/{repository}/{workflow}",
        "--deny-self-hosted-runners",
        "--format", "json",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except FileNotFoundError as e:
        raise Unverified(f"{what}: the gh command was not found") from e
    except subprocess.TimeoutExpired as e:
        raise Unverified(f"{what}: gh timed out after 120 seconds") from e
    if result.returncode != 0:
        tail = " | ".join((result.stderr or "").strip().splitlines()[-3:])
        raise Unverified(f"{what}: gh attestation verify exited with {result.returncode}: {tail}")
    try:
        items = json.loads(result.stdout)
        certificates = [item["verificationResult"]["signature"]["certificate"] for item in items]
    except (ValueError, KeyError, TypeError) as e:
        raise Unverified(f"{what}: cannot read the gh output: {e!r}") from e
    for certificate in certificates:
        ref = certificate.get("sourceRepositoryRef") if isinstance(certificate, dict) else None
        if (isinstance(ref, str) and TAG_REF.fullmatch(ref)
                and certificate.get("buildSignerURI") == f"https://github.com/{repository}/{workflow}@{ref}"):
            return
    raise Unverified(f"{what}: no attestation from a v* tag built by that workflow")


def image_entries(repository, tag, workflow):
    """The Flatpak images a tag names, one per architecture."""
    token = pull_token(repository)
    digest, media_type, manifest = get_manifest(repository, tag, token)
    # Verify before reading anything else. For an image index, this one check is
    # enough: its child manifests are bound to it by digest, and get_manifest
    # checks that each child's body matches that digest.
    verify_attestation(repository, digest, workflow)
    manifests = [(digest, media_type, manifest)]
    if media_type in INDEX_TYPES:
        manifests = [get_manifest(repository, m["digest"], token) for m in manifest.get("manifests", [])]

    entries = []
    for digest, media_type, manifest in manifests:
        config = get_blob(repository, manifest["config"]["digest"], token)
        # build-bundle --oci writes the labels to the image config; older
        # flatpak releases wrote them as manifest annotations.
        labels = dict(manifest.get("annotations") or {})
        labels.update((config.get("config") or {}).get("Labels") or {})
        if "org.flatpak.ref" not in labels:
            print(f"warning: {repository}:{tag} ({digest}) is not a Flatpak image; skipped", file=sys.stderr)
            continue
        entries.append({
            "Digest": digest,
            "MediaType": media_type,
            "OS": config.get("os", "linux"),
            "Architecture": config.get("architecture", "amd64"),
            "Tags": [tag],
            "Labels": labels,
        })
    return entries


def build_index(apps):
    """The index/static document for the apps in an apps.json document."""
    for app in apps["apps"]:
        if not app.get("workflow"):
            raise ValueError(f'{app.get("repository")}: apps.json needs a "workflow" key, the release workflow path in that repository')
    results = []
    for app in apps["apps"]:
        repository = app["repository"]
        workflow = app["workflow"]
        images = {}
        for tag in app.get("tags", ["latest"]):
            try:
                entries = image_entries(repository, tag, workflow)
            except Missing as e:
                print(f"warning: no public image for {e}; skipped", file=sys.stderr)
                continue
            for entry in entries:
                if entry["Digest"] in images:
                    images[entry["Digest"]]["Tags"].append(tag)
                else:
                    images[entry["Digest"]] = entry
        if images:
            results.append({"Name": repository, "Images": list(images.values())})
    return {"Registry": REGISTRY, "Results": results}


def label_name(image):
    """The app's ID and branch from its Flatpak ref."""
    kind, app_id, arch, branch = image["Labels"]["org.flatpak.ref"].split("/")
    return app_id, arch, branch


def write_site(index, site, root):
    """Write the remote: the index, the .flatpakrepo file and a landing page."""
    site.mkdir(parents=True, exist_ok=True)
    (site / "index").mkdir(exist_ok=True)
    (site / "index" / "static").write_text(json.dumps(index, indent=2) + "\n")
    shutil.copy(root / "frostyard.flatpakrepo", site / "frostyard.flatpakrepo")

    rows = []
    for result in index["Results"]:
        for image in result["Images"]:
            app_id, arch, branch = label_name(image)
            rows.append(f"<tr><td><code>{html.escape(app_id)}</code></td><td>{html.escape(arch)}</td>"
                        f"<td><code>flatpak install --user frostyard {html.escape(app_id)}</code></td></tr>")
    table = "\n".join(rows) or '<tr><td colspan="3">No apps yet.</td></tr>'
    (site / "index.html").write_text(PAGE.replace("@ROWS@", table))


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Frostyard Flatpaks</title>
<style>
  body { font: 16px/1.5 system-ui, sans-serif; max-width: 48rem; margin: 2rem auto; padding: 0 1rem; color: #0a1f31; background: #f4fcff; }
  code, pre { font-family: ui-monospace, monospace; }
  pre { background: #0a1f31; color: #e4f8ff; padding: 1rem; overflow-x: auto; border-radius: 6px; }
  table { border-collapse: collapse; width: 100%; }
  td, th { text-align: left; padding: .4rem .6rem; border-bottom: 1px solid #aee9ff; }
  @media (prefers-color-scheme: dark) { body { color: #e4f8ff; background: #06111d; } td, th { border-color: #17445e; } }
</style>
</head>
<body>
<h1>Frostyard Flatpaks</h1>
<p>Add the remote once, for your user, then install apps from it and update them with <code>flatpak update</code>.</p>
<pre>flatpak remote-add --user --if-not-exists frostyard https://frostyard.github.io/flatpak-index/frostyard.flatpakrepo</pre>
<p>Snow images already have this remote, so Snow users can skip this step.</p>
<table>
<tr><th>App</th><th>Architecture</th><th>Install</th></tr>
@ROWS@
</table>
<p>Apps use runtimes from <a href="https://flathub.org/">Flathub</a>. A <code>--user</code> install gets a missing runtime only from a user remote,
so add Flathub for your user too:</p>
<pre>flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo</pre>
<p>Source: <a href="https://github.com/frostyard/flatpak-index">frostyard/flatpak-index</a>.</p>
</body>
</html>
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apps", type=Path, required=True, help="apps.json")
    parser.add_argument("--site", type=Path, required=True, help="directory to write the remote to")
    args = parser.parse_args(argv)

    apps = json.loads(args.apps.read_text())
    try:
        index = build_index(apps)
    except Unverified as e:
        print(f"error: unverified image: {e}", file=sys.stderr)
        sys.exit(1)
    write_site(index, args.site, args.apps.resolve().parent)
    count = sum(len(r["Images"]) for r in index["Results"])
    print(f"Wrote {count} image(s) from {len(index['Results'])} of {len(apps['apps'])} app(s) to {args.site}")


if __name__ == "__main__":
    main()
