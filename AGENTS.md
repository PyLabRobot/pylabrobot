# Agent guide

Orientation for agents (and humans) working in this repo.

## Guides

- **Writing a device driver + its hello-world notebook:** [`docs/contributor_guide/device-driver-guide.md`](docs/contributor_guide/device-driver-guide.md)

## Working principles

- **Scope tightly.** Make the change that was asked for and nothing else — no drive-by rewrites of comments, constants, or logic, no unrequested docs/config side-quests.
- **Code documents what it does, not its history.** No diary/changelog comments ("NEW", "now", "previously"), no provenance/origin stories in code, commits, or PRs. Describe what the thing *is*.
- **Keep the repo permanent.** No one-time/backfill/migration scripts committed — run those ad-hoc.
- **Lint before shipping:** ruff + mypy (2-space indent), matching the repo's config.
- **Never drive real hardware without explicit, per-run approval.**
