# Troubleshooting

Use this guide when installation, provisioning, scaling or cleanup does not behave as expected. For the normal workflow, follow the [installation guide](installation.md) or [two-tenant autoscaling example](../examples/autoscaling/README.md).

Run commands from the repository root. Examples use the default private directory `.local/`, host context `k3d-host-cluster` and the autoscaling example's tenant names. Substitute your configured values. With a custom private directory, pass the same global `--state-dir` option before each setup command. If you configured `lima_home`, use that inventory when running `limactl` directly.

## Scenario index

| Stage | Scenario |
| --- | --- |
| Preparation | [Cannot find the Platform hostname](#preparation-cannot-find-the-platform-hostname) |
| Helper | [Port already in use](#helper-startup-reports-a-port-conflict) |
| Helper | [Startup is delayed or times out](#helper-startup-is-delayed-or-times-out) |
| Helper | [Old worker sizing or capacity limits](#helper-uses-old-sizing-or-limits) |
| Connectivity | [Platform cannot reach the helper](#platform-cannot-reach-the-helper) |
| Deployment | [Existing provider, template or credential Secret](#resolve-existing-resource-conflicts) |
| Versions | [Platform reports a newer vCluster version](#platform-reports-a-new-vcluster-version) |
| Cleanup | [Inactive resources from an older installation](#remove-inactive-resources-from-an-older-setup) |
| Tenant creation | [Cannot find an allowed template](#tenant-creation-cannot-find-an-allowed-template) |
| Diagnostics | [Subprocess failure without detailed output](#a-subprocess-fails-without-detailed-output) |
| Provisioning | [First worker takes several minutes](#first-provisioning-takes-several-minutes) |
| Provisioning | [NodeClaim stays Provisioning or bootstrap fails](#nodeclaim-stays-provisioning-or-bootstrap-fails) |
| Scheduling | [Pods stay Pending](#pods-stay-pending) |
| Capacity | [Request returns capacity exhausted](#request-returns-capacity-exhausted) |
| Recovery | [Command or terminal was interrupted](#a-command-or-terminal-was-interrupted) |
| Recovery | [Helper process restarts](#helper-process-restarts) |
| Recovery | [Managed VM is missing or stopped](#a-managed-vm-is-missing-or-stopped) |
| Scale-down | [An occupied worker remains](#if-an-occupied-worker-remains) |
| Cleanup | [Teardown fails or times out](#teardown-fails-or-times-out) |
| Cleanup | [Teardown cannot find its configuration](#teardown-cannot-find-its-configuration) |
| Cleanup | [Platform was removed before worker cleanup](#platform-was-removed-before-cleanup) |

## Preparation cannot find the Platform hostname

Log the vCluster CLI into Platform or pass the browser hostname explicitly:

```bash
./setup.sh prepare --platform-host YOUR-PLATFORM-HOSTNAME
```

Use the hostname without `https://` or a URL path. Automatic discovery requires exactly one Platform hostname in `~/.vcluster/config.json`; missing or multiple entries require an explicit choice. See [Find your Platform hostname](installation.md#1-find-your-platform-hostname).

When repeating preparation, include your complete worker, capacity and connection settings. Omitted options return to their defaults.

## Helper startup reports a port conflict

Identify the listener, replacing `18443` if you configured another port:

```bash
lsof -nP -iTCP:18443 -sTCP:LISTEN
```

Removing tenants or a NodeProvider in Platform does not stop a macOS helper service. Stop an obsolete helper through its service manager, then restart this installation:

```bash
./setup.sh stop
./setup.sh start
./setup.sh check
```

If the other service is still needed, repeat preparation with a free `--port` and your complete settings. A custom `--api-url` must use the chosen port. Remove managed workers before changing an existing installation's configuration.

## Helper startup is delayed or times out

`start` waits up to 30 seconds for authenticated health. A healthy registered helper returns immediately. `stop` waits for its own process to exit so the next start can reuse the port.

If startup times out, inspect the private `helper.err` file identified by the error. Resolve any reported configuration or service error before retrying `./setup.sh start`. If a listener already occupies the port, follow [port conflict checks](#helper-startup-reports-a-port-conflict).

## Helper uses old sizing or limits

The health response's `config_id` detects stale settings. Remove managed workers, stop the helper, prepare the complete desired configuration and restart it before redeploying. Follow [Change the worker profile or shared cap](installation.md#change-the-worker-profile-or-shared-cap).

Preparation rejects configuration changes while ownership records remain. Preserve those records until managed worker cleanup finishes.

## Platform cannot reach the helper

```bash
./setup.sh check
```

Check host networking, the configured port, the certificate's subject alternative names and the macOS firewall. Platform must reach the prepared HTTPS endpoint from inside its deployment; a successful local health probe alone does not establish container-to-Mac connectivity.

The check requires `sh`, `curl`, `base64` and `mktemp` in the Platform deployment. Review the [connection settings](installation.md#connection-settings), including `--api-url` and `--listen-host`, for environments that differ from Docker Desktop.

## Resolve existing resource conflicts

Deployment checks the NodeProvider, tenant template and provider credential Secret before making changes. Existing names can belong to an earlier setup or carry ownership metadata for another private directory.

The error lists the host context, installation directory and every conflicting resource identity. Secrets are identified by name and namespace; their contents are excluded. The installer does not adopt or overwrite resources belonging to another installation.

### Reuse the original names

For your own earlier installation, preview and remove it before repeating the normal setup:

```bash
./setup.sh teardown --dry-run
./setup.sh teardown
```

This deletes that installation's tenants and their data. Use its original private directory if it differs from the current one. Then repeat [Prepare and deploy](installation.md#prepare-and-deploy) with the original names.

For older resources without ownership metadata, follow [inactive legacy cleanup](#remove-inactive-resources-from-an-older-setup).

### Keep existing resources with different names

Copy the recovery commands printed by `deploy` and run them from the repository root:

1. Stop this installation's helper before changing its provider name. The name also determines the macOS service label.
2. Prepare the suggested provider and template names. The printed command preserves your current hostname, VM size, capacity limits, context, namespace, networking and custom paths.
3. Start the helper, check Platform connectivity and deploy again.
4. Use the new template ID with `vcluster create --template ...` and allow that ID in the target project.

These commands reuse the private directory and credentials. If this installation has managed workers, remove its tenants and wait for worker cleanup before changing names.

After a blocked deployment, `status` may show a healthy helper but `No resources found.` for NodeProviders or NodeClaims. Helper readiness and provider installation are separate stages.

## Platform reports a new vCluster version

The UI compares a template or tenant's vCluster chart version with Platform's default. This project's installer pins **0.37.4** by default. Updating the installer does not change older prepared configuration, installed templates or running tenants.

For a fresh installation, follow the normal preparation steps. To select a different published version, include `--chart-version VERSION` with your complete preparation settings. If you already prepared an older version, regenerate and deploy the template after removing managed workers as described in [Change the worker profile or shared cap](installation.md#change-the-worker-profile-or-shared-cap).

For existing tenants, update their installed template's `spec.template.helmRelease.chart.version` to the intended version, keeping its source manifest aligned. An unversioned template change requires a one-time sync on each tenant; changing the template alone does not upgrade it. For an existing `lima-demo-a` tenant in project `default`:

```bash
kubectl --context k3d-host-cluster -n p-default patch virtualclusterinstance lima-demo-a \
  --type=merge -p '{"spec":{"templateRef":{"syncOnce":true}}}'
```

Apply this only after reviewing all pending template changes; the sync applies the resolved template configuration. Platform resets `syncOnce` after resolution. Wait for the tenant control plane to become Ready and confirm its running image uses the target version before taking screenshots. Template version numbers and the vCluster chart version are separate settings. See [upstream template propagation](https://www.vcluster.com/docs/platform/administer/templates/versioning#how-template-changes-propagate-to-existing-resources).

## Remove inactive resources from an older setup

Older templates or credential Secrets may predate this project's ownership labels. For inactive resources at the configured names, use the explicit opt-in:

```bash
./setup.sh teardown --include-unmanaged --dry-run
./setup.sh teardown --include-unmanaged
```

This option only accepts resources without another installation's ownership ID. It refuses legacy cleanup while associated tenants, claims or worker records remain; clean those through the original installation first. Resources owned by another labelled installation require its original private directory.

After cleanup, repeat the [normal installation](installation.md#prepare-and-deploy) using the default provider and template names.

## Tenant creation cannot find an allowed template

Check that the generated template exists in the configured host cluster:

```bash
kubectl --context k3d-host-cluster get virtualclustertemplate private-nodes-lima-auto
```

Then check the target project's allowed templates in Platform. A globally installed template is not automatically allowed in every project. An administrator can allow its specific ID; a project allowing `*` already includes it.

Use the installed ID in `vcluster create --template ...`, including any override set with `--template-name`. See [Allow the template in your project](installation.md#1-allow-the-template-in-your-project).

## A subprocess fails without detailed output

Subprocess output is intentionally withheld because errors can contain credentials or registration data. Inspect the named operation privately in your local environment. Do not enable REST provider debugging or publish full OpenTofu state, kubeconfigs, registration payloads or unredacted bootstrap logs.

For provisioning failures, use the [claim and bootstrap checks](#nodeclaim-stays-provisioning-or-bootstrap-fails). For helper startup, inspect its private `helper.err` file. See [SECURITY.md](../SECURITY.md) before sharing diagnostic material.

## First provisioning takes several minutes

Lima may need to download an Ubuntu image. Package installation, worker bootstrap and the nginx image pull add separate delays. The helper serializes VM operations, so a long bootstrap can queue another tenant's creation or deletion.

Observe progress with the [claim and bootstrap checks](#nodeclaim-stays-provisioning-or-bootstrap-fails). Readiness waits are bounded observations, not provisioning guarantees.

## NodeClaim stays Provisioning or bootstrap fails

Compare Platform claims, helper records and actual Lima inventory:

```bash
./setup.sh status
kubectl --context k3d-host-cluster get nodeclaims.storage.loft.sh -A
limactl list
```

Inspect Platform status and events, private helper logs and the guest's root-readable `/var/log/vcluster-lima-bootstrap.log`. First-boot downloads and registration can take several minutes. Bootstrap completion alone does not prove Kubernetes Node readiness; confirm the worker inside the tenant using the [readiness checks](installation.md#4-confirm-readiness-inside-the-tenant).

Failed bootstrap leaves `ready: false` in the ownership record and retains the reservation and stable identity for a managed retry. Retry through Platform rather than erasing ownership state. Registration logs can contain secrets; inspect them privately.

## Pods stay Pending

Inspect Pod events inside the affected tenant. For tenant A in the autoscaling example:

```bash
kubectl --kubeconfig .local/autoscaling/lima-demo-a.kubeconfig -n default get pods -o wide
kubectl --kubeconfig .local/autoscaling/lima-demo-a.kubeconfig -n default describe pods -l app=lima-scale-test
kubectl --kubeconfig .local/autoscaling/lima-demo-a.kubeconfig get nodes -o wide
```

Check Pod resource requests against worker allocatable capacity and the tenant pool's node, CPU and memory limits. Then check shared NodeProvider capacity and helper reservations. The [autoscaling example](../examples/autoscaling/README.md#the-three-capacity-controls) uses all four shared slots when both tenants run two replicas.

If a worker is still starting, follow [provisioning checks](#nodeclaim-stays-provisioning-or-bootstrap-fails). Pool limits permit capacity; they do not pre-create workers or guarantee resources on the Mac.

## Request returns capacity exhausted

Inspect managed ownership records and all `vclima-*` VMs in the selected Lima inventory with `./setup.sh status` and `limactl list`. The helper counts the union of records and matching VM names against `max_nodes`; failed or missing workers can retain reservations.

Unrecorded VMs with that prefix consume capacity but are not automatically adopted or deleted. Retry the managed operation through Platform or delete the owning tenant. Do not erase ownership files while resources remain.

Review the [capacity budgets](installation.md#understand-the-capacity-budgets) before raising limits. Host-resource budgeting is manual; increasing a cap does not establish that the Mac has enough resources.

## A command or terminal was interrupted

Leave Platform and the helper running. Reconnect using the [saved tenant kubeconfigs](../examples/autoscaling/README.md#save-private-kubeconfigs), inspect current replica counts and compare NodeClaims, ownership records and Lima VMs before continuing.

The launchd-managed helper keeps running when the terminal closes. If cleanup was interrupted, follow [teardown recovery](#teardown-fails-or-times-out).

## Helper process restarts

Reuse the same private configuration, certificate, token and ownership directory:

```bash
./setup.sh start
./setup.sh check
```

`start` starts the service if needed; `check` verifies its configuration and Platform reachability. Keep one helper process per installation. Multiple processes sharing ownership state are unsupported.

## A managed VM is missing or stopped

An API read exposes a missing VM by setting its ownership record to `ready: false`. A managed create/update retry can recreate it with the same identity. There is no independent repair loop; a stopped-but-existing VM is not currently detected as drift.

Preserve the record and reconcile the worker through Platform. Do not create an untracked replacement or delete state to hide the discrepancy. The [helper lifecycle contract](local-helper.md#ownership-retries-and-capacity) explains ownership and deletion.

## If an occupied worker remains

The generated policy is `WhenEmptyOrUnderutilized` with `consolidateAfter: 1m`. That delay is not a removal deadline. System Pods and disruption constraints can retain workers after an application replica is removed.

The prototype's initialization controller writes a short instance-type label, while Karpenter can expect a provider-qualified name for occupied-worker consolidation. An `Instance Type ... not found` warning can prevent Pod repacking. Empty-worker removal is the behavior the autoscaling example targets. Do not override the controller-managed label to force a result.

To test empty-worker removal without application placement affecting the outcome, temporarily remove all test replicas:

```bash
kubectl --kubeconfig .local/autoscaling/lima-demo-a.kubeconfig -n default scale deployment/lima-scale-test --replicas=0
kubectl --kubeconfig .local/autoscaling/lima-demo-b.kubeconfig -n default scale deployment/lima-scale-test --replicas=0
```

Repeat the [Node, claim, ownership and VM checks](../examples/autoscaling/README.md#5-reduce-demand-and-verify-vm-removal). System Pods may retain a baseline worker. Restore the application after observing cleanup:

```bash
kubectl --kubeconfig .local/autoscaling/lima-demo-a.kubeconfig -n default scale deployment/lima-scale-test --replicas=1
kubectl --kubeconfig .local/autoscaling/lima-demo-b.kubeconfig -n default scale deployment/lima-scale-test --replicas=1
```

Record whether scale-down succeeded at one replica or required the zero-replica step. Empty-worker deletion does not validate occupied-worker consolidation. See the [autoscaling validation record](autoscaling-validation.md) for the observed run; it did not require the fallback. The upstream [Auto Nodes configuration](https://www.vcluster.com/docs/vcluster/configure/vcluster-yaml/private-nodes/auto-nodes) documents disruption settings.

## Teardown fails or times out

Keep Platform and the helper available. Inspect the resources reported as pending and compare claims, ownership records and actual Lima VMs using the [provisioning checks](#nodeclaim-stays-provisioning-or-bootstrap-fails). The helper serializes VM operations, so cleanup may queue behind an ongoing bootstrap.

Teardown retains the template, provider and credentials while dependent tenants and workers are removed. Platform needs the referenced template to finalize worker cleanup. Do not delete it early, bypass finalizers or delete arbitrary VMs to hide a failed lifecycle operation.

If worker cleanup fails or times out, private configuration and credentials remain available for recovery. Resolve the pending operation, then retry:

```bash
./setup.sh teardown
```

The default timeout is 900 seconds. To use another timeout:

```bash
./setup.sh teardown --timeout 1800
```

If you were only deleting the example tenants rather than the whole installation, retry their deletion through Platform while retaining the helper and provider. Preserve ownership records and credentials until workers are gone. See [normal teardown](installation.md#cleanup-before-removing-platform) for scope and order.

## Teardown cannot find its configuration

Without the prepared `config.json`, teardown cannot identify the installation's remaining resources and makes no changes. After a successful full teardown, this is expected on another invocation.

If resources remain, use the original private directory or restore its original configuration and credentials before retrying. Do not prepare a new identity over lost state and assume it will adopt existing resources. A custom directory must be selected with the global `--state-dir` option before `teardown`.

## Platform was removed before cleanup

Normal managed destroy can no longer run. Preserve remaining state and VM identities for manual reconciliation. Reinstalling Platform alone does not reconstruct lost ownership or OpenTofu state.

Complete [managed teardown](installation.md#cleanup-before-removing-platform) before removing Platform in future runs.
