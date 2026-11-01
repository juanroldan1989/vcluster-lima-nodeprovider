# Autoscaling validation record

This record accompanies the [two-tenant walkthrough](../examples/autoscaling/README.md). It describes a manual live test of this repository. Hosted acceptance automation and a supported version matrix remain release work.

## Environment

| Component | Test configuration |
| --- | --- |
| Test date | 2026-11-01 |
| Host | macOS ARM64 |
| Lima | 2.1.0 |
| vCluster Platform | 4.12.4 |
| Tenant chart | 0.37.1 |
| vCluster CLI | 0.37.4 |
| Runtime source base | `d3fcd71`, with the new example workload. |
| Workload | [workload.yaml](../examples/autoscaling/workload.yaml), nginx with a `600m` CPU request per replica. |
| Worker profile | 1 CPU / 2Gi RAM / 30Gi virtual disk. |
| Tenant pool budget | Two workers / 2 CPUs / 4Gi RAM per tenant. |
| Shared provider and helper budget | Four workers. |

The test used provider `lima-autoscaling`, template `private-nodes-lima-autoscaling` and helper port 18445 with separate private state. The tenants were `lima-demo-a` and `lima-demo-b` in project `default`. Two manually managed VMs remained outside the test lifecycle.

## Observations

**Result: passed.** Both tenants scaled from one replica to two, then returned to one replica with one Ready worker each. The two unused Lima VMs were deleted automatically. The zero-replica fallback was not needed.

| Observation | Observed at | Ready replicas A / B | Ready workers A / B | Platform claims | Ownership records | Managed VMs |
| --- | --- | --- | --- | --- | --- | --- |
| Baseline | 254s | 1 / 1 | 1 / 1 | 2 | 2 | 2 |
| Scaled up | 456s | 2 / 2 | 2 / 2 | 4 | 4 | 4 |
| Scaled down | 563s | 1 / 1 | 1 / 1 | 2 | 2 | 2 |
| Tenant teardown | 596s | — | 0 / 0 | 0 | 0 | 0 |

Times are measured from the start of the validation run. Observations were sampled roughly every 20 seconds, so they are not exact creation or deletion timestamps. In this run, the scale-down checks converged about 107 seconds after the scale-down request; `consolidateAfter: 1m` was not a one-minute deletion deadline.

## Worker-to-VM lifecycle

| Tenant | Platform NodeClaim | Lima VM | Observed removal |
| --- | --- | --- | --- |
| `lima-demo-a` | `lima-demo-a-j2w5k` | `vclima-61855a4175a7ebea41fe6c03` | Retained at one replica; removed when its tenant was deleted. |
| `lima-demo-a` | `lima-demo-a-mttqb` | `vclima-71d40ce52f933c87315dc5aa` | Removed automatically after scaling to one replica. |
| `lima-demo-b` | `lima-demo-b-5h8c5` | `vclima-45154b4e6c7550325a11bfbd` | Removed automatically after scaling to one replica. |
| `lima-demo-b` | `lima-demo-b-xmr98` | `vclima-dcc92997f31e3da5ecd3dd9c` | Retained at one replica; removed when its tenant was deleted. |

For scale-down, the test confirmed removal of each retired Node, its Platform claim and its helper ownership record. It also checked that each exact VM name was absent from Lima inventory. VM deletion occurred through the managed lifecycle.

## Final cleanup

- Deleted only `lima-demo-a` and `lima-demo-b` through Platform.
- Confirmed their remaining claims, ownership records and VMs were removed.
- Removed the test template, NodeProvider and credential Secret after worker cleanup.
- Stopped the test helper after all managed records were gone.
- Left the two manually managed Lima VMs outside the test lifecycle.

The workload manifest passed Kubernetes server-side dry-run validation in both tenants before deployment. The repository's 52 offline tests and documentation checks also passed.

## Evidence and scope

The test checks application Ready replica counts, tenant Node readiness, Platform NodeClaims, helper ownership records and actual Lima VM inventory. Credential files, OpenTofu state, join payloads and guest logs are excluded from this public record.

The helper serializes provisioning operations. Timing from one run does not establish a provisioning guarantee. System Pods and disruption constraints can retain workers; empty-worker deletion does not demonstrate successful repacking of occupied workers. See the [walkthrough's consolidation limitation](../examples/autoscaling/README.md#if-an-occupied-worker-remains).
