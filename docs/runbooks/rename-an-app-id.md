# Runbook: rename an app ID

When an app changes its Flatpak ID, installed copies of the old ID move to the
new one on `flatpak update`, through an end-of-life rebase. A Flatpak's
application ID is `org.frostyard.<AppName>`
([core ADR-0016](https://github.com/frostyard/core/blob/main/docs/adr/0016-reverse-dns-org-frostyard-identifiers.md)).

1. The app's release workflow moves `latest` to the new ID. Then it builds an
   image of the old ID with
   `flatpak build-commit-from --end-of-life-rebase=OLD_ID=NEW_ID`, attests it,
   and tags it with the old ID, for example
   `ghcr.io/frostyard/igloo:io.github.frostyard.Igloo`.
2. Its `apps.json` entry lists that tag after `latest`, for example
   `"tags": ["latest", "io.github.frostyard.Igloo"]`. A tag that does not
   exist yet is skipped, so this change can merge before the app's first
   release with the new ID.

The build lists an end-of-life image only when the index also lists the ref
that it rebases to. One run can read the old `latest` and the new end-of-life
tag, because the app moves them one after the other; that run skips the
end-of-life image and keeps the old one, and the next run lists both.

The verify job lists the live remote with `flatpak remote-ls --all`, because
`remote-ls` hides end-of-life refs without it.
