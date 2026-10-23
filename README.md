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
   `index/static`, the catalogue Flatpak reads from an OCI remote, and deploys that with [`frostyard.flatpakrepo`](frostyard.flatpakrepo)
   to GitHub Pages.
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

## Develop

```bash
python3 -m unittest discover -s tests -v
```

```bash
python3 scripts/build-index.py --apps apps.json --site _site
```

GitHub stops scheduled workflows in a public repository after 60 days without
activity; a push or a manual run restarts them.
