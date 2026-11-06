# Runbook: add an app

A new app lists in the remote when its release workflow publishes an attested
OCI image to GHCR and this repository names it in
[`apps.json`](../../apps.json). The flow is in
[`docs/design/overview.md`](../design/overview.md).

1. In the app's repository, build the Flatpak in CI and push its OCI image to
   `ghcr.io/frostyard/APP:latest`, from a workflow that runs on a `v*` tag.
   [Igloo's workflow](https://github.com/frostyard/igloo/blob/main/.github/workflows/flatpak.yml)
   shows the build, the export with `flatpak build-bundle --oci`, and the push.
2. In that workflow, attest the pushed image digest with
   `actions/attest-build-provenance` and `subject-digest`. The job needs the
   permissions `id-token: write` and `attestations: write`.
3. Make the package public: on the package's page in the
   [organization's packages](https://github.com/orgs/frostyard/packages),
   open *Package settings* and change its visibility. Private images are
   skipped, because Flatpak clients cannot pull them.
4. Add the repository to [`apps.json`](../../apps.json) and push. Name the
   release workflow's path in `"workflow"`, for example
   `".github/workflows/flatpak.yml"`. A digest without a valid attestation
   from that workflow stops the whole deploy.
5. Let the app refresh this remote right after it publishes: create a
   fine-grained token with *Actions: read and write* on this repository only,
   and save it in the app repository as the secret
   `FLATPAK_INDEX_DISPATCH_TOKEN`. The app's publish or promote step runs
   `gh workflow run publish.yml --repo frostyard/flatpak-index` with it, and
   skips that step when the secret is empty. Without it, only the schedule
   picks up new images, often hours later
   ([`docs/design/overview.md`](../design/overview.md) has the schedule facts).

A suite desktop app lists only after a test on a real desktop installed its
attested digest from a staged catalogue (core ADR-0062):
[`stage-a-candidate.md`](stage-a-candidate.md) is that procedure.

The app should install `/app/share/metainfo/APP_ID.metainfo.xml` and its icons,
or software centres show a bare application ID.
