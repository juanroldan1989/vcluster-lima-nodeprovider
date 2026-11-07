# Local helper lifecycle

This guide covers the local API and guest bootstrap adapter. Use the [installation guide](installation.md) for Platform integration and setup commands. Offline unit tests require no Platform, internet access, sudo or installed Lima; the separate OpenTofu integration test downloads its REST client provider.

The code was adapted from the project's working local experiment. The helper adds request validation, ownership-record checks, process-boundary tests and explicit configuration validation. Worker sizing is configurable; each helper installation accepts one shape at a time. Offline tests alone do not establish real-worker compatibility.

## Try the offline workflow

From the repository root with Python 3.11 or newer:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/check_repository.py
```

The host helper and tests use Python's standard library. The bootstrap unit tests supply JSON, a YAML subset, through a controlled parser stub so guest operations can be tested without installing PyYAML on the host. Actual YAML parsing requires PyYAML inside the Ubuntu guest; the helper installs `python3-yaml` before running the adapter. The real parser and guest environment are outside these offline tests.

Lifecycle tests invoke [fake_limactl.py](../tests/fake_limactl.py) through the real subprocess adapter. The fake records commands and simulates inventory using temporary files. It never invokes the real `limactl`, starts VMs or executes guest scripts. HTTP tests exercise the real request handler using in-memory socket streams; they do not open a listening port or validate a TLS handshake.

Covered behavior includes authentication, create/read/update/delete requests, stable retries, helper restart recovery, capacity reservations, concurrent claims, missing-VM repair, failed deletion, ownership mismatch, private record permissions and bootstrap command handling.

## API contract

The server in [helper/server.py](../helper/server.py) serves JSON over HTTPS with TLS 1.2 or newer. Every endpoint requires HTTP Basic authentication with username `platform` and a password read from a private token file. Startup requires a token of at least 32 bytes. Authentication is checked before reading a request body or operating on a VM.

| Method and path | Result |
| --- | --- |
| `GET /healthz` | Return `status: ok`, `max_nodes`, `worker_size` and a non-secret `config_id` used to detect stale settings. |
| `POST /nodes` | Reserve ownership, create or resume the VM and execute bootstrap. |
| `GET /nodes/<id>` | Return the ownership record; set `ready: false` when the VM is missing. An unknown record returns 404. |
| `PUT /nodes/<id>` | Retry creation or bootstrap for the same identity. The URL and body IDs must match. |
| `DELETE /nodes/<id>` | Delete the owned VM and then its record. Repeating deletion succeeds even if the record or VM is already absent. |

Create and update requests contain exactly these fields:

```json
{
  "id": "aaaaaaaaaaaaaaaaaaaaaaaa",
  "vm_name": "vclima-aaaaaaaaaaaaaaaaaaaaaaaa",
  "claim": "example-claim",
  "tenant": "example-tenant",
  "project": "p-example",
  "cpus": 1,
  "memory_gib": 2,
  "disk_gib": 30,
  "ready": true,
  "user_data": "IyEvYmluL3NoCnRydWUK"
}
```

The example payload is the base64 encoding of a synthetic shell script that runs `true`; it is not a real registration payload. The caller supplies a stable identity for the project and claim. The helper requires 24 lowercase hexadecimal characters and a VM name of `vclima-<id>`.

`ready: true` in a request means the caller requires completed bootstrap. In a stored record it means the registration command exited successfully. It does not prove that Kubernetes has marked the worker Ready. HTTP create/update responses wait synchronously for provisioning to finish.

The default worker shape is **1 CPU / 2Gi RAM / 30Gi disk**, Ubuntu 22.04 ARM64, using Lima's VZ backend and shared `user-v2` networking. Host directory mounts are disabled. Configure `worker_size` or use the setup sizing flags to choose another shape. All requests must match the configured shape; changing an existing record's identity or sizing is rejected.

| Status | Meaning |
| --- | --- |
| 200 | Operation completed or an idempotent retry succeeded. |
| 400 | Invalid JSON, fields, identity or worker shape. |
| 401 | Authentication failed. |
| 404 | Unknown route or missing managed-node record. |
| 405 | Unsupported method on a managed-node route. |
| 409 | Capacity exhausted, changed ownership or a conflicting VM/record. |
| 413 | Missing, empty or oversized request body; the limit is 2MiB. |
| 502 | A Lima operation failed or timed out. |

Errors omit subprocess output because bootstrap output can contain registration credentials. Unexpected internal failures return a generic 500 response. Some unimplemented HTTP methods are rejected by Python's HTTP server before routing.

## Ownership, retries and capacity

Ownership is saved before VM creation. Records contain the ID, VM name, claim, project, tenant, sizing and completion status. Bootstrap payloads are not saved in ownership records. Records use mode `0600` and the state directory uses `0700`.

A retry uses the same record and VM. Failed bootstrap leaves `ready: false` so a subsequent create/update can retry registration. See the troubleshooting guide for [bootstrap recovery](troubleshooting.md#nodeclaim-stays-provisioning-or-bootstrap-fails) and [missing or stopped VMs](troubleshooting.md#a-managed-vm-is-missing-or-stopped).

The helper counts the union of `vclima-*` VMs and reserved records against `max_nodes`. Unrecorded VMs with that prefix consume capacity but are not automatically adopted or deleted. Unrelated VM names are excluded from this count. A failed operation can retain a reserved slot until it is retried or cleaned up.

Operations are serialized by a lock in one helper process. This prevents concurrent requests from reserving the same last slot, but it also queues creation and deletion behind a long bootstrap operation. Multiple helper processes sharing the same state are unsupported. Preserve the records while workers exist; losing them requires manual reconciliation.

Deletion verifies the record's ID and VM name before invoking `limactl delete --force`. The record is removed only after successful deletion or confirmed VM absence. A failed deletion keeps the record for retry. Deletion is not a Kubernetes drain operation; the caller must retire the worker through the managed lifecycle before requesting VM deletion.

## Configuration and startup

[helper-config.example.json](../examples/helper-config.example.json) documents the configuration shape. It contains example paths and a placeholder Platform hostname, not usable credentials. Copy it into a private directory such as `.local/config.json` only when preparing a real helper manually. Relative file paths resolve beside the configuration file; shell variables are not expanded. Adjust the absolute `limactl` path for your machine.

| Setting | Meaning |
| --- | --- |
| `listen_host` / `port` | Explicit bind address and TCP port. The example binds only to loopback. |
| `max_nodes` | Positive integer shared across tenants using this helper; the setup default is 8. No automatic host-resource calculation is performed. |
| `worker_size` | Positive integer `cpus`, `memory_gib` and `disk_gib`; defaults to 1 / 2 / 30 when omitted. |
| `lima_home` | Optional Lima inventory directory, passed as `LIMA_HOME`; omitted to use Lima's normal inventory. |
| `state_dir` | Durable ownership records; keep this across helper restarts. |
| `limactl` | Executable path used for VM operations. |
| `token_file` | Private shared password file containing at least 32 bytes. |
| `cert_file` / `key_file` | Server certificate chain and matching private key. |
| `platform_hosts` | Hostnames without a scheme or path, used for guest connectivity checks. |

With a valid private configuration, token and certificate in place, the developer entry point is:

```bash
python3 -m helper.server --config .local/config.json
```

This command starts a real listener. Its API can create real VMs when clients submit requests; it is separate from the offline test workflow. The [setup commands](installation.md#prepare-and-deploy) prepare these files and manage a macOS user service. Platform running in Kubernetes needs a reachable bind address, trusted certificate and credentials; the loopback example alone does not provide that connection.

## Guest bootstrap boundary

[guest_bootstrap.py](../helper/guest_bootstrap.py) executes trusted Platform registration data as root inside the guest. It is a small cloud-config adapter, not a full cloud-init implementation or a sandbox for untrusted scripts.

Supported payloads are base64-encoded shebang scripts or cloud-config, optionally gzip-compressed. Cloud-config requires `runcmd` and supports `bootcmd`, package update/upgrade, packages, `write_files`, hostname/FQDN, `preserve_hostname` and a literal `final_message`. File writing supports absolute paths, owner, permissions, append and plain/base64/gzip encodings. Unsupported top-level modules and file encodings fail explicitly; cloud-init stage scheduling, templates and its full per-module option set are not implemented.

The adapter checks the normal Platform DNS address first. A Lima gateway mapping is a fallback after a failed connectivity probe; a failed fallback restores the previous hosts content. The reachability probe uses `curl --insecure` for local/test endpoints and is not a server-identity check. Certificate verification for registration depends on the supplied registration commands.

Generated script output is redirected to the root-readable guest file `/var/log/vcluster-lima-bootstrap.log`. Temporary shell scripts use mode `0700` and are removed after execution. Logs can contain registration secrets and should be inspected privately. Ownership JSON and HTTP errors do not include bootstrap payloads.

## Platform integration

The [installation guide](installation.md) documents the OpenTofu module, Platform resources, private credentials and managed cleanup. The offline checks do not modify Platform objects or live VMs. Run `python3 tests/integration_platform.py` to exercise the real module over TLS against fake Lima; it creates no VMs but requires a provider download and a local listening socket.

The [two-tenant autoscaling example](../examples/autoscaling/README.md) demonstrates the shared budget, worker observations and cleanup. Use the [troubleshooting scenario index](troubleshooting.md#scenario-index) for helper errors, capacity issues and interrupted operations.

Use `./setup.sh teardown` for complete managed cleanup. It preserves the helper and credentials until its mapped workers are gone, then removes generated local files. See the [teardown guide](installation.md#cleanup-before-removing-platform) for scope and the [troubleshooting guide](troubleshooting.md#teardown-fails-or-times-out) for recovery.
