# Roadmap

The project is being extracted from a working local experiment in small phases. Each phase should be independently reviewable and keep the README accurate about what is available. These milestones describe scope, not delivery dates.

## Phase 1 — Project foundation

- [x] Explain the problem, goals, intended architecture and current repository status.
- [x] Establish contribution, conduct, security reporting and licensing documents.
- [x] Add issue and pull request templates and repository formatting defaults.
- [x] Define the phases before importing runtime code.

## Phase 2 — Local helper lifecycle

- [ ] Introduce the helper and worker bootstrap adapter without importing generated lab state.
- [ ] Define the authenticated API, ownership records, retry behavior and deletion contract.
- [ ] Document the trusted local execution boundary and bootstrap limitations.
- [ ] Bring over meaningful unit tests using a fake Lima executable.
- [ ] Add automated checks for the code introduced in this phase.

Exit criterion: helper behavior can be exercised without starting real VMs or requiring Platform.

## Phase 3 — Platform integration

- [ ] Add the OpenTofu node module and NodeProvider manifest source.
- [ ] Add preparation, helper startup, health checks, deployment and status commands.
- [ ] Make paths, Platform access, worker sizing and capacity settings configurable.
- [ ] Generate private credentials and state outside tracked source files.
- [ ] Add a tenant template and an installation guide with explicit prerequisites.
- [ ] Add an integration test covering API authentication, create/read, recovery and destroy.

Exit criterion: a fresh installation can provision one real worker and remove it through Platform's managed lifecycle.

## Phase 4 — Reproducible autoscaling example

- [ ] Add a workload whose requests demonstrate worker scale-up.
- [ ] Document two independent tenants sharing the same Mac capacity budget.
- [ ] Record readiness, worker removal and actual Lima VM deletion.
- [ ] Explain the three capacity controls: tenant pool, NodeProvider and helper.
- [ ] Document download delays, bootstrap failures and the prototype's occupied-worker consolidation limitation.
- [ ] Document teardown before Platform removal and recovery from interrupted runs.

Exit criterion: another person can reproduce creation, scale-up, scale-down and cleanup on a clean supported environment.

## Phase 5 — Experimental release

- [ ] Publish a tested compatibility matrix and remaining limitations.
- [ ] Improve provisioning progress and actionable failure diagnostics.
- [ ] Verify interrupted bootstrap, missing-VM recovery, helper restarts and ownership preservation.
- [ ] Review the helper's authentication and handling of registration credentials.
- [ ] Establish release notes, versioning and a tagged experimental `v0.1.0` release.
- [ ] Publish a short demo and portfolio page linking to installation and source.

Exit criterion: a tagged release has reproducible installation and cleanup instructions, release checks and an explicit experimental support scope.

## Later candidates

Additional worker shapes, resource-aware host budgeting, Linux support, multiple hosts and alternative Lima management backends can be discussed after the first workflow is reproducible. They are not part of the initial release commitment.

A phase may need multiple commits or pull requests. Keep unrelated changes separate rather than treating the suggested commit titles as a requirement to bundle everything.
