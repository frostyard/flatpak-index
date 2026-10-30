"""Tests for scripts/build-index.py with a fake GHCR."""

import hashlib
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("build_index", ROOT / "scripts" / "build-index.py")
build_index = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_index)

OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
OCI_INDEX = "application/vnd.oci.image.index.v1+json"


def flatpak_labels(app_id, arch="x86_64"):
    return {"org.flatpak.ref": f"app/{app_id}/{arch}/master", "org.flatpak.metadata": "[Application]\n"}


WORKFLOW = ".github/workflows/flatpak.yml"
APP = {"repository": "frostyard/igloo", "workflow": WORKFLOW}


def body(obj):
    return json.dumps(obj, sort_keys=True).encode()


def digest_of(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


class FakeRegistry:
    """Serves tokens, manifests and config blobs; every body has a matching digest."""

    def __init__(self):
        self.manifests = {}  # (repository, reference) -> (media type, body, header digest or None)
        self.blobs = {}  # (repository, digest) -> body
        self.refused = set()  # repositories whose tokens are refused

    def add_manifest(self, repository, media_type, obj, tags=()):
        data = body(obj)
        digest = digest_of(data)
        for reference in (digest, *tags):
            self.manifests[(repository, reference)] = (media_type, data, digest)
        return digest

    def add_image(self, repository, tag, labels, arch="amd64", annotations=None):
        """Add an image; return its manifest digest."""
        config = {"os": "linux", "architecture": arch}
        if labels is not None:
            config["config"] = {"Labels": labels}
        config_body = body(config)
        config_digest = digest_of(config_body)
        self.blobs[(repository, config_digest)] = config_body
        manifest = {"mediaType": OCI_MANIFEST, "config": {"digest": config_digest}}
        if annotations:
            manifest["annotations"] = annotations
        return self.add_manifest(repository, OCI_MANIFEST, manifest, [tag] if tag else [])

    def add_index(self, repository, tag, children):
        return self.add_manifest(repository, OCI_INDEX, {
            "mediaType": OCI_INDEX, "manifests": [{"digest": d} for d in children]}, [tag])

    def fetch(self, url, token=None, accept=None):
        path = url.removeprefix(build_index.REGISTRY)
        if path.startswith("/token?"):
            repository = path.split("repository%3A", 1)[1].split("%3Apull", 1)[0].replace("%2F", "/")
            if repository in self.refused:
                raise urllib.error.HTTPError(url, 403, "Forbidden", {}, io.BytesIO())
            return {}, json.dumps({"token": "t"}).encode()
        assert token == "t", "registry requests need the pull token"
        _, rest = path.split("/v2/", 1)
        if "/manifests/" in rest:
            repository, reference = rest.split("/manifests/")
            if (repository, reference) not in self.manifests:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO())
            media_type, data, digest = self.manifests[(repository, reference)]
            headers = {"Content-Type": media_type}
            if digest:
                headers["Docker-Content-Digest"] = digest
            return headers, data
        repository, digest = rest.split("/blobs/")
        return {}, self.blobs[(repository, digest)]


class FakeGh:
    """A fake `gh`: attested[(repository, digest)] = (workflow, source ref)."""

    def __init__(self):
        self.attested = {}
        self.calls = []
        self.missing = False

    def attest(self, repository, digest, workflow=WORKFLOW, ref="refs/tags/v1.2.3"):
        self.attested[(repository, digest)] = (workflow, ref)

    def run(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if self.missing:
            raise FileNotFoundError("gh")
        subject = command[3].removeprefix("oci://ghcr.io/")
        repository, digest = subject.split("@")
        signer = command[command.index("--signer-workflow") + 1]
        attested = self.attested.get((repository, digest))
        if not attested or signer != f"github.com/{repository}/{attested[0]}":
            return subprocess.CompletedProcess(command, 1, "", "line1\nno matching attestations found\n")
        workflow, ref = attested
        certificate = {
            "subjectAlternativeName": f"https://github.com/{repository}/{workflow}@{ref}",
            "buildSignerURI": f"https://github.com/{repository}/{workflow}@{ref}",
            "sourceRepositoryRef": ref,
            "sourceRepositoryURI": f"https://github.com/{repository}",
        }
        out = json.dumps([{"verificationResult": {"signature": {"certificate": certificate}}}])
        return subprocess.CompletedProcess(command, 0, out, "")


class BuildIndexTest(unittest.TestCase):
    def setUp(self):
        self.registry = FakeRegistry()
        self.gh = FakeGh()
        for patcher in (mock.patch.object(build_index, "fetch", self.registry.fetch),
                        mock.patch.object(build_index.subprocess, "run", self.gh.run)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def image(self, repository, tag, labels, attest=True, **kwargs):
        digest = self.registry.add_image(repository, tag, labels, **kwargs)
        if attest:
            self.gh.attest(repository, digest)
        return digest

    def index(self, apps):
        with mock.patch("sys.stderr", io.StringIO()) as stderr:
            result = build_index.build_index({"apps": apps})
        return result, stderr.getvalue()

    def test_image(self):
        digest = self.image("frostyard/igloo", "latest", flatpak_labels("io.github.frostyard.Igloo"))
        index, _ = self.index([APP])
        self.assertEqual(index["Registry"], "https://ghcr.io")
        self.assertEqual(index["Results"], [{
            "Name": "frostyard/igloo",
            "Images": [{
                "Digest": digest,
                "MediaType": OCI_MANIFEST,
                "OS": "linux",
                "Architecture": "amd64",
                "Tags": ["latest"],
                "Labels": flatpak_labels("io.github.frostyard.Igloo"),
            }],
        }])

    def test_gh_is_called_with_the_pinned_workflow(self):
        digest = self.image("frostyard/igloo", "latest", flatpak_labels("a.B"))
        self.index([APP])
        command, kwargs = self.gh.calls[0]
        self.assertEqual(command, [
            "gh", "attestation", "verify", f"oci://ghcr.io/frostyard/igloo@{digest}",
            "--repo", "frostyard/igloo",
            "--signer-workflow", "github.com/frostyard/igloo/.github/workflows/flatpak.yml",
            "--deny-self-hosted-runners",
            "--format", "json",
        ])
        self.assertEqual(kwargs["timeout"], 120)
        self.assertTrue(kwargs["capture_output"])
        self.assertFalse(kwargs.get("shell"))

    def test_image_index_is_verified_once(self):
        x86 = self.image("frostyard/igloo", None, flatpak_labels("a.B"), attest=False)
        arm = self.image("frostyard/igloo", None, flatpak_labels("a.B", "aarch64"), attest=False, arch="arm64")
        top = self.registry.add_index("frostyard/igloo", "latest", [x86, arm])
        self.gh.attest("frostyard/igloo", top)
        index, _ = self.index([APP])
        images = index["Results"][0]["Images"]
        self.assertEqual([(i["Digest"], i["Architecture"]) for i in images], [(x86, "amd64"), (arm, "arm64")])
        self.assertEqual(len(self.gh.calls), 1)
        self.assertIn(top, self.gh.calls[0][0][3])

    def test_tags_of_one_image_merge(self):
        self.image("frostyard/igloo", "latest", flatpak_labels("a.B"))
        self.registry.manifests[("frostyard/igloo", "stable")] = self.registry.manifests[("frostyard/igloo", "latest")]
        index, _ = self.index([{**APP, "tags": ["latest", "stable"]}])
        self.assertEqual(index["Results"][0]["Images"][0]["Tags"], ["latest", "stable"])

    def test_digest_as_the_tag_lists_that_one_digest(self):
        latest = self.image("frostyard/igloo", "latest", flatpak_labels("a.B"))
        digest = self.image("frostyard/igloo", "v1.3.0-rc.1", flatpak_labels("a.C"))
        self.assertNotEqual(latest, digest)
        index, _ = self.index([{**APP, "tags": [digest]}])
        images = index["Results"][0]["Images"]
        self.assertEqual([i["Digest"] for i in images], [digest])
        self.assertEqual(images[0]["Tags"], [digest])
        self.assertEqual([call[0][3] for call in self.gh.calls], [f"oci://ghcr.io/frostyard/igloo@{digest}"])

    def test_annotations_are_read_when_the_config_has_no_labels(self):
        self.image("frostyard/igloo", "latest", None, annotations=flatpak_labels("a.B"))
        index, _ = self.index([APP])
        self.assertEqual(index["Results"][0]["Images"][0]["Labels"]["org.flatpak.ref"], "app/a.B/x86_64/master")

    def test_missing_private_and_foreign_images_are_skipped(self):
        self.image("frostyard/igloo", "latest", flatpak_labels("a.B"))
        foreign = self.image("frostyard/server", "latest", {"org.opencontainers.image.title": "x"})
        self.registry.refused.add("frostyard/private")
        index, warnings = self.index([
            APP,
            {"repository": "frostyard/server", "workflow": WORKFLOW},
            {"repository": "frostyard/private", "workflow": WORKFLOW},
            {**APP, "tags": ["nope"]},
        ])
        self.assertEqual([r["Name"] for r in index["Results"]], ["frostyard/igloo"])
        self.assertIn(f"frostyard/server:latest ({foreign}) is not a Flatpak image", warnings)
        self.assertIn("no public image for frostyard/private: HTTP 403", warnings)
        self.assertIn("no public image for frostyard/igloo:nope: HTTP 404", warnings)

    def test_foreign_image_without_attestation_fails(self):
        self.image("frostyard/server", "latest", {"org.opencontainers.image.title": "x"}, attest=False)
        with self.assertRaises(build_index.Unverified):
            self.index([{"repository": "frostyard/server", "workflow": WORKFLOW}])

    def test_other_registry_errors_fail(self):
        def broken(url, token=None, accept=None):
            raise urllib.error.HTTPError(url, 500, "Server Error", {}, io.BytesIO())

        with mock.patch.object(build_index, "fetch", broken):
            with self.assertRaises(urllib.error.HTTPError):
                build_index.build_index({"apps": [APP]})

    def test_missing_attestation_fails(self):
        digest = self.image("frostyard/igloo", "latest", flatpak_labels("a.B"), attest=False)
        with self.assertRaises(build_index.Unverified) as caught:
            self.index([APP])
        message = str(caught.exception)
        for part in ("frostyard/igloo", digest, WORKFLOW, "no matching attestations found"):
            self.assertIn(part, message)

    def test_wrong_signer_workflow_fails(self):
        digest = self.registry.add_image("frostyard/igloo", "latest", flatpak_labels("a.B"))
        self.gh.attest("frostyard/igloo", digest, workflow=".github/workflows/other.yml")
        with self.assertRaises(build_index.Unverified):
            self.index([APP])
        command = self.gh.calls[0][0]
        self.assertEqual(command[command.index("--signer-workflow") + 1],
                         "github.com/frostyard/igloo/.github/workflows/flatpak.yml")

    def test_branch_ref_fails(self):
        digest = self.registry.add_image("frostyard/igloo", "latest", flatpak_labels("a.B"))
        self.gh.attest("frostyard/igloo", digest, ref="refs/heads/main")
        with self.assertRaises(build_index.Unverified) as caught:
            self.index([APP])
        self.assertIn("v* tag", str(caught.exception))

    def test_tag_ref_must_match_fully(self):
        for ref in ("refs/tags/v1/x", "refs/tags/version", "refs/tags/xv1.0"):
            with self.subTest(ref=ref):
                self.gh = FakeGh()
                digest = self.registry.add_image("frostyard/igloo", "latest", flatpak_labels("a.B"))
                self.gh.attest("frostyard/igloo", digest, ref=ref)
                with mock.patch.object(build_index.subprocess, "run", self.gh.run):
                    with self.assertRaises(build_index.Unverified):
                        self.index([APP])

    def test_release_candidate_tag_verifies(self):
        digest = self.registry.add_image("frostyard/frostmail", "v0.4.0-rc.1", flatpak_labels("org.frostyard.Frostmail"))
        self.gh.attest("frostyard/frostmail", digest, ref="refs/tags/v0.4.0-rc.1")
        index, warnings = self.index(
            [{"repository": "frostyard/frostmail", "workflow": WORKFLOW, "tags": ["v0.4.0-rc.1"]}])
        self.assertEqual(warnings, "")
        self.assertEqual(index["Results"], [{
            "Name": "frostyard/frostmail",
            "Images": [{
                "Digest": digest,
                "MediaType": OCI_MANIFEST,
                "OS": "linux",
                "Architecture": "amd64",
                "Tags": ["v0.4.0-rc.1"],
                "Labels": flatpak_labels("org.frostyard.Frostmail"),
            }],
        }])
        command = self.gh.calls[0][0]
        self.assertIn(digest, command[3])
        self.assertEqual(command[command.index("--signer-workflow") + 1],
                         "github.com/frostyard/frostmail/.github/workflows/flatpak.yml")

    def test_signer_uri_must_match_the_ref(self):
        digest = self.registry.add_image("frostyard/igloo", "latest", flatpak_labels("a.B"))
        self.gh.attest("frostyard/igloo", digest)
        run = self.gh.run

        def other_tag(command, **kwargs):
            result = run(command, **kwargs)
            items = json.loads(result.stdout)
            items[0]["verificationResult"]["signature"]["certificate"]["buildSignerURI"] += "9"
            return subprocess.CompletedProcess(command, 0, json.dumps(items), "")

        with mock.patch.object(build_index.subprocess, "run", other_tag):
            with self.assertRaises(build_index.Unverified):
                self.index([APP])

    def test_bad_gh_output_fails(self):
        self.image("frostyard/igloo", "latest", flatpak_labels("a.B"))
        for out in ("not json", "[]", '[{"verificationResult": {}}]', '{"a": 1}'):
            with self.subTest(out=out):
                done = subprocess.CompletedProcess([], 0, out, "")
                with mock.patch.object(build_index.subprocess, "run", return_value=done):
                    with self.assertRaises(build_index.Unverified):
                        self.index([APP])

    def test_gh_timeout_fails(self):
        self.image("frostyard/igloo", "latest", flatpak_labels("a.B"))
        with mock.patch.object(build_index.subprocess, "run", side_effect=subprocess.TimeoutExpired("gh", 120)):
            with self.assertRaises(build_index.Unverified):
                self.index([APP])

    def test_missing_workflow_key_fails(self):
        self.image("frostyard/igloo", "latest", flatpak_labels("a.B"))
        with self.assertRaisesRegex(ValueError, "workflow"):
            self.index([{"repository": "frostyard/igloo"}])
        self.assertEqual(self.gh.calls, [])

    def test_gh_not_installed_fails(self):
        self.image("frostyard/igloo", "latest", flatpak_labels("a.B"))
        self.gh.missing = True
        with self.assertRaises(build_index.Unverified) as caught:
            self.index([APP])
        self.assertIn("not found", str(caught.exception))

    def test_manifest_that_does_not_match_its_digest_fails(self):
        digest = self.image("frostyard/igloo", "latest", flatpak_labels("a.B"))
        media_type, _, _ = self.registry.manifests[("frostyard/igloo", "latest")]
        # a registry that sends other content under the digest it claims
        self.registry.manifests[("frostyard/igloo", "latest")] = (media_type, body({"config": {"digest": "x"}}), digest)
        with self.assertRaises(build_index.Unverified):
            self.index([APP])
        self.assertEqual(self.gh.calls, [])

    def test_manifest_without_digest_header_uses_the_computed_digest(self):
        digest = self.image("frostyard/igloo", "latest", flatpak_labels("a.B"))
        media_type, data, _ = self.registry.manifests[("frostyard/igloo", "latest")]
        self.registry.manifests[("frostyard/igloo", "latest")] = (media_type, data, None)
        index, _ = self.index([APP])
        self.assertEqual(index["Results"][0]["Images"][0]["Digest"], digest)
        self.assertIn(digest, self.gh.calls[0][0][3])

    def test_child_manifest_that_does_not_match_its_digest_fails(self):
        child = self.image("frostyard/igloo", None, flatpak_labels("a.B"), attest=False)
        top = self.registry.add_index("frostyard/igloo", "latest", [child])
        self.gh.attest("frostyard/igloo", top)
        media_type, _, _ = self.registry.manifests[("frostyard/igloo", child)]
        self.registry.manifests[("frostyard/igloo", child)] = (media_type, body({"config": {"digest": "x"}}), None)
        with self.assertRaises(build_index.Unverified):
            self.index([APP])

    def test_config_blob_that_does_not_match_its_digest_fails(self):
        self.image("frostyard/igloo", "latest", flatpak_labels("a.B"))
        for key in self.registry.blobs:
            self.registry.blobs[key] = body({"config": {"Labels": flatpak_labels("evil.App")}})
        with self.assertRaises(build_index.Unverified):
            self.index([APP])

    def test_one_failing_app_fails_the_run(self):
        self.image("frostyard/igloo", "latest", flatpak_labels("a.B"))
        self.image("frostyard/other", "latest", flatpak_labels("c.D"), attest=False)
        with self.assertRaises(build_index.Unverified):
            self.index([APP, {"repository": "frostyard/other", "workflow": WORKFLOW}])

    def test_main_exits_non_zero_and_writes_no_site(self):
        self.image("frostyard/igloo", "latest", flatpak_labels("a.B"), attest=False)
        with tempfile.TemporaryDirectory() as tmp:
            apps = Path(tmp) / "apps.json"
            apps.write_text(json.dumps({"apps": [APP]}))
            site = Path(tmp) / "_site"
            with mock.patch("sys.stderr", io.StringIO()) as stderr:
                with self.assertRaises(SystemExit) as caught:
                    build_index.main(["--apps", str(apps), "--site", str(site)])
            self.assertEqual(caught.exception.code, 1)
            self.assertFalse(site.exists())
            self.assertIn("frostyard/igloo", stderr.getvalue())

    def test_main_writes_the_site_when_verified(self):
        self.image("frostyard/igloo", "latest", flatpak_labels("a.B"))
        with tempfile.TemporaryDirectory() as tmp:
            apps = Path(tmp) / "apps.json"
            apps.write_text(json.dumps({"apps": [APP]}))
            (Path(tmp) / "frostyard.flatpakrepo").write_text("[Flatpak Repo]\n")
            site = Path(tmp) / "_site"
            with mock.patch("sys.stdout", io.StringIO()):
                build_index.main(["--apps", str(apps), "--site", str(site)])
            self.assertTrue((site / "index" / "static").exists())

    def test_apps_json_names_a_workflow_for_every_app(self):
        for app in json.loads((ROOT / "apps.json").read_text())["apps"]:
            self.assertTrue(app.get("workflow"), app)

    def test_apps_json_lists_only_the_latest_tag(self):
        # The public catalogue never lists a candidate tag or a digest.
        for app in json.loads((ROOT / "apps.json").read_text())["apps"]:
            with self.subTest(repository=app["repository"]):
                self.assertEqual(app.get("tags", ["latest"]), ["latest"])

    def test_site(self):
        self.image("frostyard/igloo", "latest", flatpak_labels("io.github.frostyard.Igloo"))
        index, _ = self.index([APP])
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp) / "_site"
            build_index.write_site(index, site, ROOT)
            self.assertEqual(json.loads((site / "index" / "static").read_text()), index)
            self.assertEqual((site / "frostyard.flatpakrepo").read_text(), (ROOT / "frostyard.flatpakrepo").read_text())
            page = (site / "index.html").read_text()
            self.assertIn("flatpak install --user frostyard io.github.frostyard.Igloo", page)
            self.assertIn("flatpak remote-add --user --if-not-exists frostyard "
                          "https://frostyard.github.io/flatpak-index/frostyard.flatpakrepo", page)
            self.assertNotIn("Snow", page)
            self.assertIn("flatpak remote-add --user --if-not-exists flathub", page)
            self.assertNotIn("flatpak remote-add --if-not-exists", page)

    def test_flatpakrepo_points_at_the_pages_site(self):
        repo = (ROOT / "frostyard.flatpakrepo").read_text()
        self.assertIn("Url=oci+https://frostyard.github.io/flatpak-index\n", repo)
        self.assertIn("AuthenticatorName=org.flatpak.Authenticator.Oci\n", repo)


if __name__ == "__main__":
    unittest.main()
