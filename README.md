<!-- readme-kind: packaging -->
# Frostyard Flatpaks

The Frostyard Flatpak remote. It publishes the Flatpaks of Frostyard's apps as
an OCI remote on GitHub Pages, built from the attested images in GHCR. It is
for people who install Frostyard apps on a Linux desktop.

Every listed image carries a build attestation from its app's release
workflow. An image without a valid one fails the publish run, so the remote
lists only what an app's CI built and signed.

**Status:** in production. It publishes from `main`.

**Documentation:** [docs/README.md](docs/README.md)

## What it publishes

| App | What it is | App ID | Remote |
| --- | --- | --- | --- |
| [Igloo](https://github.com/frostyard/igloo) | A terminal for nsl machines and containers | `org.frostyard.Igloo` | `frostyard` |
| [Frostnotes](https://github.com/frostyard/frostnotes) | Plain Markdown notes on your own computer | `org.frostyard.Frostnotes` | `frostyard` |
| [Frostmail](https://github.com/frostyard/frostmail) | Read and send mail on your own computer | `org.frostyard.Frostmail` | `frostyard` |
| [Frostread](https://github.com/frostyard/frostread) | Feeds and a reading list on your own computer | `org.frostyard.Frostread` | `frostyard` |

[`apps.json`](apps.json) is the list the build reads. It also keeps Igloo's
old app ID, `io.github.frostyard.Igloo`, so that installed copies move to the
new ID on `flatpak update`.

## Install

Add the remote once, for your user, then install the apps you want:

```sh
flatpak remote-add --user --if-not-exists frostyard https://frostyard.github.io/flatpak-index/frostyard.flatpakrepo
flatpak install --user frostyard org.frostyard.Igloo
flatpak install --user frostyard org.frostyard.Frostnotes
flatpak install --user frostyard org.frostyard.Frostmail
flatpak install --user frostyard org.frostyard.Frostread
```

Update them with `flatpak update`, like any other Flatpak. Apps use runtimes
from Flathub, such as the GNOME runtime. A `--user` install gets a missing
runtime only from a user remote, so add Flathub for your user too:

```sh
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
```

## Add a package

An app lists here when its release workflow pushes an OCI image to
`ghcr.io/frostyard/APP` on a `v*` tag and attests the digest, and someone adds
its repository and workflow path to [`apps.json`](apps.json).
[docs/runbooks/add-an-app.md](docs/runbooks/add-an-app.md) has the 5 steps,
the token that refreshes the remote after a release, and the staged test that a
suite desktop app needs first.

## How it works

An app's CI builds its Flatpak, exports it with `flatpak build-bundle --oci`
and attests the image digest. [`scripts/build-index.py`](scripts/build-index.py)
reads the images of every app in `apps.json` from GHCR, verifies each
attestation against the app's own release workflow, writes `index/static`, and
the [publish workflow](.github/workflows/publish.yml) deploys it to GitHub
Pages with [`frostyard.flatpakrepo`](frostyard.flatpakrepo). After each deploy
a job reads the live site back and installs the remote with a Flatpak client.
[docs/design/overview.md](docs/design/overview.md) has the flow, the
attestation rules and the schedule.

## Develop

```sh
mise install                                  # Python 3.12, from mise.toml and mise.lock
make ci                                       # what CI runs: the gate, make verify
python3 scripts/build-index.py --apps apps.json --site _site   # build the remote locally
```

The build runs `gh attestation verify`, so it needs `gh` signed in. The tests
need nothing else. [docs/design/overview.md](docs/design/overview.md) has the
way to run the live check by hand.

## Where to go next

- [AGENTS.md](AGENTS.md): the rules that agents and contributors must keep.
- [docs/README.md](docs/README.md): the design, the decisions and the runbooks.
- [SECURITY.md](SECURITY.md): report a vulnerability privately, and what gets
  fixes.
- [LICENSE](LICENSE): MIT.
