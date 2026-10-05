"""Tests for scripts/build-index.py with a fake GHCR."""

import importlib.util
import io
import json
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


class FakeRegistry:
    """Serves tokens, manifests and config blobs keyed by URL path."""

    def __init__(self):
        self.manifests = {}  # (repository, reference) -> (digest, media type, manifest)
        self.blobs = {}  # (repository, digest) -> object
        self.refused = set()  # repositories whose tokens are refused

    def add_image(self, repository, tag, digest, labels, arch="amd64", annotations=None):
        config_digest = "sha256:config-" + digest[7:]
        manifest = {"mediaType": OCI_MANIFEST, "config": {"digest": config_digest}}
        if annotations:
            manifest["annotations"] = annotations
        self.manifests[(repository, tag)] = (digest, OCI_MANIFEST, manifest)
        self.manifests[(repository, digest)] = (digest, OCI_MANIFEST, manifest)
        config = {"os": "linux", "architecture": arch}
        if labels is not None:
            config["config"] = {"Labels": labels}
        self.blobs[(repository, config_digest)] = config

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
            digest, media_type, manifest = self.manifests[(repository, reference)]
            return {"Docker-Content-Digest": digest, "Content-Type": media_type}, json.dumps(manifest).encode()
        repository, digest = rest.split("/blobs/")
        return {}, json.dumps(self.blobs[(repository, digest)]).encode()


class BuildIndexTest(unittest.TestCase):
    def setUp(self):
        self.registry = FakeRegistry()
        patcher = mock.patch.object(build_index, "fetch", self.registry.fetch)
        patcher.start()
        self.addCleanup(patcher.stop)

    def index(self, apps):
        with mock.patch("sys.stderr", io.StringIO()) as stderr:
            result = build_index.build_index({"apps": apps})
        return result, stderr.getvalue()

    def test_image(self):
        self.registry.add_image("frostyard/igloo", "latest", "sha256:aaa", flatpak_labels("io.github.frostyard.Igloo"))
        index, _ = self.index([{"repository": "frostyard/igloo"}])
        self.assertEqual(index["Registry"], "https://ghcr.io")
        self.assertEqual(index["Results"], [{
            "Name": "frostyard/igloo",
            "Images": [{
                "Digest": "sha256:aaa",
                "MediaType": OCI_MANIFEST,
                "OS": "linux",
                "Architecture": "amd64",
                "Tags": ["latest"],
                "Labels": flatpak_labels("io.github.frostyard.Igloo"),
            }],
        }])

    def test_image_index_lists_each_architecture(self):
        self.registry.add_image("frostyard/igloo", "x86", "sha256:x86", flatpak_labels("a.B"))
        self.registry.add_image("frostyard/igloo", "arm", "sha256:arm", flatpak_labels("a.B", "aarch64"), arch="arm64")
        self.registry.manifests[("frostyard/igloo", "latest")] = ("sha256:index", OCI_INDEX, {
            "mediaType": OCI_INDEX,
            "manifests": [{"digest": "sha256:x86"}, {"digest": "sha256:arm"}],
        })
        index, _ = self.index([{"repository": "frostyard/igloo"}])
        images = index["Results"][0]["Images"]
        self.assertEqual([(i["Digest"], i["Architecture"]) for i in images], [("sha256:x86", "amd64"), ("sha256:arm", "arm64")])

    def test_tags_of_one_image_merge(self):
        self.registry.add_image("frostyard/igloo", "latest", "sha256:aaa", flatpak_labels("a.B"))
        self.registry.manifests[("frostyard/igloo", "stable")] = self.registry.manifests[("frostyard/igloo", "latest")]
        index, _ = self.index([{"repository": "frostyard/igloo", "tags": ["latest", "stable"]}])
        self.assertEqual(index["Results"][0]["Images"][0]["Tags"], ["latest", "stable"])

    def test_annotations_are_read_when_the_config_has_no_labels(self):
        self.registry.add_image("frostyard/igloo", "latest", "sha256:aaa", None, annotations=flatpak_labels("a.B"))
        index, _ = self.index([{"repository": "frostyard/igloo"}])
        self.assertEqual(index["Results"][0]["Images"][0]["Labels"]["org.flatpak.ref"], "app/a.B/x86_64/master")

    def test_missing_private_and_foreign_images_are_skipped(self):
        self.registry.add_image("frostyard/igloo", "latest", "sha256:aaa", flatpak_labels("a.B"))
        self.registry.add_image("frostyard/server", "latest", "sha256:bbb", {"org.opencontainers.image.title": "x"})
        self.registry.refused.add("frostyard/private")
        index, warnings = self.index([
            {"repository": "frostyard/igloo"},
            {"repository": "frostyard/server"},
            {"repository": "frostyard/private"},
            {"repository": "frostyard/igloo", "tags": ["nope"]},
        ])
        self.assertEqual([r["Name"] for r in index["Results"]], ["frostyard/igloo"])
        self.assertIn("frostyard/server:latest (sha256:bbb) is not a Flatpak image", warnings)
        self.assertIn("no public image for frostyard/private: HTTP 403", warnings)
        self.assertIn("no public image for frostyard/igloo:nope: HTTP 404", warnings)

    def test_other_registry_errors_fail(self):
        def broken(url, token=None, accept=None):
            raise urllib.error.HTTPError(url, 500, "Server Error", {}, io.BytesIO())

        with mock.patch.object(build_index, "fetch", broken):
            with self.assertRaises(urllib.error.HTTPError):
                build_index.build_index({"apps": [{"repository": "frostyard/igloo"}]})

    def test_site(self):
        self.registry.add_image("frostyard/igloo", "latest", "sha256:aaa", flatpak_labels("io.github.frostyard.Igloo"))
        index, _ = self.index([{"repository": "frostyard/igloo"}])
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp) / "_site"
            build_index.write_site(index, site, ROOT)
            self.assertEqual(json.loads((site / "index" / "static").read_text()), index)
            self.assertEqual((site / "frostyard.flatpakrepo").read_text(), (ROOT / "frostyard.flatpakrepo").read_text())
            page = (site / "index.html").read_text()
            self.assertIn("flatpak install frostyard io.github.frostyard.Igloo", page)

    def test_flatpakrepo_points_at_the_pages_site(self):
        repo = (ROOT / "frostyard.flatpakrepo").read_text()
        self.assertIn("Url=oci+https://frostyard.github.io/flatpak-index\n", repo)
        self.assertIn("AuthenticatorName=org.flatpak.Authenticator.Oci\n", repo)


if __name__ == "__main__":
    unittest.main()
