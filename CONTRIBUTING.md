# Contributing

Thank you for helping improve Lima NodeProvider for vCluster. Documentation corrections, bug reports, design feedback and focused pull requests are welcome.

## Start here

Read the [README](README.md), [roadmap](ROADMAP.md) and [Code of Conduct](CODE_OF_CONDUCT.md). The repository contains the helper, guest bootstrap adapter, Platform integration and setup tooling. Read the [installation guide](docs/installation.md) before testing against real VMs.

Use this repository's issue templates for bugs and feature requests. For a substantial change, describe the problem and proposed scope in an issue before implementing it. Sensitive reports belong in the process described in [SECURITY.md](SECURITY.md).

## Small changes and clear commits

- Keep each pull request focused on one problem or roadmap milestone.
- Describe the trigger, expected behavior, proposed change and how it was checked.
- Preserve existing attribution when moving or adapting code; record dependency licenses when introducing them.
- Keep source changes separate from generated files, credentials and local experiment state.
- Update documentation when user-visible behavior or configuration changes.

Use descriptive commit subjects. This project uses prefixes such as `docs:`, `feat:`, `fix:`, `test:` and `chore:`. Example: `docs: explain local worker capacity limits`. These are project conventions, not a claim that an automated commit check or upstream CLA process is configured.

## Engineering standards

For runtime code:

- Keep Platform integration, VM lifecycle operations and guest bootstrap responsibilities explicit.
- Keep provider manifests, OpenTofu modules, bootstrap code and examples in this repository. Installation must not depend on files or pre-existing custom resources from a separate lab or project. Declare upstream tools and services as prerequisites.
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

Run the offline checks from the repository root with Python 3.11 or newer:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/check_repository.py
```

The suite uses a fake executable and mocked guest operations. It requires no Lima installation, Platform access or external Python packages. The test workflow runs this suite on Python 3.11 through 3.14; a configured workflow is not evidence that a remote run has passed. See [local helper validation](docs/local-helper.md#try-the-offline-workflow) for the boundaries of these checks.

## Testing the OpenTofu integration

Install OpenTofu or Terraform 1.6 or newer plus OpenSSL, then run:

```bash
python3 tests/integration_platform.py
# Select a specific binary if both are installed:
python3 tests/integration_platform.py --terraform /path/to/tofu
```

This test opens a temporary loopback HTTPS listener and downloads the pinned Mastercard REST client. It exercises certificate verification, authentication, configurable sizing, UID-free Platform input, create/read, a clean plan, missing-VM repair and destroy using fake Lima. Credentials and OpenTofu state are temporary. It never contacts Platform or starts real VMs. The test workflow runs it with OpenTofu 1.10.6; local runs may use either supported CLI.

## Running lint and security checks

The offline tests need no external Python packages. Lint and security checks use the optional tools pinned in [requirements-dev.txt](requirements-dev.txt). Install them in a local virtual environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m ruff check helper tests scripts
.venv/bin/python scripts/check_repository.py
.venv/bin/python -m bandit -r helper -ll
```

Ruff checks Python errors and unused code. The repository checker validates syntax, local Markdown links, image references and basic formatting. Bandit scans `helper/` for medium- and high-severity findings; tests are outside that runtime scan. Static scanning does not validate the whole trust model or replace review of the trusted guest bootstrap boundary.

Separate [test](.github/workflows/test.yml), [lint](.github/workflows/lint.yml) and [security](.github/workflows/security.yml) workflows run on pull requests, pushes to `main` and manual dispatch. They do not provision VMs or use Platform credentials. Automated acceptance testing of real workers remains planned; the opt-in manual workflow is documented below. See the [roadmap](ROADMAP.md) for release work.

The README's workflow badges follow [GitHub's status badge format](https://docs.github.com/en/actions/how-tos/monitor-workflows/add-a-status-badge). Each badge links to this repository's corresponding workflow and displays the result for `main`. Adding a badge does not create a check or make it pass; a workflow must run on GitHub first. The gray acceptance badge links to the roadmap and does not claim a successful run.

## Validating the autoscaling example

The [two-tenant walkthrough](examples/autoscaling/README.md) is an opt-in live test. It creates real tenants and up to four Lima workers, so review the worker profile and shared capacity budget before running it. Keep credentials under ignored `.local/` and remove workers through Platform before stopping the helper or removing the provider.

When recording results, include application readiness, Node and NodeClaim counts, ownership records and actual VM removal. Follow the structure in the [autoscaling validation record](docs/autoscaling-validation.md); do not publish kubeconfigs, state files or bootstrap logs. Documentation of a live test does not establish that GitHub acceptance automation exists.

## Opening a pull request

Explain what changed, why it matters, which roadmap goal it supports and how it was validated. Call out limitations or configuration migration where relevant. The pull request template provides space for that evidence.

Review is performed by the repository maintainer. No response-time commitment or contributor agreement bot is configured as part of the initial foundation.

## Community conventions and references

The documentation structure and contribution approach were informed by these public projects:

- [vCluster](https://github.com/loft-sh/vcluster) and its [contribution guide](https://github.com/loft-sh/vcluster/blob/main/CONTRIBUTING.md): explicit contribution guidance, validation and Apache-2.0 licensing.
- [vCluster Auto Nodes Azure](https://github.com/loft-sh/vcluster-auto-nodes-azure) and [GCP](https://github.com/loft-sh/vcluster-auto-nodes-gcp): provider-focused overviews, prerequisites, configuration and limitations.
- [vind](https://github.com/loft-sh/vind): clear project scope, related projects, contribution entry points and support links.

This repository uses its own support channels and community identity. It does not imply official upstream maintenance or copy upstream claims about features, support, or licensing entitlements.

## License

Contributions are accepted under the repository's [Apache-2.0 license](LICENSE). Contributors retain copyright in their contributions.
