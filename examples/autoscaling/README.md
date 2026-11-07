# Two-tenant autoscaling example

Run the same nginx Deployment in two independent vCluster tenants and observe each tenant provision and retire its own Lima workers. Both tenants share one provider and one Mac helper.

This is a live lab: the commands create real tenants and VMs. Read the [installation prerequisites](../../docs/installation.md#prerequisites) first. Run commands from the repository root.

Screenshot placeholders follow the steps below. Fill in the [capture metadata](#screenshot-capture-metadata) when adding images, then update the listed reuse locations.

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

<a name="platform-nodeprovider"></a>

> **Screenshot placeholder — Lima NodeProvider**
>
> Show the provider in Platform's NodeProviders view with its ID, availability and 1 CPU / 2Gi worker node type visible.
>
> Planned image: `docs/images/screenshots/platform-nodeprovider.png`.

<a name="lima-auto-template"></a>

> **Screenshot placeholder — Dynamic tenant template**
>
> Show the installed template's provider reference and dynamic pool limits: two nodes, 2 CPUs and 4Gi per tenant for this example configuration.
>
> Planned image: `docs/images/screenshots/lima-auto-template.png`.

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

<a name="default-project-tenants"></a>

> **Screenshot placeholder — Default project tenants**
>
> Show lima-demo-a and lima-demo-b running in the Default project after the baseline workers and nginx replicas are Ready.
>
> Planned image: `docs/images/screenshots/default-project-tenants.png`.

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

<a name="lima-demo-a-overview"></a>

> **Screenshot placeholder — Tenant A overview**
>
> Show lima-demo-a's graph or overview with its hosted control plane, Auto Nodes worker and Lima provider association at baseline.
>
> Planned image: `docs/images/screenshots/lima-demo-a-overview.png`.

<a name="lima-demo-a-nodes-baseline"></a>

> **Screenshot placeholder — Tenant A baseline worker**
>
> Show lima-demo-a's Nodes view with one joined Lima worker in Ready state. Keep the worker name legible for later comparisons.
>
> Planned image: `docs/images/screenshots/lima-demo-a-nodes-baseline.png`.

<a name="lima-demo-b-nodes-baseline"></a>

> **Screenshot placeholder — Tenant B baseline worker**
>
> Show lima-demo-b's Nodes view with one joined Lima worker in Ready state, using the same columns as tenant A.
>
> Planned image: `docs/images/screenshots/lima-demo-b-nodes-baseline.png`.

## 4. Scale up both tenants

```bash
kubectl --kubeconfig .local/autoscaling/lima-demo-a.kubeconfig -n default scale deployment/lima-scale-test --replicas=2
kubectl --kubeconfig .local/autoscaling/lima-demo-b.kubeconfig -n default scale deployment/lima-scale-test --replicas=2
```

Capture provisioning while the new workers are being requested, before waiting for both rollouts to finish:

<a name="nodeclaims-provisioning"></a>

> **Screenshot placeholder — NodeClaims provisioning**
>
> Capture the extra NodeClaims while they are Pending or Provisioning after both Deployments are scaled to two replicas. Keep tenant associations visible.
>
> Planned image: `docs/images/screenshots/nodeclaims-provisioning.png`.

Then wait for application readiness:

```bash
kubectl --kubeconfig .local/autoscaling/lima-demo-a.kubeconfig -n default rollout status deployment/lima-scale-test --timeout=15m
kubectl --kubeconfig .local/autoscaling/lima-demo-b.kubeconfig -n default rollout status deployment/lima-scale-test --timeout=15m
```

Repeat the Pod and Node checks. Each tenant should now have two Ready replicas on separate workers.

<a name="lima-demo-a-nodes-scaled-up"></a>

> **Screenshot placeholder — Tenant A scaled-up workers**
>
> Show two Ready Lima workers in lima-demo-a. Match the baseline view so the additional worker is easy to identify.
>
> Planned image: `docs/images/screenshots/lima-demo-a-nodes-scaled-up.png`.

<a name="lima-demo-b-nodes-scaled-up"></a>

> **Screenshot placeholder — Tenant B scaled-up workers**
>
> Show two Ready Lima workers in lima-demo-b using the same view as its baseline capture.
>
> Planned image: `docs/images/screenshots/lima-demo-b-nodes-scaled-up.png`.

<a name="lima-demo-a-deployment"></a>

> **Screenshot placeholder — Tenant A nginx Deployment**
>
> Show lima-scale-test in lima-demo-a's Deployments view with two desired, Ready and available replicas.
>
> Planned image: `docs/images/screenshots/lima-demo-a-deployment.png`.

<a name="lima-demo-b-deployment"></a>

> **Screenshot placeholder — Tenant B nginx Deployment**
>
> Show lima-scale-test in lima-demo-b's Deployments view with two desired, Ready and available replicas.
>
> Planned image: `docs/images/screenshots/lima-demo-b-deployment.png`.

<a name="lima-demo-a-pods"></a>

> **Screenshot placeholder — Tenant A Pod placement**
>
> Show both Ready nginx Pods in lima-demo-a's default namespace with the Node column visible. The replicas should be on distinct workers.
>
> Planned image: `docs/images/screenshots/lima-demo-a-pods.png`.

<a name="lima-demo-b-pods"></a>

> **Screenshot placeholder — Tenant B Pod placement**
>
> Show both Ready nginx Pods in lima-demo-b's default namespace with their associated worker names visible.
>
> Planned image: `docs/images/screenshots/lima-demo-b-pods.png`.

Also inspect the host-side claims and the actual VM inventory:

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

<a name="nodeclaims-available"></a>

> **Screenshot placeholder — NodeClaims available**
>
> Show four available Platform NodeClaims, two for each tenant, after all workers have joined. Correlate claim identities with the helper status capture.
>
> Planned image: `docs/images/screenshots/nodeclaims-available.png`.

<a name="helper-status"></a>

> **Screenshot placeholder — Shared capacity and ownership**
>
> Capture ./setup.sh status after scale-up. Show max_nodes: 4, the worker profile and the tenant/claim/VM mappings without displaying credential files.
>
> Planned image: `docs/images/screenshots/helper-status.png`.

<a name="lima-vms-scaled-up"></a>

> **Screenshot placeholder — Four managed Lima VMs**
>
> Capture limactl list on the Mac after scale-up. Keep all four mapped VM names and their Running state legible.
>
> Planned image: `docs/images/screenshots/lima-vms-scaled-up.png`.

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

<a name="lima-demo-a-nodes-scaled-down"></a>

> **Screenshot placeholder — Tenant A after scale-down**
>
> Show lima-demo-a with one remaining Ready worker after automatic removal. Use the same Nodes view as the baseline and scale-up captures.
>
> Planned image: `docs/images/screenshots/lima-demo-a-nodes-scaled-down.png`.

<a name="lima-demo-b-nodes-scaled-down"></a>

> **Screenshot placeholder — Tenant B after scale-down**
>
> Show lima-demo-b with one remaining Ready worker after automatic removal, keeping its worker identity visible.
>
> Planned image: `docs/images/screenshots/lima-demo-b-nodes-scaled-down.png`.

<a name="nodeclaims-scaled-down"></a>

> **Screenshot placeholder — NodeClaims after scale-down**
>
> Show only the two retained example claims, one per tenant. Compare the exact claim identities with the four-claim capture.
>
> Planned image: `docs/images/screenshots/nodeclaims-scaled-down.png`.

<a name="lima-vms-scaled-down"></a>

> **Screenshot placeholder — Two managed Lima VMs remain**
>
> Capture limactl list after scale-down. Confirm the two retired VM names are absent and the retained names match the workers still shown in Platform.
>
> Planned image: `docs/images/screenshots/lima-vms-scaled-down.png`.

If a worker remains after demand falls, follow [scale-down troubleshooting](../../docs/troubleshooting.md#if-an-occupied-worker-remains) for the consolidation limitation and empty-worker fallback.

## 6. Tear down before removing Platform

Keep Platform available while removing the complete installation. This includes every tenant using its template or associated provider claims, so preview the scope if the provider serves additional tenants:

```bash
./setup.sh teardown --dry-run
./setup.sh teardown

kubectl --context k3d-host-cluster get virtualclusterinstances.storage.loft.sh -A
kubectl --context k3d-host-cluster -n p-default get nodeclaims.storage.loft.sh
limactl list --format '{{.Name}} {{.Status}}'
```

Teardown keeps the helper running until managed VM cleanup completes, then removes the provider resources, stops the helper and removes generated local files. `status` requires a prepared configuration, so the checks afterward use the host API and Lima directly.

Confirm both tenants are gone and none of their claims, ownership records or mapped VMs remain. Unrelated tenants and manually managed VMs can remain in the output.

<a name="default-project-teardown"></a>

> **Screenshot placeholder — Default project after teardown**
>
> Show that lima-demo-a and lima-demo-b are absent from the Default project after managed tenant deletion. Unrelated tenants can remain.
>
> Planned image: `docs/images/screenshots/default-project-teardown.png`.

<a name="nodeclaims-teardown"></a>

> **Screenshot placeholder — NodeClaims after teardown**
>
> Show that no Platform NodeClaims for either example tenant remain after deletion.
>
> Planned image: `docs/images/screenshots/nodeclaims-teardown.png`.

<a name="lima-vms-teardown"></a>

> **Screenshot placeholder — Mac inventory after teardown**
>
> Capture limactl list after both tenants are deleted. All four example VM names must be absent; unrelated VMs may remain.
>
> Planned image: `docs/images/screenshots/lima-vms-teardown.png`.

See [Cleanup before removing Platform](../../docs/installation.md#cleanup-before-removing-platform) for the normal cleanup order and reinstallation steps. For legacy resources or interrupted cleanup, use the [troubleshooting guide](../../docs/troubleshooting.md#scenario-index).

## Screenshot capture metadata

All screenshot captures are pending. Keep tenant, claim and VM identities consistent throughout a capture run. Update this table when screenshots are taken; use a separate metadata block if images come from different runs.

| Field | Capture value |
| --- | --- |
| Capture date and run | Pending |
| Host OS and CPU architecture | Pending |
| vCluster Platform version | Pending |
| Tenant chart and Kubernetes versions | Pending |
| vCluster CLI and Lima versions | Pending |
| Provider and template IDs | Pending |
| Worker profile and capacity limits | Pending |

The [asset directory guide](../../docs/images/screenshots/README.md) explains how to add each PNG and replace its callouts with embedded images. The [validation record](../../docs/autoscaling-validation.md) remains the source for the earlier measured run; future captures must identify their own run.

### Reused display locations

The walkthrough contains all 23 capture slots. These images also have placeholders in other documents; replace every corresponding callout when adding a capture.

| Planned image | Additional display locations |
| --- | --- |
| `platform-nodeprovider.png` | [README showcase](../../README.md#screenshot-showcase), [installation](../../docs/installation.md#4-deploy-the-provider-and-inspect-its-status). |
| `lima-auto-template.png` | [Installation template defaults](../../docs/installation.md#template-defaults). |
| `default-project-tenants.png` | [README showcase](../../README.md#screenshot-showcase). |
| `lima-demo-a-nodes-baseline.png` | [Installation worker readiness](../../docs/installation.md#4-confirm-readiness-inside-the-tenant). |
| `lima-vms-scaled-up.png` | [README showcase](../../README.md#screenshot-showcase). |
| `lima-vms-scaled-down.png` | [README showcase](../../README.md#screenshot-showcase). |

## Troubleshooting

Use the [scenario index](../../docs/troubleshooting.md#scenario-index) for provisioning delays, Pending Pods, capacity limits, interrupted runs and cleanup issues. The [autoscaling validation record](../../docs/autoscaling-validation.md) documents the observed environment and results.
