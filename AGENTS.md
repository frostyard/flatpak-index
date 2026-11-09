# flatpak-index contributor context

This repository builds the Frostyard Flatpak remote: an OCI remote on GitHub
Pages that lists Frostyard's attested app images from GHCR. Start at
[docs/README.md](docs/README.md).

`AGENTS.md` is the canonical instruction file. `CLAUDE.md`, `GEMINI.md`,
`.cursorrules` and [`.github/copilot-instructions.md`](.github/copilot-instructions.md)
are relative symlinks to it (core ADR-0018, core ADR-0029). Edit only this
file.

## Layout

| Path | What it is |
| --- | --- |
| [`scripts/build-index.py`](scripts/build-index.py) | Builds `index/static` from the GHCR images of `apps.json`, verifies each attestation, and writes the landing page. |
| [`scripts/check-live.py`](scripts/check-live.py) | Reads the live site back after a deploy and installs the remote in a temporary Flatpak installation. |
| [`apps.json`](apps.json) | The apps the index lists, each with its release workflow path and its tags. |
| [`frostyard.flatpakrepo`](frostyard.flatpakrepo) | The remote file that users add. It is copied into the site as it is. |
| [`.github/workflows/publish.yml`](.github/workflows/publish.yml) | Runs the tests, builds the index, deploys it to Pages, then verifies the live site. |
| [`tests/`](tests/) | The unit tests: the build, the live check and the workflows. |
| [`docs/`](docs/README.md) | The design, the ADRs and the runbooks. |

## Gates

`make ci` is the gate; it runs `make verify`, which runs `make test`:
`python3 -m unittest discover -s tests -v`. The tests use the Python standard
library only (no PyYAML, which the test machine does not have).
`mise.toml` and `mise.lock` pin the tools (core ADR-0043); run `mise install`
first. CI runs `make ci` in [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

## Rules

- **The index lists only attested digests.** `verify_attestation` runs
  `gh attestation verify` with
  `--signer-workflow github.com/<repository>/<workflow>`, where the
  repository and the workflow come from [`apps.json`](apps.json), and with
  `--deny-self-hosted-runners`. It accepts a certificate only when its
  `sourceRepositoryRef` is a `refs/tags/v*` tag and its `buildSignerURI` is
  that workflow at that ref. A new app needs its release workflow path in
  `apps.json` for its images to list.
- **An unverified digest fails the whole run and nothing deploys.**
  `build_index` raises `Unverified`, `main` exits 1, and the `deploy` job has
  `needs: build`, so the last deployed site stays live. Never turn this into a
  skip or a warning.
- **Every manifest and config blob matches its sha256 digest**
  (`check_digest`), so the labels come from the verified image. Only an image
  that is missing, private or not a Flatpak is skipped, and the Flatpak check
  comes after the attestation check.
- **`apps.json` tags are `latest` first, then only old app IDs** for
  end-of-life rebases: `"tags": ["latest", "io.github.frostyard.Igloo"]`.
  `tests/test_build_index.py::test_apps_json_lists_only_latest_and_old_app_ids`
  enforces it. Never list a `v*` tag or a digest there; the staged catalogue
  is a local file, not `apps.json`.
- **An end-of-life image is listed only when the index also lists the ref it
  rebases to** (`drop_orphan_rebases`). A rebase chain ends at a listed ref.
- **Never run the publish workflow from an agent.** `publish.yml` runs on a
  push to `main`, on a schedule and on `workflow_dispatch`; its `deploy` job
  writes to GitHub Pages. A person starts it.
- **Every action is pinned to a full commit SHA with a `# vX.Y.Z` comment
  (a frostyard/core reusable workflow: `# frostyard/core main, YYYY-MM-DD`,
  because core has no release tags),
  every checkout sets `persist-credentials: false`, and every workflow has
  top-level `permissions: {}` or read-only permissions** (core ADR-0021).
  `tests/test_workflows.py` and `tests/test_baseline.py` check it.
- **A PR that touches a path in [`.github/risk-paths.json`](.github/risk-paths.json)
  gets 1 level label, `level:L1` to `level:L4`, and `risk:high` at L1 and L2**
  (core ADR-0068, ADR-0078). The owner reads each level as the risk-paths spec
  says: L1 is every line, L2 the whole diff, L3 a skim of the diff, L4 the title
  and CI. The org floors can raise that level, the workflow never lowers a label
  that the pull request already has, a missing level label only warns, and a
  missing `risk:high` label fails the job. The workflow removes
  `ready-for-human` when the head moves after the label, or when CI at the head
  is not green (core ADR-0077). The PR body names each matched path and why the
  change is safe.
- **A blocking review point adds a guard:** a test that fails without the fix,
  or a rule here when no test can catch it, plus 1 line in
  [`.memory/corrections.jsonl`](.memory/corrections.jsonl)
  ([`.memory/README.md`](.memory/README.md)).
- **No product change without its test.** `scripts/build-index.py`,
  `scripts/check-live.py`, `apps.json` and `frostyard.flatpakrepo` are the
  published contract; a change to them comes with a test in `tests/`.
- **PR text speaks for the repository.** A title, a body, a comment and a
  commit message give the reason in this repository's terms and link the ADR,
  the spec or the issue that applies.

## Documentation

`docs/README.md` is the index. A decision about this repository gets an ADR in
`docs/adr/`; an org-wide one goes to frostyard/core and is listed in
`docs/org-adrs.md`. The root `README.md` follows core's README spec (kind
`packaging`, at most 1000 words); the flow, the rules and the runbooks go in
`docs/`.
