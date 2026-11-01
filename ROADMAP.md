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
- [x] Document two independent tenants sharing the same Mac capacity budget.
- [x] Record readiness, worker removal and actual Lima VM deletion.
- [x] Explain the three capacity controls: tenant pool, NodeProvider and helper.
- [x] Document download delays, bootstrap failures and the prototype's occupied-worker consolidation limitation.
- [x] Document teardown before Platform removal and recovery from interrupted runs.

Exit criterion met by the documented two-tenant exercise and a live run on 2026-11-01: the source workload scaled from two to four Ready workers, returned to two through automatic empty-worker deletion, then tenant teardown removed the remaining VMs. See the [walkthrough](examples/autoscaling/README.md) and [validation record](docs/autoscaling-validation.md) for the tested environment, observations, cleanup and limits.

## Phase 5 — Platform screenshots and project showcase

Capture real resources created with this repository's [two-tenant example](examples/autoscaling/README.md), using consistent tenant and VM names throughout the screenshots.

- [x] Add named screenshot placeholders, captions and display locations across the README, installation guide and [autoscaling walkthrough](examples/autoscaling/README.md).
- [ ] Capture the Lima NodeProvider in Platform's NodeProviders section, showing its availability and worker node type.
- [ ] Capture the Default project with both testing tenants visible and running.
- [ ] For each tenant, capture the Nodes section with the joined Lima workers and their Ready status.
- [ ] For each tenant, capture the Deployments section with `lima-scale-test` and its desired, Ready and available replica counts.
- [ ] For each tenant, capture the Pods section with the nginx replicas, readiness and associated worker names visible.
- [ ] Capture a tenant overview or graph showing the hosted control plane, Auto Nodes workers and provider association.
- [ ] Capture the tenant template's dynamic pool configuration and explain the worker profile, per-tenant limits and shared provider/helper cap alongside it.
- [ ] Capture NodeClaims during provisioning and after they become available, correlating claims with the resulting tenant workers and Lima VMs.
- [ ] Capture matching before-and-after views of scale-up: one replica and worker per tenant, then two replicas and workers per tenant.
- [ ] Capture `limactl list` on the Mac with VM names matching the workers shown in Platform.
- [ ] Capture scale-down evidence showing the retired workers and claims are gone, with a matching Mac inventory confirming their VMs were deleted.
- [ ] Capture final teardown evidence showing the test tenants and their managed VMs have been removed.
- [ ] Store images under `docs/images/screenshots/` with descriptive filenames, useful alt text and captions stating what each image demonstrates. Record the capture date and tested versions.
- [ ] Keep credentials, kubeconfig contents and bootstrap logs out of captures. Crop unrelated resources so the screenshots focus on this project's lifecycle.
- [ ] Add a concise screenshot showcase to the README and use the autoscaling walkthrough for the complete capture sequence.

Exit criterion: readers can visually follow provider setup, two-tenant provisioning, worker readiness, workload placement, scale-up, automatic VM removal and teardown using screenshots from a real run. The views agree on tenant, claim and VM identities and make the project's local autoscaling behavior easy to assess.

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
- [ ] Repeat the two-tenant exercise on the reference Linux host: fresh setup, Ready workers, nginx placement, dynamic scale-up, empty-worker scale-down and actual VM deletion during teardown.
- [ ] Include Linux service definitions, configuration, examples and tests in this repository. Document Linux prerequisites, installation, troubleshooting and cleanup without requiring files or custom resources from another project.
- [ ] Update platform-specific names and documentation when Linux support is implemented. Publish the tested host/architecture/backend combinations and keep unsupported combinations explicit.

Exit criterion: a developer on the documented Linux reference host can install this repository's provider, reproduce the two-tenant autoscaling example and confirm managed VM cleanup. Linux helper restarts preserve ownership, macOS regression checks pass and the compatibility record distinguishes real VM validation from offline tests. Linux support remains planned until these checks pass.

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
