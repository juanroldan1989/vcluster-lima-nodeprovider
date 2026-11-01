# Install the Lima NodeProvider

This guide installs the provider into an existing vCluster Platform. It does not install Platform or a host Kubernetes cluster. The integration targets a single trusted Mac running macOS on Apple Silicon, Lima's VZ backend and Ubuntu ARM64 guests.

## Prerequisites

- Python 3.11 or newer, OpenSSL and Lima installed on the Mac.
- `limactl` available in `PATH`, or an explicit `--limactl` path.
- A running host Kubernetes cluster reachable with `kubectl`; the default context is `k3d-host-cluster`.
- vCluster Platform with Auto Nodes and Terraform NodeProvider features available under your upstream license or trial. This project does not grant those entitlements.
- Platform administrator access to create NodeProviders, tenant templates and credential Secrets.
- The vCluster CLI logged into Platform, with permission to create and delete tenants in your chosen project. That project must allow the template ID you install.
- Platform able to reach the Mac helper over HTTPS and Lima guests able to reach Platform's registration endpoint.

The prototype used Platform 4.12.1, vCluster 0.37.1 and Lima 2.1.0 on macOS ARM64. The generated tenant chart defaults to 0.37.1. These are starting versions for this experimental integration, not a supported version matrix.

Platform executes OpenTofu inside its own environment. Installing OpenTofu on the Mac is optional unless you want to run the [module integration test](../CONTRIBUTING.md#testing-the-opentofu-integration). See the upstream [Terraform NodeProvider documentation](https://www.vcluster.com/docs/platform/administer/node-providers/terraform) for the Platform lifecycle and credential discovery contract.

## Prepare and deploy

Run the following commands from the repository root.

### 1. Find your Platform hostname

Use the hostname from your Platform browser URL, without `https://` or a path:

| Browser URL | `YOUR-PLATFORM-HOSTNAME` |
| --- | --- |
| `https://example.loft.host/clusters` | `example.loft.host` |

You can omit `--platform-host` when `~/.vcluster/config.json` contains exactly one Platform hostname. Preparation fails if that configuration is missing or ambiguous. Login credentials are not printed.

### 2. Prepare the configuration

```bash
./setup.sh prepare --platform-host YOUR-PLATFORM-HOSTNAME
```

This generates the following files under ignored `.local/`:

- Helper configuration and a private API password.
- A self-signed TLS certificate and key.
- NodeProvider, credential Secret and tenant template manifests.
- A macOS LaunchAgent definition.

Preparation does not create a tenant or VM.

### 3. Start the helper and verify connectivity

```bash
./setup.sh start
./setup.sh check
```

`start` registers the helper with your macOS user launchd service and waits for authenticated health. The service keeps running after the terminal closes; keep the repository and private directory at their prepared paths.

`check` verifies authenticated TLS both locally and from the Platform deployment. Both connections must succeed before deployment.

### 4. Deploy the provider and inspect its status

```bash
./setup.sh deploy
./setup.sh status
```

`deploy` repeats the connectivity check, validates manifests with Kubernetes server-side dry runs, installs the credential Secret, then applies the NodeProvider and tenant template. Existing resources must carry this installation's ownership label and directory-derived annotation.

`status` reports helper health, managed worker records and Platform provider, node type and NodeClaim resources. Tenant creation is covered in [Create a tenant and observe a worker](#create-a-tenant-and-observe-a-worker).

### Connection settings

The default configuration assumes Docker Desktop networking:

| Setting | Default | Override during preparation |
| --- | --- | --- |
| Host Kubernetes context | `k3d-host-cluster` | `--context` |
| Platform namespace | `vcluster-platform` | `--namespace` |
| Platform deployment | `loft` | `--deployment` |
| Helper HTTPS endpoint | `https://host.docker.internal:18443` | `--api-url` |

For another context, namespace or deployment, use:

```bash
./setup.sh --context MY-HOST-CONTEXT --namespace MY-PLATFORM-NAMESPACE prepare \
  --platform-host YOUR-PLATFORM-HOSTNAME \
  --deployment MY-PLATFORM-DEPLOYMENT
```

Place the global `--context` and `--namespace` options **before** `prepare`. Subsequent commands use the saved values and do not change your current kubeconfig context.

Other host environments must provide a reachable HTTPS endpoint with `--api-url` and an appropriate `--listen-host` address. The helper endpoint connects Platform to the Mac; `--platform-host` configures guest registration connectivity.

If you choose a private directory with the global `--state-dir` option, repeat that option on every command. Paths inside the repository must be under `.local/`; paths outside the repository are also accepted.

## Worker sizing and capacity

Each installation supports one worker shape. Configure the individual VM size and capacity budgets during preparation.

### Choose a worker profile

This example uses the default profile and limits:

```bash
./setup.sh prepare --platform-host YOUR-PLATFORM-HOSTNAME \
  --cpus 1 --memory-gib 2 --disk-gib 30 \
  --max-nodes 8 --pool-nodes 4
```

| Setting | Default | Meaning |
| --- | --- | --- |
| `--cpus` | `1` | CPUs per worker VM. |
| `--memory-gib` | `2` | RAM per worker VM, in GiB. |
| `--disk-gib` | `30` | Virtual disk capacity per worker VM, in GiB. |
| `--max-nodes` | `8` | Shared worker cap across tenants using this installation. |
| `--pool-nodes` | Smaller of `4` and `--max-nodes` | Worker cap for each tenant pool. |

The helper and OpenTofu module use the same worker profile. The NodeProvider advertises its CPU and memory capacity, with 200m CPU and 512Mi memory reserved per worker for Kubernetes overhead. Workloads therefore have less allocatable capacity than the VM's total resources.

### Understand the capacity budgets

The shared cap is applied in two places: the NodeProvider's `maxCapacity` and the helper's `max_nodes`. The tenant pool has its own aggregate node, CPU and memory limits.

For four workers sized at 1 CPU / 2Gi each, the generated pool limits are:

| Pool setting | Generated value | Meaning |
| --- | --- | --- |
| `nodes` | `"4"` | At most four workers in this tenant pool. |
| `cpu` | `"4"` | At most four CPUs across those workers. |
| `memory` | `8Gi` | At most 8Gi RAM across those workers. |

These are combined worker capacities. Each individual VM still has 1 CPU / 2Gi RAM.

When choosing limits, account for the following:

- Caps are positive integers with no built-in upper ceiling; a larger cap does not establish that the Mac has enough resources.
- Host-resource budgeting is manual. Lima shares the Mac with Docker, other VMs and macOS.
- Virtual disk capacity does not reserve free space on the host disk.

### Change the worker profile or shared cap

1. Remove this installation's tenants and wait for [managed worker cleanup](#cleanup-before-removing-platform).
2. Run `./setup.sh stop`.
3. Repeat `./setup.sh prepare` with the complete settings you want to use.
4. Run the helper and deployment commands again:

```bash
./setup.sh start
./setup.sh check
./setup.sh deploy
```

Preparation refuses configuration changes while managed records remain. Repeating preparation preserves the token and valid certificate. Changing the endpoint hostname may require certificate replacement after all workers are removed.

**Preparation defaults:** Every omitted option returns to its documented default; it does not inherit the previous invocation. If you use a custom `--state-dir`, repeat it on every command as described in [Prepare and deploy](#prepare-and-deploy).

### Adjust tenant pool limits

You can edit the generated tenant template before deployment. Keep its node, CPU and memory totals consistent with the intended worker profile.

Template changes do not automatically update existing tenant instances; update their configuration separately through Platform. Re-running `prepare` regenerates the template files.

## Create a tenant and observe a worker

### 1. Allow the template in your project

In Platform project settings, ensure the target project's allowed templates include the generated template ID. An administrator can allow the specific ID; a project allowing `*` already includes it.

Deploying a template does not grant project access or change this allowlist. If the CLI reports that it could not find an allowed template, check the project settings even when the template exists globally.

### 2. Create the tenant

`private-nodes-lima-auto` is the default ID of the `VirtualClusterTemplate` included in this repository. Its source is [platform/tenant-template.yaml.tmpl](../platform/tenant-template.yaml.tmpl).

| Step | What this repository provides |
| --- | --- |
| Source | `platform/tenant-template.yaml.tmpl` defines the private-node template with placeholders for its ID, provider, worker profile and pool limits. |
| `./setup.sh prepare` | Renders that source into `.local/tenant-template.yaml` using your configuration. |
| `./setup.sh deploy` | Applies the generated manifest to Platform, creating the `VirtualClusterTemplate` resource. |
| `vcluster create --template private-nodes-lima-auto` | Creates a tenant from that installed resource. |

The default ID is configured by `--template-name` in [helper/manage.py](../helper/manage.py). If you override it during preparation, use the same ID in the creation command below. A custom `--state-dir` also changes the generated manifest's location.

This workflow supplies the template from this project; no template from a separate lab or previous installation is required. After deployment, verify that the resource exists:

```bash
kubectl --context k3d-host-cluster get virtualclustertemplate private-nodes-lima-auto
```

Use your configured host context and template ID if you changed the defaults.

In Platform, select **Private Nodes Template (Lima Auto)**. If you changed the default template ID during preparation, select that ID.

Alternatively, create the tenant with the CLI:

```bash
vcluster create lima-demo --driver platform \
  --template private-nodes-lima-auto \
  --project YOUR-PROJECT --cluster YOUR-CONNECTED-CLUSTER \
  --connect=false
```

| Value | What to use |
| --- | --- |
| `lima-demo` | The name for your new tenant. |
| `private-nodes-lima-auto` | The resource ID generated and installed by this project; replace it if you set `--template-name`. |
| `YOUR-PROJECT` | Your Platform project name. |
| `YOUR-CONNECTED-CLUSTER` | The connected cluster name in Platform, rather than the kubectl context name. |

`--connect=false` keeps your current kubeconfig context while creating the tenant.

### 3. Observe provisioning from the host

```bash
./setup.sh status
kubectl --context k3d-host-cluster get nodeclaims.storage.loft.sh -A
limactl list
```

Use your saved host context in place of `k3d-host-cluster` if you configured a different one.

Auto Nodes requests capacity when tenant Pods need it. The pool limit does not pre-create that many workers; tenant system Pods may trigger the first worker. Application requests must fit the remaining worker capacity.

### 4. Confirm readiness inside the tenant

```bash
vcluster connect lima-demo --project YOUR-PROJECT
kubectl get nodes -o wide
kubectl get pods -A
```

Confirm the worker's status is **Ready**. Helper health and completed bootstrap alone do not establish Kubernetes readiness.

`vcluster connect` switches your kubeconfig context into the tenant. Use explicit `--context` for subsequent host commands.

### Template defaults

| Setting | Generated configuration |
| --- | --- |
| Worker provisioning | Private nodes with a dynamic Auto Nodes pool. |
| Worker networking | VPN enabled, including node-to-node connectivity. |
| Control-plane backing store | Embedded etcd. |
| Pod CIDR | `10.66.0.0/16` |
| Service CIDR | `10.130.0.0/16` |

Use `--pod-cidr` and `--service-cidr` during preparation if these networks conflict with your environment. See the upstream [Auto Nodes configuration](https://www.vcluster.com/docs/vcluster/configure/vcluster-yaml/private-nodes/auto-nodes) for pool settings.

### Worker removal behavior

| Disruption setting | Generated value |
| --- | --- |
| `consolidationPolicy` | `WhenEmptyOrUnderutilized` |
| `consolidateAfter` | `1m` |

Auto Nodes handles scheduling and disruption decisions; the provider supplies the VM lifecycle. Actual removal depends on Auto Nodes, system Pods and disruption constraints. The one-minute setting is not a guarantee that a worker disappears one minute after scaling down.

The prototype demonstrated empty-worker deletion. Its short instance-type labels prevented some occupied-worker consolidation; that limitation remains unresolved here.

For a complete two-tenant exercise with a workload, shared capacity controls and VM deletion checks, follow the [autoscaling example](../examples/autoscaling/README.md). The [autoscaling validation record](autoscaling-validation.md) documents the observed environment and results.

## Private files and credentials

| Artifact under `.local/` | Purpose |
| --- | --- |
| `config.json` | Helper paths, profile, limits and saved Platform settings. |
| `api-token`, `server.key`, `server.crt` | Basic-auth password and TLS identity. |
| `node.tf`, `node-provider.yaml` | Rendered module and provider; contain the public CA and endpoint, without the password. |
| `credentials.json` | Credential Secret manifest containing the password; keep private. |
| `tenant-template.yaml` | Rendered private-node template and aggregate pool limits. |
| `launchagent.plist`, `helper.log`, `helper.err` | macOS service definition and private logs. |
| `nodes/*.json` | Durable ownership records needed for retries and safe deletion. |

The private directory uses mode `0700`; generated configuration, token, key and manifests use `0600`. The public certificate is pinned in the module. The helper requires authenticated TLS for every endpoint. Its default listener accepts connections on all Mac interfaces so Platform can reach it; restrict access to your trusted lab network. This is not a public multi-user service.

The Secret in the Platform namespace is labelled `terraform.vcluster.com/provider: <provider-name>`. Platform supplies `REST_API_PASSWORD` to the Mastercard REST client through the environment. Deployment creates or replaces this Secret without a credential-bearing last-applied annotation. It validates ownership before changing existing resources. The password is neither embedded in the node module nor passed in command-line arguments during health checks.

Generated credentials, ownership records, guest logs and OpenTofu state must stay private. A sensitive Terraform value is still stored in state. Preserve ownership records while workers exist. The certificate expires after one year; preparation reuses it only while valid and does not silently rotate a running installation's identity. See [SECURITY.md](../SECURITY.md) and the [helper trust boundary](local-helper.md#guest-bootstrap-boundary).

## Cleanup before removing Platform

Delete only tenants created for this installation, using Platform or the CLI:

```bash
vcluster delete lima-demo --driver platform --project YOUR-PROJECT
./setup.sh status
limactl list
```

Keep Platform and the helper running until the tenant's NodeClaims disappear, its `nodes/*.json` records are gone and its associated `vclima-*` VMs are deleted. This allows Platform to run OpenTofu destroy and release ownership through `DELETE /nodes/<id>`. Manually deleting arbitrary Lima VMs does not clear Platform state or helper reservations.

After managed cleanup, remove this installation's resources with the names saved during preparation:

```bash
kubectl --context k3d-host-cluster delete virtualclustertemplate private-nodes-lima-auto
kubectl --context k3d-host-cluster delete nodeprovider lima-local
kubectl --context k3d-host-cluster -n vcluster-platform delete secret lima-local-credentials
./setup.sh stop
```

Adapt these resource names, context, namespace and `--state-dir` for a custom installation. `stop` refuses to stop while managed records remain. It unregisters the macOS service but preserves private files. Remove those files only after confirming cleanup; do not delete another installation's state.

## Troubleshooting

- **Preparation cannot find the Platform hostname:** log the CLI into Platform or pass the browser hostname explicitly. Multiple configured hostnames require an explicit choice.
- **Helper starts with old sizing or limits:** `config_id` detects stale settings. Remove managed workers, stop the helper, prepare again and restart it before redeploying.
- **Platform cannot reach the helper:** check host networking, the chosen port, the certificate SAN and macOS firewall. `check` requires `sh`, `curl`, `base64` and `mktemp` in the Platform deployment. A successful local probe alone does not prove container-to-Mac connectivity.
- **An existing provider/template cannot be overwritten:** choose unique names and a separate private directory. Do not remove ownership guards to migrate the prototype's live resources.
- **NodeClaim stays Provisioning:** inspect Platform events, the private helper logs and guest `/var/log/vcluster-lima-bootstrap.log`. First-boot image downloads and registration can take several minutes. Avoid publishing unredacted logs or registration payloads.
- **Request returns capacity exhausted:** inspect both managed records and all `vclima-*` VMs in the selected Lima inventory. Missing or failed workers can retain reservations. Retry through Platform or delete the owning tenant; do not erase ownership files while resources remain.
- **A setup subprocess fails:** output is intentionally withheld because it may contain credentials. Inspect the named operation privately in your local environment. Do not enable REST provider debugging or publish full state files to investigate.

## Validation record

### Manual worker lifecycle

**Test date:** 2026-11-01

| Component | Tested configuration |
| --- | --- |
| Host | macOS ARM64 |
| Lima | 2.1.0 |
| vCluster Platform | 4.12.4 |
| Tenant chart | 0.37.1 |
| Worker size | 1 CPU / 2Gi RAM |

The smoke test verified the following sequence:

1. Prepared separate private state and deployed provider `lima-repo-smoke` with its tenant template.
2. Verified authenticated TLS connectivity from Platform to the Mac helper.
3. Created a tenant in a project that allowed the template and confirmed one worker reached Kubernetes `Ready`.
4. Deleted the tenant through Platform and confirmed its ownership record and Lima VM were removed.
5. Removed the temporary provider resources.

### Module integration

| Component | Local test configuration |
| --- | --- |
| CLI | Terraform 1.14.0 |
| REST client provider | Mastercard/restapi 3.0.0 |
| Transport | Real HTTPS |
| VM operations | Fake Lima; no real VMs created |
| Result | Passed |

The GitHub workflow is configured to repeat the integration test with **OpenTofu 1.10.6**. Local results do not establish that the remote workflow has passed.

**Validation scope:** This smoke test covers one worker in one observed environment. For the separate two-tenant scaling exercise, see the [autoscaling validation record](autoscaling-validation.md). These observations do not establish a support guarantee. Automated live-worker acceptance remains planned.
