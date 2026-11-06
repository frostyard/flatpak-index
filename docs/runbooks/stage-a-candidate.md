# Runbook: stage a candidate

A suite desktop app (frostnotes, frostmail, frostread) gets its listing only
after a test on a clean desktop installs the release's attested digest from a
staged catalogue
([core ADR-0062](https://github.com/frostyard/core/blob/main/docs/adr/0062-make-desktop-suite-apps-standalone-flatpaks-with-opt-in-lodge.md)).
The staged catalogue is built by [`scripts/build-index.py`](../../scripts/build-index.py),
with the digest as the tag, and served on the test machine only.

A release candidate is a `vX.Y.Z-rc.N` tag. The app's release workflow pushes
and attests it like a release, but does not move `latest`. The attestation
check accepts it, because the ref is a `v*` tag. The release later copies the
same digest to `vX.Y.Z` and `latest`, so the public catalogue lists the digest
that was tested, with the candidate's attestation.

The staged catalogue is never deployed: only `apps.json` is published, and a
test keeps every `apps.json` tag at `latest` or at an old app ID (see
[rename-an-app-id.md](rename-an-app-id.md)).

## A. Build the staged catalogue

Do this on a host where `gh` is signed in (`gh auth login`, or `GH_TOKEN` in
the environment): the build runs `gh attestation verify`. The test machine
needs no credentials; step B copies the built directory to it. Run this from
the repository root:

```bash
skopeo inspect --no-creds --format '{{.Digest}}' \
  docker://ghcr.io/frostyard/APP:vX.Y.Z-rc.N
cat > staged.json <<'JSON'
{"apps": [{"repository": "frostyard/APP", "tags": ["sha256:DIGEST"],
           "workflow": ".github/workflows/flatpak.yml"}]}
JSON
python3 scripts/build-index.py --apps staged.json --site _staged
```

Put the digest from `skopeo inspect` in `staged.json` as the only tag.
`staged.json` stays in the repository root, because the script copies
`frostyard.flatpakrepo` from the directory of the `--apps` file. `.gitignore`
already ignores `staged.json` and `_staged/`.

The script verifies the attestation as the publish workflow does, so an
unattested digest fails here too.

## B. Copy the catalogue to the test machine and serve it

Copy it from the build host, then serve it on the test machine, from the
directory that holds `_staged/`:

```bash
incus file push -r _staged VM/home/USER/   # or: scp -r _staged VM:
```

```bash
cd ~ && python3 -m http.server --bind 127.0.0.1 -d _staged 8765
```

Serve it on loopback only: nothing outside the machine reads it.

## C. Install the candidate on the test machine

```bash
flatpak remote-add --user --no-gpg-verify \
  --authenticator-name=org.flatpak.Authenticator.Oci \
  frostyard-staged oci+http://127.0.0.1:8765
flatpak install --user frostyard-staged APP_ID
flatpak info --user APP_ID
```

`Alt-id` in the output must equal the digest without its `sha256:` prefix.

## D. Update to a second candidate

Build the catalogue again, with the second digest as the only tag, and copy
`_staged/` to the test machine again. Flatpak caches an OCI remote's index, so
remove the 2 cached files before the update:

```bash
rm ~/.local/share/flatpak/oci/frostyard-staged.index.gz \
   ~/.local/share/flatpak/oci/frostyard-staged.summary
flatpak update --user APP_ID
flatpak info --user APP_ID
```

Without the removal, `flatpak update` says "Nothing to do". Check `Alt-id`
again.

Record the digest and this repository's commit with the test. Merge the app's
`apps.json` entry only when its `latest` tag still points to the tested digest:

```bash
skopeo inspect --no-creds --format '{{.Digest}}' \
  docker://ghcr.io/frostyard/APP:latest
```

The staged test gates an app's first listing only. After that, later releases
list through `latest` with no new staged test; the attestation check still
gates every digest.
