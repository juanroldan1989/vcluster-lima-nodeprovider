# Autoscaling validation record

This record accompanies the [two-tenant walkthrough](../examples/autoscaling/README.md). It describes the original two-tenant live test and a later [occupied-worker consolidation regression](#occupied-worker-consolidation-regression). Hosted acceptance automation and a supported version matrix remain release work.

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

## Screenshot references

The [autoscaling walkthrough](../examples/autoscaling/README.md#screenshot-capture-metadata) contains the visual evidence slots and capture metadata for a run of the same workflow. Captures are pending. Record their actual date, versions and resource identities; later screenshots should not be presented as images of the historical run measured above.

## Evidence and scope

The test checks application Ready replica counts, tenant Node readiness, Platform NodeClaims, helper ownership records and actual Lima VM inventory. Credential files, OpenTofu state, join payloads and guest logs are excluded from this public record.

The helper serializes provisioning operations. Timing from one run does not establish a provisioning guarantee. The 2026-11-01 run demonstrated empty-worker removal; the focused regression below separately verifies occupied-worker consolidation. See [scale-down troubleshooting](troubleshooting.md#if-an-occupied-worker-remains) for type-label migration and disruption constraints.

## Occupied-worker consolidation regression

**Test date:** 2026-11-07. **Result: passed.**

| Component | Tested configuration |
| --- | --- |
| Host | macOS ARM64, Lima 2.1.0 |
| vCluster Platform | 4.12.4 |
| Tenant chart / Kubernetes | 0.37.4 / v1.36.5 |
| Tenant / project | `lima-demo-a` / `default` |
| Provider / pool | `lima-local` / `lima-workers-dynamic` |
| Worker profile | 1 CPU / 2Gi RAM / 30Gi disk |
| Shared / tenant caps | Four / two workers |
| Canonical type | `lima-local.lima-local-1cpu-2gi` |

The previous template selected the short `vcluster.com/node-type` value. vCluster used that short value for the Node's instance-type label, while Karpenter's instance type used the provider-qualified name. This caused `Unconsolidatable` events with `Instance Type "lima-local-1cpu-2gi" not found`.

The fix selects the canonical Platform ID in the generated tenant pool and omits the unused short `instance-type` provider property. Existing workers received a one-time migration of both type labels; the selector change also triggered normal Karpenter drift replacement. **The regression below used fresh workers that registered with both canonical labels automatically.** No label repairs were applied to those fresh workers.

### Regression sequence

1. Confirmed one Ready nginx replica and one fresh Ready worker.
2. Scaled the Deployment to two replicas. A second fresh Lima VM joined and both workers had matching canonical type labels.
3. Used temporary [Pod deletion-cost annotations](https://kubernetes.io/docs/concepts/workloads/controllers/replicaset/#pod-deletion-cost) to reproduce the original failing placement: reducing to one replica removed nginx from the original worker and kept nginx on the newer worker. The original worker still hosted CoreDNS, konnectivity-agent and local-path-provisioner, so both workers contained non-DaemonSet Pods.
4. Observed Karpenter evict nginx from the newer worker and reschedule it on the retained worker. It then removed the retired Node, Platform NodeClaim, ownership record and actual Lima VM automatically.
5. Verified nginx and the system Deployments were Ready. Removed the temporary deletion-cost annotation if it remained on a surviving test Pod.

| Stage | Observed at | Ready nginx replicas | Ready workers | Platform claims | Ownership records | Managed VMs |
| --- | --- | --- | --- | --- | --- | --- |
| Baseline | 0.9s | 1 | 1 | 1 | 1 | 1 |
| Scaled up | 121.0s | 2 | 2 | 2 | 2 | 2 |
| Occupied-worker scale-down | 185.1s | 1 | 1 | 1 | 1 | 1 |

Times are relative to the start of this regression and were sampled roughly every eight seconds. They do not establish a removal deadline.

| Worker VM | Platform claim | Result |
| --- | --- | --- |
| `vclima-7f27993df686f53eda15f796` | `lima-demo-a-9d445` | Retained; nginx was rescheduled here. |
| `vclima-db19125756e6ca3e1d5c52d3` | `lima-demo-a-wh2xc` | Removed automatically; exact VM name absent from Lima inventory. |

**Validation scope:** One tenant in the observed environment. The desired replica count went from one to two to one; no zero-replica fallback, manual Node/NodeClaim deletion or manual VM deletion was used during this regression. The legacy metadata migration is separate from that fresh-worker test. This extends the earlier empty-worker evidence without claiming that every scheduling or disruption constraint permits consolidation. The tenant was left running with one Ready worker and one Ready nginx replica for screenshots.
