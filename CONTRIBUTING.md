# Contributing

Thank you for helping improve Lima NodeProvider for vCluster. Documentation corrections, bug reports, design feedback and focused pull requests are welcome.

## Start here

Read the [README](README.md), [roadmap](ROADMAP.md) and [Code of Conduct](CODE_OF_CONDUCT.md). Phase 1 contains project documentation; no runtime setup, installer, or provider test suite is available in this repository yet.

Use this repository's issue templates for bugs and feature requests. For a substantial change, describe the problem and proposed scope in an issue before implementing it. Sensitive reports belong in the process described in [SECURITY.md](SECURITY.md).

## Small changes and clear commits

- Keep each pull request focused on one problem or roadmap milestone.
- Describe the trigger, expected behavior, proposed change and how it was checked.
- Preserve existing attribution when moving or adapting code; record dependency licenses when introducing them.
- Keep source changes separate from generated files, credentials and local experiment state.
- Update documentation when user-visible behavior or configuration changes.

Use descriptive commit subjects. This project uses prefixes such as `docs:`, `feat:`, `fix:`, `test:` and `chore:`. Example: `docs: explain local worker capacity limits`. These are project conventions, not a claim that an automated commit check or upstream CLA process is configured.

## Engineering standards

As runtime code is introduced:

- Keep Platform integration, VM lifecycle operations and guest bootstrap responsibilities explicit.
- Document API inputs, configuration defaults, lifecycle ownership and recovery behavior.
- Pass process arguments explicitly and validate identities before operating on VMs.
- Keep creation retries idempotent; delete only resources with recorded ownership.
- Treat worker sizing and capacity limits as configuration rather than user-specific constants.
- Keep status messages useful without exposing credentials or registration data.
- State tested platforms and versions; record known gaps instead of promising unverified compatibility.

Use plain language and omit the comma before the final "and" in lists. Preserve standard license text verbatim.

Formatting defaults are defined in [.editorconfig](.editorconfig). Use fenced code blocks with language labels, relative links for repository documents and a blank line around Markdown headings, lists and tables.

## Validation

For documentation-only changes:

1. Review the rendered Markdown, tables and diagrams.
2. Check that local links and heading anchors exist and commands match the current repository contents.
3. Run `git diff --check` from the repository root.

For runtime changes, include checks appropriate to the behavior being changed. Unit tests should use fake external commands where practical. Tests that create real VMs or touch a live cluster must be explicitly documented and opt-in. Add reproducible regression coverage for lifecycle bugs and distinguish VM creation, completed bootstrap and Kubernetes Node readiness.

Document test commands alongside the implementation when it is introduced. Until then, do not add successful-build badges or commands for nonexistent test suites.

## Opening a pull request

Explain what changed, why it matters, which roadmap phase it supports and how it was validated. Call out limitations or configuration migration where relevant. The pull request template provides space for that evidence.

Review is performed by the repository maintainer. No response-time commitment or contributor agreement bot is configured as part of the initial foundation.

## Community conventions and references

The documentation structure and contribution approach were informed by these public projects:

- [vCluster](https://github.com/loft-sh/vcluster) and its [contribution guide](https://github.com/loft-sh/vcluster/blob/main/CONTRIBUTING.md): explicit contribution guidance, validation and Apache-2.0 licensing.
- [vCluster Auto Nodes Azure](https://github.com/loft-sh/vcluster-auto-nodes-azure) and [GCP](https://github.com/loft-sh/vcluster-auto-nodes-gcp): provider-focused overviews, prerequisites, configuration and limitations.
- [vind](https://github.com/loft-sh/vind): clear project scope, related projects, contribution entry points and support links.

This repository uses its own support channels and community identity. It does not imply official upstream maintenance or copy upstream claims about features, support, or licensing entitlements.

## License

Contributions are accepted under the repository's [Apache-2.0 license](LICENSE). Contributors retain copyright in their contributions.
