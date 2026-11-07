# Roadmap

The project is being extracted from a working local experiment in small phases. Each phase should be independently reviewable and keep the README accurate about what is available. These milestones describe scope, not delivery dates.

## Phase 1 — Project foundation

- [x] Explain the problem, goals, intended architecture and current repository status.
- [x] Establish contribution, conduct, security reporting and licensing documents.
- [x] Add issue and pull request templates and repository formatting defaults.
- [x] Define the phases before importing runtime code.

## Phase 2 — Local helper lifecycle

- [x] Introduce the helper and worker bootstrap adapter without importing generated lab state.
- [x] Define the authenticated API, ownership records, retry behavior and deletion contract.
- [x] Document the trusted local execution boundary and bootstrap limitations.
- [x] Bring over meaningful unit tests using a fake Lima executable.
- [x] Add automated checks for the code introduced in this phase.

Exit criterion met by the offline suite: helper behavior can be exercised without starting real VMs or requiring Platform. See the [local helper guide](docs/local-helper.md) for coverage and limits.

## Phase 3 — Platform integration

- [x] Add the OpenTofu node module and NodeProvider manifest source.
- [x] Add preparation, helper startup, health checks, deployment and status commands.
- [x] Make paths, Platform access, worker sizing and capacity settings configurable.
- [x] Generate private credentials and state outside tracked source files.
- [x] Add a tenant template and an installation guide with explicit prerequisites.
- [x] Add an integration test covering API authentication, create/read, recovery and destroy.

Exit criterion met by a manual smoke test on 2026-11-01: a fresh preparation provisioned one real Ready worker, then Platform deletion removed its VM and ownership record. See the [validation record](docs/installation.md#validation-record). Automated live acceptance remains a later milestone.

## Phase 4 — Reproducible autoscaling example

- [x] Add a workload whose requests demonstrate worker scale-up.
- [x] Document a tenant scaling within its pool limits and the shared Mac capacity budget.
- [x] Record readiness, worker removal and actual Lima VM deletion.
- [x] Explain the three capacity controls: tenant pool, NodeProvider and helper.
- [x] Document download delays, bootstrap failures and occupied-worker consolidation constraints.
- [x] Document teardown before Platform removal and recovery from interrupted runs.

Exit criterion met by live validation: the current [tenant autoscaling example](examples/autoscaling/README.md) demonstrates one to two Ready workers, automatic consolidation back to one and managed VM cleanup. The [validation record](docs/autoscaling-validation.md) documents the observed environment, results and earlier shared-capacity validation.

## Phase 5 — Platform screenshots and project showcase

The current gallery covers **tenant A on 2026-11-07**, including intermediate provisioning states, VM inventory, cleanup and a provider-removal recording. The [tenant autoscaling example](examples/autoscaling/README.md) follows the same tenant through setup, scale-up, scale-down and teardown.

- [x] Replace the screenshot callouts with reviewed images, descriptions and recording links across the README, installation guide and autoscaling walkthrough.
- [x] Show the Lima provider Available with zero and one claimed worker, including its node type.
- [x] Capture tenant A creation, Running state, graph, Ready workers, nginx Deployments and Pod placement.
- [x] Capture tenant A scale-up from one Ready replica and worker to two, including Pending and joining states.
- [x] Capture the template catalog and explain worker sizing, tenant limits and shared provider capacity.
- [x] Show two-worker VM inventory after scale-up and one-worker inventory after scale-down.
- [x] Capture tenant termination, its absence from the Default project and an empty Lima inventory.
- [x] Add the original provider-removal recording, an MP4 playback copy, a linked preview and an extracted final frame.
- [x] Catalog the supplied assets, record dates and versions, add captions and review the captures for credentials or registration data.
- [ ] Capture current template configuration with the canonical type selector visible.
- [ ] Add dedicated Platform NodeClaim and helper-status captures correlating claims, ownership records and VMs.
- [ ] Add a consistent tenant scale-down sequence with matched before/after worker and VM identities.

Exit criterion: readers can visually follow provider setup, tenant provisioning, worker readiness, workload placement, automatic VM removal and teardown. The gallery is integrated; the remaining detailed configuration and lifecycle views above complete the showcase.

## Phase 6 — Linux host support

Make the same NodeProvider lifecycle available to developers using Linux hosts. Lima already supports [Linux installation](https://lima-vm.io/docs/installation/) and uses [QEMU as its default Linux driver](https://lima-vm.io/docs/config/vmtype/qemu/). This project's current implementation still assumes macOS VZ, ARM64 workers and launchd service management.

- [ ] Define an initial reference environment: an explicitly versioned Ubuntu LTS host on x86_64. Record kernel, Lima and QEMU versions; add other distributions or Linux ARM64 only after separate validation.
- [ ] Detect the host OS and CPU architecture, then select or configure an appropriate VM driver and guest architecture. Preserve the existing macOS VZ workflow.
- [ ] Support Linux QEMU workers with documented KVM prerequisites, executable discovery and permissions. Fail clearly when required virtualization capabilities are unavailable.
- [ ] Select matching guest images and align bootstrap inputs with NodeProvider architecture properties instead of hardcoding `aarch64` and `kubernetes.io/arch: arm64`.
- [ ] Add Linux helper lifecycle commands backed by a systemd user service, with a documented foreground mode. Retain launchd support on macOS and preserve private state across helper restarts.
- [ ] Make Platform-to-helper connectivity work with Linux container networking without assuming Docker Desktop's `host.docker.internal`. Validate the configured endpoint, certificate trust and guest-to-Platform registration path.
- [ ] Validate Lima VM-to-VM networking and tenant worker VPN connectivity with multiple workers on Linux.
- [ ] Preserve the same tenant pool, NodeProvider and helper capacity controls, ownership checks, retry behavior and managed deletion contract on both operating systems.
- [ ] Add platform-selection and service-management regression tests. Keep offline checks on Linux and macOS, then add opt-in real-worker validation on a Linux runner with the required virtualization capabilities.
- [ ] Repeat the tenant autoscaling example on the reference Linux host: fresh setup, Ready workers, nginx placement, dynamic scale-up, empty-worker scale-down and actual VM deletion during teardown.
- [ ] Include Linux service definitions, configuration, examples and tests in this repository. Document Linux prerequisites, installation, troubleshooting and cleanup without requiring files or custom resources from another project.
- [ ] Update platform-specific names and documentation when Linux support is implemented. Publish the tested host/architecture/backend combinations and keep unsupported combinations explicit.

Exit criterion: a developer on the documented Linux reference host can install this repository's provider, reproduce the tenant autoscaling example and confirm managed VM cleanup. Linux helper restarts preserve ownership, macOS regression checks pass and the compatibility record distinguishes real VM validation from offline tests. Linux support remains planned until these checks pass.

## Phase 7 — Experimental release

- [ ] Publish a tested compatibility matrix and remaining limitations.
- [ ] Improve provisioning progress and actionable failure diagnostics.
- [ ] Verify interrupted bootstrap, missing-VM recovery, helper restarts and ownership preservation.
- [ ] Review the helper's authentication and handling of registration credentials.
- [ ] Establish release notes, versioning and a tagged experimental `v0.1.0` release.
- [ ] Publish a short demo and portfolio page linking to installation and source.

Exit criterion: a tagged release has reproducible installation and cleanup instructions, release checks and an explicit experimental support scope.

## Later candidates

Multiple worker shapes per provider, resource-aware host budgeting, multiple hosts and alternative Lima management backends can be discussed after the first workflow is reproducible. They are not part of the initial release commitment.

A phase may need multiple commits or pull requests. Keep unrelated changes separate rather than treating the suggested commit titles as a requirement to bundle everything.
