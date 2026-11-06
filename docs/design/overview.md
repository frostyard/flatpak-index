# The Frostyard Flatpak remote

Living document. Rationale: [ADR-0062](https://github.com/frostyard/core/blob/main/docs/adr/0062-make-desktop-suite-apps-standalone-flatpaks-with-opt-in-lodge.md).
Contracts: [`apps.json`](../../apps.json), [`frostyard.flatpakrepo`](../../frostyard.flatpakrepo).

## Overview

The repository publishes an OCI Flatpak remote on GitHub Pages, the format
Fedora's Flatpak registry uses. App repositories push their images and their
attestations to GHCR; this repository reads them anonymously, verifies them,
and writes the catalogue that `flatpak` reads. The approach follows
[tuna-os/flatpak-index](https://github.com/tuna-os/flatpak-index).

```
app release workflow ──► ghcr.io/frostyard/APP ──► build-index.py ──► index/static ──► Pages ──► flatpak client
        (attestation)                                  (verify)                      (live check)
```

## Design

**1. An app publishes an image.** Each app's CI builds its Flatpak, exports it
with `flatpak build-bundle --oci`, and pushes the image to
`ghcr.io/frostyard/APP`. The image carries the Flatpak ref, metadata,
AppStream data and icons as labels. The release workflow also attests the
image digest with `actions/attest-build-provenance`.

**2. The index verifies and lists.** This repository's
[publish workflow](../../.github/workflows/publish.yml) runs
[`scripts/build-index.py`](../../scripts/build-index.py). The script asks
GHCR, anonymously, for the images of every app in [`apps.json`](../../apps.json),
and for each tag it runs `gh attestation verify` on the digest. The index lists
an image only when all of these hold:

- The attestation comes from the release workflow that `apps.json` names in
  its `"workflow"` key, in the app's own repository.
- Its certificate names a `v*` tag as the source ref, and the same tag in the
  signer URI.
- It was built on a GitHub-hosted runner: the command passes
  `--deny-self-hosted-runners`.

Every manifest and config blob must also match its sha256 digest, so the
labels come from the verified image. The script writes the verified images,
and the landing page, to `index/static`, and copies
[`frostyard.flatpakrepo`](../../frostyard.flatpakrepo) beside them, from the
directory of the `--apps` file. The deploy job publishes that directory to
GitHub Pages.

**3. A user installs.** `flatpak remote-ls` and `flatpak install` fetch
`index/static`, then pull the image from GHCR. GHCR wants a token even for
public images, which the `org.flatpak.Authenticator.Oci` authenticator named
in the `.flatpakrepo` file fetches.

**What is skipped.** An image that is missing, private or not a Flatpak is
skipped, with a warning; the Flatpak check comes after the attestation check.
A digest without a valid attestation is not a skip: the run fails and nothing
is deployed, so the last deployed site stays live.

**End-of-life rebases.** An image can mark its ref as end-of-life in favour of
another ref (`flatpak build-commit-from --end-of-life-rebase`), so that
`flatpak update` moves users to a new app ID. The script lists such an image
only when the index also lists the ref it rebases to, and repeats that check so
that a chain of rebases ends at a listed ref. One run can read the old `latest`
and the new end-of-life tag, because the app moves them one after the other;
that run skips the end-of-life image and keeps the old one, and the next run
lists both. [`docs/runbooks/rename-an-app-id.md`](../runbooks/rename-an-app-id.md)
has the procedure.

**The staged catalogue.** The same script builds a catalogue for a test on a
real desktop, with a digest as the tag. That catalogue is served on the test
machine only and is never deployed: only `apps.json` is published.
[`docs/runbooks/stage-a-candidate.md`](../runbooks/stage-a-candidate.md) has the
procedure.

## Operational notes

**The live check.** After each deploy, the `verify` job fetches
`frostyard.flatpakrepo` and `index/static` from the live site, compares them
with the deployed artifact, and retries for up to 15 minutes because Pages
caches a file for 10 minutes. Then it adds the remote with
`flatpak remote-add --user` in a temporary folder and checks that
`flatpak remote-ls --user --all` lists each built ref with its digest. It reads
`--all` because `remote-ls` hides end-of-life refs without it. A red verify job
does not undo the deploy.

**When it runs.** The workflow runs on every push to `main`, on a schedule,
and when an app dispatches it after publishing. Because it reads public images,
app repositories need no credentials for this one.

The schedule asks for every hour (`17 * * * *`), but GitHub delays and drops
scheduled runs. From 6 to 31 October 2026 it ran 124 times, 3 to 7 times a day,
with a median gap of 5 hours and a longest gap of 10.6 hours. Only the dispatch
publishes a release at once; the schedule is a fallback that can take most of a
working day.

GitHub stops scheduled workflows in a public repository after 60 days without
activity; a push or a manual run restarts them.

**Running the live check by hand.** Take the deployed artifact of a publish run
and compare it with the live site. Run this from the repository root, with
`RUN` as the run's ID:

```sh
gh run download RUN -R frostyard/flatpak-index -n github-pages
mkdir -p _built && tar -xf artifact.tar -C _built
python3 scripts/check-live.py --site _built --url https://frostyard.github.io/flatpak-index/
```

The check needs `flatpak`. It does not change your own Flatpak setup: it adds
the remote with `XDG_DATA_HOME`, `XDG_CONFIG_HOME` and `XDG_CACHE_HOME` set to
a temporary folder, and deletes that folder after each attempt. `--timeout` and
`--interval` change how long it retries and how long it waits between attempts.
`.gitignore` ignores `artifact.tar` and `_built/`.

## References

- Rationale: [core ADR-0062](https://github.com/frostyard/core/blob/main/docs/adr/0062-make-desktop-suite-apps-standalone-flatpaks-with-opt-in-lodge.md)
- Contracts: [`apps.json`](../../apps.json), [`frostyard.flatpakrepo`](../../frostyard.flatpakrepo)
- Procedures: [add an app](../runbooks/add-an-app.md), [stage a candidate](../runbooks/stage-a-candidate.md), [rename an app ID](../runbooks/rename-an-app-id.md)
- Org decisions: [`docs/org-adrs.md`](../org-adrs.md)
