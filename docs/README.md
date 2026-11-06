# Documentation

Docs are split by the question they answer:

| Directory | Question | Contents |
| --- | --- | --- |
| [adr/](adr/) | **Why** did we choose this? | Architecture Decision Records: immutable once accepted; superseded, never edited |
| [design/](design/) | **How** does it fit together? | Living documents describing the current architecture |
| [specs/](specs/) | **What exactly** is the contract? | Precise, testable interface definitions |
| [plans/](plans/) | **When/in what order** do we build? | Roadmaps and phase plans; updated as work lands |
| [runbooks/](runbooks/) | **What do I type now?** | Operations and development procedures |

## Index

### Org-wide decisions

- [org-adrs.md](org-adrs.md): the frostyard/core ADRs that bind this repository, and what each one binds here.

### Decisions (ADRs)

No local ADR yet. A decision about this repository gets an ADR here, with the
next free number, started from [adr/TEMPLATE.md](adr/TEMPLATE.md).

### Design

- [overview.md](design/overview.md): the flow from an app's image to a user's `flatpak install`, the attestation rules, the schedule facts and the live check. A new design doc starts from [design/TEMPLATE.md](design/TEMPLATE.md).

### Specs

No local spec yet. The published contracts are [`apps.json`](../apps.json) and
[`frostyard.flatpakrepo`](../frostyard.flatpakrepo); a spec starts from
[specs/TEMPLATE.md](specs/TEMPLATE.md) when one is needed.

### Plans

No local plan yet. Plans start from [plans/TEMPLATE.md](plans/TEMPLATE.md).

### Runbooks

- [add-an-app.md](runbooks/add-an-app.md): what an app repository does to list its Flatpaks in this remote.
- [stage-a-candidate.md](runbooks/stage-a-candidate.md): build a staged catalogue, install a release candidate on a test machine, and update it.
- [rename-an-app-id.md](runbooks/rename-an-app-id.md): move installed copies of an old app ID to the new one.

## Conventions

- **New docs start from their category's `TEMPLATE.md`** (in each directory).
- New decision → new ADR with the next number; if it reverses an old one, mark
  the old one `Superseded by NNNN` rather than editing it.
- Design docs are updated in place to always reflect reality.
- Specs change only alongside the code that implements them.
- Cross-links between categories are mandatory in both directions: see the
  documentation rules in [AGENTS.md](../AGENTS.md) (CLAUDE.md, GEMINI.md,
  `.cursorrules` and `.github/copilot-instructions.md` are symlinks to it,
  core ADR-0018).
- Adding a doc means adding it to the index above.
- The root [README.md](../README.md) is the front page. It follows core's
  [README spec](https://github.com/frostyard/core/blob/main/docs/specs/readme.md)
  (kind `packaging`, at most 1000 words); the details live here.
