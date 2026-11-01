# Two-tenant autoscaling example

Run the same nginx Deployment in two independent vCluster tenants and observe each tenant provision and retire its own Lima workers. Both tenants share one provider and one Mac helper.

This is a live lab: the commands create real tenants and VMs. Read the [installation prerequisites](../../docs/installation.md#prerequisites) first. Run commands from the repository root.

## What the example demonstrates

| Stage | Ready replicas per tenant | Expected workers per tenant | Total managed VMs |
| --- | --- | --- | --- |
| Baseline | 1 | 1 | 2 |
| Scale up | 2 | 2 | 4 |
| Scale down | 1 | 1 after an empty worker is retired | 2 |
| Delete tenants | — | 0 | 0 |

System Pods can retain workers. The scale-down checks below distinguish Pod removal, Kubernetes Node removal, NodeClaim deletion and actual VM deletion. A single successful observation is not a guarantee of an exact node count in every environment.

## 1. Prepare the shared capacity budget

Use the [setup commands](../../docs/installation.md#prepare-and-deploy) with this profile:

```bash
./setup.sh prepare --platform-host YOUR-PLATFORM-HOSTNAME \
  --cpus 1 --memory-gib 2 --disk-gib 30 \
  --max-nodes 4 --pool-nodes 2
./setup.sh start
./setup.sh check
./setup.sh deploy
```

Preparation is intended for a fresh installation or one whose managed workers have already been removed. It refuses configuration changes while ownership records remain. Use your complete settings if you change the defaults.

The commands below use these names. Substitute your own host context, project or connected cluster if needed:

| Setting | Example value |
| --- | --- |
| Host kubectl context | `k3d-host-cluster` |
| Platform namespace | `vcluster-platform` |
| Platform project | `default` |
| Project namespace | `p-default` |
| Connected cluster in Platform | `loft-cluster` |
| NodeProvider | `lima-local` |
| Tenant template | `private-nodes-lima-auto` |

### The three capacity controls

| Control | Generated setting | Scope and effect |
| --- | --- | --- |
| Tenant pool | `limits.nodes: "2"`, `cpu: "2"`, `memory: 4Gi` | Each tenant can request two 1 CPU / 2Gi workers. These are aggregate worker capacities, before Kubernetes overhead. |
| NodeProvider | Node type `maxCapacity: 4` | Platform's shared capacity setting for this node type across tenants. This provider exposes one worker shape. |
| Mac helper | `max_nodes: 4` | The helper checks its ownership reservations and all `vclima-*` VMs in its Lima inventory before accepting another VM. |

`--max-nodes` configures both the NodeProvider and helper. `--pool-nodes` generates the per-tenant node, CPU and memory limits. Two tenants can therefore each use two workers while sharing four provider slots.

Four workers represent **4 CPUs / 8Gi RAM / four 30Gi virtual disks**. This does not reserve host resources or account for Docker, macOS and unrelated VMs. The helper excludes unrelated VM names from its count; choose a budget your Mac can support. Use one helper for the shared budget.

## 2. Create two independent tenants

Ensure your [project allows the template](../../docs/installation.md#1-allow-the-template-in-your-project), then create the tenants:

```bash
vcluster create lima-demo-a --driver platform \
  --template private-nodes-lima-auto \
  --project default --cluster loft-cluster --connect=false

vcluster create lima-demo-b --driver platform \
  --template private-nodes-lima-auto \
  --project default --cluster loft-cluster --connect=false
```

Each tenant has its own control plane, Kubernetes API and worker membership. They share the provider's capacity budget; their workers are not interchangeable nodes in one cluster. Tenant system Pods may request an initial worker before the application is deployed.

### Save private kubeconfigs

Export a kubeconfig for each tenant so later commands target the intended cluster without switching your current context:

```bash
umask 077
mkdir -p .local/autoscaling
chmod 700 .local/autoscaling

vcluster connect lima-demo-a --driver platform --project default --print --background-proxy=false \
  > .local/autoscaling/lima-demo-a.kubeconfig
vcluster connect lima-demo-b --driver platform --project default --print --background-proxy=false \
  > .local/autoscaling/lima-demo-b.kubeconfig

chmod 600 .local/autoscaling/*.kubeconfig
```

These files contain credentials. Keep them in ignored `.local/` and do not paste their contents into an issue or validation record. The [vCluster connect reference](https://www.vcluster.com/docs/vcluster/cli/vcluster_connect) documents `--print` and command execution options.

## 3. Deploy one replica in each tenant

Apply the same [workload.yaml](workload.yaml) to both tenants:

```bash
kubectl --kubeconfig .local/autoscaling/lima-demo-a.kubeconfig apply -f examples/autoscaling/workload.yaml
kubectl --kubeconfig .local/autoscaling/lima-demo-b.kubeconfig apply -f examples/autoscaling/workload.yaml

kubectl --kubeconfig .local/autoscaling/lima-demo-a.kubeconfig -n default rollout status deployment/lima-scale-test --timeout=15m
kubectl --kubeconfig .local/autoscaling/lima-demo-b.kubeconfig -n default rollout status deployment/lima-scale-test --timeout=15m
```

| Container setting | Value | Purpose |
| --- | --- | --- |
| Image | `nginx:1.28-alpine` | Small HTTP application with an HTTP readiness probe. |
| CPU request | `600m` | One Pod fits a 1-CPU worker; two require `1200m` and cannot share one. |
| Memory request | `128Mi` | Small reservation suitable for a local lab. |
| CPU limit | `750m` | Runtime CPU ceiling for the container. |
| Memory limit | `256Mi` | Runtime memory ceiling for the container. |

The scheduler uses requests to place Pods. This example needs no CPU stress generator: two idle nginx Pods still reserve more CPU than one worker provides. See [Kubernetes resource requests and limits](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/) for the scheduling distinction.

Confirm one Ready application Pod and one Ready worker in each tenant:

```bash
kubectl --kubeconfig .local/autoscaling/lima-demo-a.kubeconfig -n default get pods -l app=lima-scale-test -o wide
kubectl --kubeconfig .local/autoscaling/lima-demo-a.kubeconfig get nodes -o wide

kubectl --kubeconfig .local/autoscaling/lima-demo-b.kubeconfig -n default get pods -l app=lima-scale-test -o wide
kubectl --kubeconfig .local/autoscaling/lima-demo-b.kubeconfig get nodes -o wide
```

## 4. Scale up both tenants

```bash
kubectl --kubeconfig .local/autoscaling/lima-demo-a.kubeconfig -n default scale deployment/lima-scale-test --replicas=2
kubectl --kubeconfig .local/autoscaling/lima-demo-b.kubeconfig -n default scale deployment/lima-scale-test --replicas=2

kubectl --kubeconfig .local/autoscaling/lima-demo-a.kubeconfig -n default rollout status deployment/lima-scale-test --timeout=15m
kubectl --kubeconfig .local/autoscaling/lima-demo-b.kubeconfig -n default rollout status deployment/lima-scale-test --timeout=15m
```

Repeat the Pod and Node checks. Each tenant should now have two Ready replicas on separate workers. Also inspect the host-side claims and the actual VM inventory:

```bash
./setup.sh status
kubectl --context k3d-host-cluster -n p-default get nodeclaims.storage.loft.sh
limactl list --format '{{.Name}} {{.Status}}'
```

Record which NodeClaim belongs to each tenant and which VM belongs to that claim. The helper's private ownership records provide this mapping without containing bootstrap payloads:

```bash
python3 - <<'PY'
import json
from pathlib import Path

for path in sorted(Path('.local/nodes').glob('*.json')):
    record = json.loads(path.read_text())
    print(record['tenant'], record['claim'], record['vm_name'], 'bootstrap-complete=' + str(record['ready']))
PY
```

Use your configured ownership path if you changed `--state-dir`. Keep the VM names for the scale-down check. The inventory may also contain unrelated VMs; only count the VMs mapped to these two tenants.

## 5. Reduce demand and verify VM removal

```bash
kubectl --kubeconfig .local/autoscaling/lima-demo-a.kubeconfig -n default scale deployment/lima-scale-test --replicas=1
kubectl --kubeconfig .local/autoscaling/lima-demo-b.kubeconfig -n default scale deployment/lima-scale-test --replicas=1
```

Wait for each Deployment to return to one Ready replica. Then repeat the tenant Node checks, host NodeClaim listing, helper status and Lima inventory listing.

For each retired worker, confirm all four observations:

1. Its Kubernetes Node disappears from the tenant.
2. Its Platform NodeClaim disappears from `p-default`.
3. Its helper ownership record disappears.
4. Its exact `vclima-*` VM name is absent from `limactl list`.

Auto Nodes initiates removal; Platform destroys the managed OpenTofu resource and the helper deletes its owned VM. Do not manually delete a Lima VM to claim that automatic scale-down succeeded.

### If an occupied worker remains

The generated policy is `WhenEmptyOrUnderutilized` with `consolidateAfter: 1m`. That delay is not a removal deadline. A system Pod can keep a worker occupied even after an application replica is removed.

The prototype's initialization controller writes a short instance-type label, while Karpenter can expect a provider-qualified name for occupied-worker consolidation. An `Instance Type ... not found` warning can prevent Pod repacking. Empty-worker removal is the behavior this example targets. Do not override the controller-managed label to force a result.

To test empty-worker removal without application placement affecting the outcome, temporarily remove all test replicas:

```bash
kubectl --kubeconfig .local/autoscaling/lima-demo-a.kubeconfig -n default scale deployment/lima-scale-test --replicas=0
kubectl --kubeconfig .local/autoscaling/lima-demo-b.kubeconfig -n default scale deployment/lima-scale-test --replicas=0
```

Observe the same Node, claim, ownership and VM checks. System Pods may retain a baseline worker. Restore the original application after observing cleanup:

```bash
kubectl --kubeconfig .local/autoscaling/lima-demo-a.kubeconfig -n default scale deployment/lima-scale-test --replicas=1
kubectl --kubeconfig .local/autoscaling/lima-demo-b.kubeconfig -n default scale deployment/lima-scale-test --replicas=1
```

Record whether scale-down succeeded at one replica or required the zero-replica step. Do not report occupied-worker consolidation as validated by empty-worker deletion. See the [Auto Nodes configuration](https://www.vcluster.com/docs/vcluster/configure/vcluster-yaml/private-nodes/auto-nodes) for disruption settings.

## 6. Tear down before removing Platform

Keep Platform and the helper running while deleting the example tenants:

```bash
vcluster delete lima-demo-a --driver platform --project default
vcluster delete lima-demo-b --driver platform --project default

./setup.sh status
kubectl --context k3d-host-cluster -n p-default get nodeclaims.storage.loft.sh
limactl list --format '{{.Name}} {{.Status}}'
```

Confirm both tenants are gone and none of their claims, ownership records or mapped VMs remain. Unrelated tenants and manually managed VMs can remain in the output.

If you are also removing the provider, follow [Cleanup before removing Platform](../../docs/installation.md#cleanup-before-removing-platform) only after worker cleanup finishes. Remove the example kubeconfigs after they are no longer needed. Never discard helper ownership records or provider state while workers remain.

## Delays, failures and interrupted runs

| Observation | What to check or do |
| --- | --- |
| First VM takes several minutes | Lima may need to download an Ubuntu image. Package installation, worker bootstrap and the nginx image pull add separate delays. |
| Another claim waits behind provisioning | The helper serializes VM operations. A long bootstrap can delay another tenant's creation or deletion; readiness waits are bounded observations, not provisioning guarantees. |
| Pods stay Pending | Inspect tenant Pod events, requests and pool limits, then check shared provider capacity and helper reservations. This example's four slots are fully used at two replicas in both tenants. |
| Claim stays Provisioning or bootstrap fails | Inspect Platform status and the private guest log `/var/log/vcluster-lima-bootstrap.log`. Failed bootstrap retains a reservation and stable identity for managed retries. Do not publish unredacted registration logs. |
| A command or terminal is interrupted | Leave Platform and the helper running. Reconnect using the saved kubeconfigs, inspect current replica counts and compare claims, ownership records and VMs before continuing. |
| Helper process restarts | Reuse the same private configuration, certificate, token and ownership directory. `./setup.sh start` starts the service if needed; `check` verifies its configuration and Platform reachability. |
| A managed VM is missing | An API read exposes the missing VM and a managed retry can recreate it. There is no independent repair loop; a stopped-but-existing VM is not currently detected as drift. |
| Cleanup was interrupted | Retry deleting only the example tenants while Platform and the helper are available. Preserve provider credentials and ownership records until cleanup completes. Do not bypass finalizers or delete arbitrary VMs to hide a failed lifecycle operation. |
| Platform was removed too early | Normal managed destroy can no longer run. Preserve remaining state and VM identities for manual reconciliation; reinstalling Platform alone does not reconstruct lost ownership or OpenTofu state. |

For the observed environment and results, see the [autoscaling validation record](../../docs/autoscaling-validation.md). The [helper guide](../../docs/local-helper.md) explains the ownership and retry contract.
