# Security policy

flatpak-index is a public repository. It publishes the Frostyard Flatpak
remote at `https://frostyard.github.io/flatpak-index/`, which every Frostyard
Flatpak user installs apps from.

## Reporting a vulnerability

Use GitHub private vulnerability reporting:
https://github.com/frostyard/flatpak-index/security

Open the Security tab and click **Report a vulnerability**.

Do not open a public issue with the details of a vulnerability. Do not post
credentials in an issue, a pull request or a discussion.

Tell us what you can: the affected commit, what an attacker can do, and the
smallest way to reproduce it. We will answer with what we found and when a
fix lands.

If a credential may be exposed, say so in the first line.

## Supported versions

The repository makes no releases. `main` is what runs, and fixes land on
`main`; only the current `main` gets fixes. The remote that users have added
changes with each deploy, so an installed app comes from its own repository's
release and is supported by that repository.

## Scope

In scope:

- the attestation check in [`scripts/build-index.py`](scripts/build-index.py):
  which signer workflows, tags and runners it accepts, and the digest check of
  every manifest and config blob;
- the trust list in [`apps.json`](apps.json): which repositories and signer
  workflows the index reads images from;
- the publish workflow and its token permissions: the build, the GitHub Pages
  deploy and the live check in
  [`.github/workflows/publish.yml`](.github/workflows/publish.yml);
- [`frostyard.flatpakrepo`](frostyard.flatpakrepo): the remote URL and the
  authenticator that users install;
- the live remote itself: `index/static`, the landing page and the paths under
  `https://frostyard.github.io/flatpak-index/`;
- the dispatch token that an app repository holds as
  `FLATPAK_INDEX_DISPATCH_TOKEN`.

Out of scope: an app's own bugs and the contents of its image. Report those to
the app's repository.
