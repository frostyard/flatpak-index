# Frostyard Flatpaks

A Flatpak remote for Frostyard's apps, served from GitHub Pages at
[frostyard.github.io/flatpak-index](https://frostyard.github.io/flatpak-index/).

## Install apps

Add the remote once:

```bash
flatpak remote-add --if-not-exists frostyard https://frostyard.github.io/flatpak-index/frostyard.flatpakrepo
```

Then install apps from it, and update them with `flatpak update` like any
other Flatpak:

| App | Install |
| --- | --- |
| [Igloo](https://github.com/frostyard/igloo), a terminal for nsl machines and containers | `flatpak install frostyard io.github.frostyard.Igloo` |
| [Frostnotes](https://github.com/frostyard/frostnotes), plain Markdown notes on your own computer | `flatpak install frostyard org.frostyard.Frostnotes` |
| [Frostmail](https://github.com/frostyard/frostmail), read and send mail on your own computer | `flatpak install frostyard org.frostyard.Frostmail` |

Apps use runtimes from Flathub, such as the GNOME runtime. If Flathub is not
set up, [add it](https://flathub.org/setup) first.

## How it works

The remote is an OCI remote, the format Fedora's Flatpak registry uses.

1. Each app's CI builds its Flatpak, exports it with
   `flatpak build-bundle --oci`, and pushes the image to
   `ghcr.io/frostyard/APP`. The image carries the Flatpak ref, metadata,
   AppStream data and icons as labels. The release workflow also attests the
   image digest with `actions/attest-build-provenance`.
2. This repository's [publish workflow](.github/workflows/publish.yml) runs
   [`scripts/build-index.py`](scripts/build-index.py), which asks GHCR,
   anonymously, for the images of every app in [`apps.json`](apps.json). For
   each tag, it runs `gh attestation verify` on the digest and lists the image
   only if the attestation comes from the app's release workflow, on a `v*`
   tag, on a GitHub-hosted runner. It also checks that every manifest and
   config blob matches its digest. It writes the verified images to
   `index/static`, the catalogue Flatpak reads from an OCI remote, and
   deploys that with [`frostyard.flatpakrepo`](frostyard.flatpakrepo) to
   GitHub Pages.
3. `flatpak remote-ls` and `flatpak install` fetch `index/static`, then pull
   the image from GHCR. GHCR wants a token even for public images, which the
   `org.flatpak.Authenticator.Oci` authenticator named in the `.flatpakrepo`
   file fetches.

If a digest has no valid attestation, the build fails and nothing is
deployed; the last deployed site stays live. Images that are missing, private
or not Flatpaks are skipped.

The workflow runs on every push, every hour, and when an app dispatches it
after publishing. Because it reads public images, app repositories need no
credentials for this one.

The approach follows [tuna-os/flatpak-index](https://github.com/tuna-os/flatpak-index).

## Add an app

1. In the app's repository, build the Flatpak in CI and push its OCI image to
   `ghcr.io/frostyard/APP:latest`, from a workflow that runs on a `v*` tag.
   [Igloo's workflow](https://github.com/frostyard/igloo/blob/main/.github/workflows/flatpak.yml)
   shows the build and the push; it does not yet run on a tag or attest.
2. In that workflow, attest the pushed image digest with
   `actions/attest-build-provenance` and `subject-digest`. The job needs the
   permissions `id-token: write` and `attestations: write`.
3. Make the package public: on the package's page in the
   [organization's packages](https://github.com/orgs/frostyard/packages),
   open *Package settings* and change its visibility. Private images are
   skipped, because Flatpak clients cannot pull them.
4. Add the repository to [`apps.json`](apps.json) and push. Name the release
   workflow's path in `"workflow"`, for example
   `".github/workflows/flatpak.yml"`. A digest without a valid attestation
   from that workflow stops the whole deploy.
5. Optionally, let the app refresh this remote right after it publishes:
   create a fine-grained token with *Actions: read and write* on this
   repository only, and save it in the app repository as the secret
   `FLATPAK_INDEX_DISPATCH_TOKEN`. Without it, the hourly run picks up new
   images.

The app should install `/app/share/metainfo/APP_ID.metainfo.xml` and its icons,
or software centres show a bare application ID.

## Stage a candidate

A suite desktop app (frostnotes, frostmail, frostread) gets its listing only
after a test on a clean desktop installs the release's attested digest from a
staged catalogue
([core ADR-0062](https://github.com/frostyard/core/blob/main/docs/adr/0062-make-desktop-suite-apps-standalone-flatpaks-with-opt-in-lodge.md)).
The staged catalogue is built by the same script, with the digest as the tag,
and served on the test machine only.

A release candidate is a `vX.Y.Z-rc.N` tag. The app's release workflow pushes
and attests it like a release, but does not move `latest`. The attestation
check accepts it, because the ref is a `v*` tag. The release later copies the
same digest to `vX.Y.Z` and `latest`, so the public catalogue lists the digest
that was tested, with the candidate's attestation.

The staged catalogue is never deployed: only `apps.json` is published, and a
test keeps every `apps.json` tag at `latest`.

### A. Build the staged catalogue

Do this on a host where `gh` is signed in (`gh auth login`, or `GH_TOKEN` in
the environment): the build runs `gh attestation verify`. The test machine
needs no credentials; step B copies the built directory to it. Run this from
this repository's root:

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

### B. Copy the catalogue to the test machine and serve it

Copy it from the build host, then serve it on the test machine, from the
directory that holds `_staged/`:

```bash
incus file push -r _staged VM/home/USER/   # or: scp -r _staged VM:
```

```bash
cd ~ && python3 -m http.server --bind 127.0.0.1 -d _staged 8765
```

Serve it on loopback only: nothing outside the machine reads it.

### C. Install the candidate on the test machine

```bash
flatpak remote-add --user --no-gpg-verify \
  --authenticator-name=org.flatpak.Authenticator.Oci \
  frostyard-staged oci+http://127.0.0.1:8765
flatpak install --user frostyard-staged APP_ID
flatpak info --user APP_ID
```

`Alt-id` in the output must equal the digest without its `sha256:` prefix.

### D. Update to a second candidate

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
list through `latest` with no new staged test (Brian, 2026-10-27); the
attestation check still gates every digest.

## Develop

```bash
python3 -m unittest discover -s tests -v
```

```bash
python3 scripts/build-index.py --apps apps.json --site _site
```

GitHub stops scheduled workflows in a public repository after 60 days without
activity; a push or a manual run restarts them.
