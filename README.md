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
| [Penguin Mail](https://penguin-mail.com), mail and calendar | `flatpak install frostyard io.github.c9dev.PenguinMail` |

Apps use runtimes from Flathub, such as the GNOME runtime. If Flathub is not
set up, [add it](https://flathub.org/setup) first.

## How it works

The remote is an OCI remote, the format Fedora's Flatpak registry uses.

1. Each app's CI builds its Flatpak, exports it with
   `flatpak build-bundle --oci`, and pushes the image to
   `ghcr.io/frostyard/APP`. The image carries the Flatpak ref, metadata,
   AppStream data and icons as labels.
2. This repository's [publish workflow](.github/workflows/publish.yml) runs
   [`scripts/build-index.py`](scripts/build-index.py), which asks GHCR,
   anonymously, for the images of every app in [`apps.json`](apps.json). It
   writes them to `index/static`, the catalogue Flatpak reads from an OCI
   remote, and deploys that with [`frostyard.flatpakrepo`](frostyard.flatpakrepo)
   to GitHub Pages.
3. `flatpak remote-ls` and `flatpak install` fetch `index/static`, then pull
   the image from GHCR. GHCR wants a token even for public images, which the
   `org.flatpak.Authenticator.Oci` authenticator named in the `.flatpakrepo`
   file fetches.

The workflow runs on every push, every hour, and when an app dispatches it
after publishing. Because it reads public images, app repositories need no
credentials for this one.

The approach follows [tuna-os/flatpak-index](https://github.com/tuna-os/flatpak-index).

## Add an app

1. In the app's repository, build the Flatpak in CI and push its OCI image to
   `ghcr.io/frostyard/APP:latest`. [Igloo's workflow](https://github.com/frostyard/igloo/blob/main/.github/workflows/flatpak.yml)
   is a working example.
2. Make the package public: on the package's page in the
   [organization's packages](https://github.com/orgs/frostyard/packages),
   open *Package settings* and change its visibility. Private images are
   skipped, because Flatpak clients cannot pull them.
3. Add the repository to [`apps.json`](apps.json) and push.
4. Optionally, let the app refresh this remote right after it publishes:
   create a fine-grained token with *Actions: read and write* on this
   repository only, and save it in the app repository as the secret
   `FLATPAK_INDEX_DISPATCH_TOKEN`. Without it, the hourly run picks up new
   images.

The app should install `/app/share/metainfo/APP_ID.metainfo.xml` and its icons,
or software centres show a bare application ID.

## Apps built here

Some apps come from other projects and are built in this repository instead,
each by its own workflow, which pushes to `ghcr.io/frostyard/APP` the same way.

- [Penguin Mail](https://github.com/c9dev/penguin-mail):
  [`penguin-mail.yml`](.github/workflows/penguin-mail.yml) builds a pinned
  upstream release with upstream's own manifest and starts it on a hidden
  display before publishing. To update it, change `UPSTREAM_TAG` and
  `UPSTREAM_COMMIT` together. Upstream's manifest leaves out the Google and
  Microsoft OAuth clients, so the workflow adds them from the repository
  secrets `PENGUIN_MAIL_GOOGLE_CLIENT_ID`, `PENGUIN_MAIL_GOOGLE_CLIENT_SECRET`
  and `PENGUIN_MAIL_MICROSOFT_CLIENT_ID`. Without the Google pair, Gmail
  sign-in fails, so main does not publish.

## Develop

```bash
python3 -m unittest discover -s tests -v
```

```bash
python3 scripts/build-index.py --apps apps.json --site _site
```

GitHub stops scheduled workflows in a public repository after 60 days without
activity; a push or a manual run restarts them.
