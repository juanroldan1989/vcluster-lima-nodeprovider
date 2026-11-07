# SPDX-License-Identifier: Apache-2.0
"""Prepare and deploy the local Lima NodeProvider without printing credentials."""
import argparse
import base64
import errno
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import plistlib
import re
import secrets
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import textwrap
import time
import urllib.error
from urllib.parse import urlparse

from helper.server import configuration_id, load_config

ROOT = Path(__file__).resolve().parents[1]
MANAGER = "vcluster-lima-nodeprovider"
OWNER = "lima.automatalife.com/helper-id"
# Platform must reach the TLS/authenticated helper from the host cluster.
DEFAULT_LISTEN_HOST = "0.0.0.0"  # nosec B104
WILDCARD_HOSTS = (DEFAULT_LISTEN_HOST, "::")
STARTUP_TIMEOUT = 30


def run(command, **kwargs):
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    if result.returncode:
        # A subprocess or Kubernetes error can contain a credential-bearing body.
        raise RuntimeError(f"{Path(command[0]).name} failed (exit {result.returncode}); inspect the operation privately")
    return result


def write_private(path, content):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(content)


def platform_hostname(path=None):
    path = path or Path.home() / ".vcluster/config.json"
    if not path.exists():
        raise ValueError("Log the vCluster CLI into Platform or pass --platform-host")
    hosts = set()

    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "host" and isinstance(item, str):
                    hostname = urlparse(item if "://" in item else "https://" + item).hostname
                    if hostname:
                        hosts.add(hostname)
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(json.loads(path.read_text()).get("platform", {}))
    if len(hosts) != 1:
        raise ValueError("Platform hostname is missing or ambiguous; pass --platform-host")
    return hosts.pop()


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Value must be a positive integer")
    return number


def name(value, maximum=63):
    if len(value) > maximum or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", value):
        raise ValueError("Resource names must be lowercase DNS labels within the supported length")
    return value


def endpoint(value):
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("--api-url must be an HTTPS origin without credentials or a path")
    # Accessing port also rejects malformed/out-of-range values.
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("Invalid helper API port")
    return value.rstrip("/")


def local_directory(args):
    directory = args.state_dir.expanduser().resolve()
    if directory == ROOT or (ROOT in directory.parents and ROOT / ".local" not in (directory, *directory.parents)):
        raise ValueError("Use .local/ for generated files inside the repository, or choose a directory outside it")
    return directory


def helper_id(directory):
    return hashlib.sha256(str(Path(directory).resolve()).encode()).hexdigest()[:16]


def render_terraform(api_url, certificate, worker_size):
    source = (ROOT / "terraform/node/main.tf.tmpl").read_text()
    replacements = {
        '"__LIMA_API_URL__"': json.dumps(endpoint(api_url)),
        '"__LIMA_CA_PEM__"': json.dumps(certificate),
        "__WORKER_CPUS__": str(worker_size["cpus"]),
        "__WORKER_MEMORY_GIB__": str(worker_size["memory_gib"]),
        "__WORKER_DISK_GIB__": str(worker_size["disk_gib"]),
    }
    for old, new in replacements.items():
        source = source.replace(old, new)
    return source


def render_manifests(config, directory, certificate):
    platform = config["platform"]
    worker = config["worker_size"]
    pool_nodes = config["pool_nodes"]
    node_type = name(f'{platform["provider"]}-{worker["cpus"]}cpu-{worker["memory_gib"]}gi')
    module = render_terraform(config["api_url"], certificate, worker)
    replacements = {
        "PROVIDER_NAME": platform["provider"], "TEMPLATE_NAME": platform["template"],
        "NODE_TYPE": node_type, "HELPER_ID": helper_id(directory),
        "CPUS": worker["cpus"], "MEMORY_GIB": worker["memory_gib"],
        "MAX_NODES": config["max_nodes"], "POOL_NODES": pool_nodes,
        "POOL_CPUS": pool_nodes * worker["cpus"], "POOL_MEMORY_GIB": pool_nodes * worker["memory_gib"],
        "CHART_VERSION": platform["chart_version"], "POD_CIDR": config["pod_cidr"],
        "SERVICE_CIDR": config["service_cidr"], "TERRAFORM_INLINE": textwrap.indent(module.rstrip(), " " * 8),
    }
    rendered = {}
    for filename in ("node-provider.yaml", "tenant-template.yaml"):
        source = (ROOT / "platform" / (filename + ".tmpl")).read_text()
        for key, value in replacements.items():
            source = source.replace("@@" + key + "@@", str(value))
        if re.search(r"@@[A-Z_]+@@", source):
            raise ValueError("Unresolved manifest placeholder")
        rendered[filename] = source
    return module, rendered


def certificate(directory, api_url, listen_host):
    cert, key = directory / "server.crt", directory / "server.key"
    if cert.exists() != key.exists():
        raise ValueError("Certificate/key pair is incomplete; reconcile it before preparation")
    if cert.exists():
        run(["openssl", "x509", "-in", str(cert), "-checkend", "0", "-noout"])
        return cert.read_text()
    names = {"localhost", "host.docker.internal", "127.0.0.1", urlparse(api_url).hostname}
    if listen_host not in WILDCARD_HOSTS:
        names.add(listen_host)
    entries = []
    for host in sorted(names):
        try:
            ipaddress.ip_address(host)
            entries.append("IP:" + host)
        except ValueError:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", host):
                raise ValueError("Invalid certificate hostname") from None
            entries.append("DNS:" + host)
    openssl = """[req]
distinguished_name = subject
x509_extensions = extensions
prompt = no
[subject]
CN = vcluster-lima-nodeprovider
[extensions]
basicConstraints = critical,CA:TRUE
keyUsage = critical,digitalSignature,keyEncipherment,keyCertSign
extendedKeyUsage = serverAuth
subjectAltName = """ + ",".join(entries) + "\n"
    write_private(directory / "openssl.cnf", openssl)
    run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-days", "365", "-nodes",
         "-keyout", str(key), "-out", str(cert), "-config", str(directory / "openssl.cnf")])
    os.chmod(key, 0o600)
    return cert.read_text()


def prepare(args):
    directory = local_directory(args)
    if not 1 <= args.port <= 65535:
        raise ValueError("Invalid listen port")
    provider = name(args.provider_name, maximum=40)
    namespace = name(args.namespace or "vcluster-platform")
    hostname = args.platform_host or platform_hostname()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", hostname):
        raise ValueError("Platform hostname must not contain a scheme or path")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", args.chart_version):
        raise ValueError("Chart version must be an explicit version")
    networks = [ipaddress.ip_network(value) for value in (args.pod_cidr, args.service_cidr)]
    if any(net.version != 4 for net in networks) or networks[0].overlaps(networks[1]):
        raise ValueError("Pod and service CIDRs must be distinct IPv4 networks")
    pool_nodes = args.pool_nodes or min(4, args.max_nodes)
    if pool_nodes > args.max_nodes:
        raise ValueError("Tenant pool node limit must not exceed the shared node limit")
    limactl = shutil.which(args.limactl or "limactl")
    if not limactl or not shutil.which("openssl"):
        raise ValueError("Preparation requires limactl and openssl")
    config = {
        "listen_host": args.listen_host, "port": args.port, "max_nodes": args.max_nodes,
        "worker_size": {"cpus": args.cpus, "memory_gib": args.memory_gib, "disk_gib": args.disk_gib},
        "state_dir": str(directory / "nodes"), "limactl": str(Path(limactl).resolve()),
        "token_file": str(directory / "api-token"), "cert_file": str(directory / "server.crt"),
        "key_file": str(directory / "server.key"), "platform_hosts": [hostname],
        "api_url": endpoint(args.api_url or f"https://host.docker.internal:{args.port}"),
        "pool_nodes": pool_nodes, "pod_cidr": args.pod_cidr, "service_cidr": args.service_cidr,
        "platform": {"context": args.context or "k3d-host-cluster", "namespace": namespace,
                     "deployment": name(args.deployment), "provider": provider,
                     "template": name(args.template_name), "chart_version": args.chart_version},
        "service_label": "com.github.juanroldan1989." + provider,
    }
    if args.lima_home:
        config["lima_home"] = str(args.lima_home.expanduser().resolve())
    if list((directory / "nodes").glob("*.json")) and (directory / "config.json").exists():
        previous = json.loads((directory / "config.json").read_text())
        if previous != config:
            raise ValueError("Managed workers exist; deprovision them before changing prepared settings")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    cert = certificate(directory, config["api_url"], args.listen_host)
    # A reused certificate must cover the Platform-facing hostname.
    api_host = urlparse(config["api_url"]).hostname
    try:
        ipaddress.ip_address(api_host)
        check_option = "-checkip"
    except ValueError:
        check_option = "-checkhost"
    checked = run(["openssl", "x509", "-in", config["cert_file"], check_option, api_host, "-noout"])
    if b"does NOT match" in checked.stdout:
        raise ValueError("Existing certificate does not cover --api-url; reconcile certificates before preparation")
    token = Path(config["token_file"])
    if not token.exists():
        write_private(token, secrets.token_urlsafe(48) + "\n")
    if len(token.read_text().strip().encode()) < 32:
        raise ValueError("Existing helper token is too short")
    os.chmod(token, 0o600)
    module, manifests = render_manifests(config, directory, cert)
    write_private(directory / "config.json", json.dumps(config, indent=2) + "\n")
    write_private(directory / "node.tf", module)
    for filename, source in manifests.items():
        write_private(directory / filename, source)
    secret = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
              "metadata": {"name": provider + "-credentials", "namespace": namespace,
                           "labels": {"terraform.vcluster.com/provider": provider, "app.kubernetes.io/managed-by": MANAGER},
                           "annotations": {OWNER: helper_id(directory)}},
              "stringData": {"REST_API_PASSWORD": token.read_text().strip()}}
    write_private(directory / "credentials.json", json.dumps(secret, indent=2) + "\n")
    plist = {"Label": config["service_label"], "ProgramArguments": [sys.executable, "-m", "helper.server", "--config", str(directory / "config.json")],
             "WorkingDirectory": str(ROOT), "RunAtLoad": True, "KeepAlive": True,
             "StandardOutPath": str(directory / "helper.log"), "StandardErrorPath": str(directory / "helper.err")}
    write_private(directory / "launchagent.plist", plistlib.dumps(plist).decode())
    print("Prepared private files under", directory)
    print(f"Provider: {provider}; worker: {args.cpus} CPU / {args.memory_gib}Gi; shared cap: {args.max_nodes}; pool cap: {pool_nodes}")


def configuration(args):
    path = local_directory(args) / "config.json"
    if not path.exists():
        raise ValueError("Run ./setup.sh prepare first")
    config = load_config(path)
    for option, key in ((args.context, "context"), (args.namespace, "namespace")):
        if option is not None and option != config["platform"][key]:
            raise ValueError("Platform settings differ from preparation; re-prepare with the intended settings")
    return config


def health(config):
    context = ssl.create_default_context(cafile=config["cert_file"])
    password = Path(config["token_file"]).read_text().strip()
    auth = base64.b64encode(("platform:" + password).encode()).decode()
    host = config["listen_host"] if config["listen_host"] not in WILDCARD_HOSTS else "localhost"
    connection = http.client.HTTPSConnection(host, config["port"], context=context, timeout=10)
    try:
        connection.request("GET", "/healthz", headers={"Authorization": "Basic " + auth})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("Helper health request failed; check private credentials and configuration")
        data = json.load(response)
    finally:
        connection.close()
    if data.get("config_id") != configuration_id(config):
        raise ValueError("Helper is running different settings; stop it before re-preparing configuration")
    return data


def service_domain(config):
    if sys.platform != "darwin":
        raise ValueError("Helper service commands require macOS; use python3 -m helper.server for foreground testing")
    return "gui/" + str(os.getuid()) + "/" + config["service_label"]


def port_in_use(config):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            # Match HTTPServer's reuse behavior so TIME_WAIT is not a conflict.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((config["listen_host"], config["port"]))
    except OSError as error:
        if error.errno == errno.EADDRINUSE:
            return True
        raise RuntimeError("Cannot bind the helper listen address: " + str(error.strerror)) from None
    return False


def start(args):
    config = configuration(args)
    domain = service_domain(config)
    existing = subprocess.run(["launchctl", "print", domain], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    conflict = (
        f"TCP port {config['port']} is already in use; its listener does not match this helper's "
        "prepared TLS, credentials or configuration. "
        f"Identify it with: lsof -nP -iTCP:{config['port']} -sTCP:LISTEN. "
        "Stop the conflicting service, then run ./setup.sh stop and ./setup.sh start. "
        "Alternatively, re-prepare with a free --port and your complete worker/capacity settings."
    )
    if port_in_use(config):
        try:
            status = health(config)
        except (OSError, ValueError, http.client.HTTPException):
            raise RuntimeError(conflict) from None
        if existing.returncode:
            raise RuntimeError(
                f"TCP port {config['port']} already serves this helper outside its launchd service; "
                "stop that foreground process before starting the service."
            )
        print("Helper health:", status)
        return
    if existing.returncode:
        run(["launchctl", "bootstrap", domain.rsplit("/", 1)[0], str(local_directory(args) / "launchagent.plist")])
    print(f"Waiting for helper startup (up to {STARTUP_TIMEOUT}s)...")
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while True:
        try:
            print("Helper health:", health(config))
            return
        except (OSError, urllib.error.URLError):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.5, remaining))
    if port_in_use(config):
        raise RuntimeError(conflict)
    raise RuntimeError(
        f"Helper did not become healthy within {STARTUP_TIMEOUT} seconds; "
        f"inspect the private log: {local_directory(args) / 'helper.err'}"
    )


def kube(config, *command, namespace=None):
    args = ["kubectl", "--context", config["platform"]["context"]]
    if namespace:
        args.extend(["--namespace", namespace])
    return [*args, *command]


def get_resource(config, resource, name, namespace=None):
    result = run(kube(config, "get", resource, name, "--ignore-not-found", "-o", "json", namespace=namespace))
    return json.loads(result.stdout) if result.stdout.strip() else None


def check(args):
    config = configuration(args)
    print("Helper health:", health(config))
    ca = base64.b64encode(Path(config["cert_file"]).read_bytes()).decode()
    # Only the public certificate is embedded in the command. Password goes through stdin.
    shell = 'set -e; ca_file=$(mktemp); trap \'rm -f "$ca_file"\' EXIT; printf %s ' + ca + ' | base64 -d > "$ca_file"; curl --silent --show-error --fail --cacert "$ca_file" --config -'
    token = Path(config["token_file"]).read_text().strip()
    curl_config = 'url = ' + json.dumps(config["api_url"] + "/healthz") + '\nuser = ' + json.dumps("platform:" + token) + '\n'
    result = run(kube(config, "exec", "-i", "deploy/" + config["platform"]["deployment"], "--", "sh", "-c", shell,
                      namespace=config["platform"]["namespace"]), input=curl_config.encode())
    if json.loads(result.stdout).get("config_id") != configuration_id(config):
        raise ValueError("Platform reached a different helper configuration")
    print("Platform-to-helper TLS and authentication passed.")


def ownership_conflict(resource, directory):
    if resource is None:
        return None
    metadata = resource.get("metadata", {})
    labels = metadata.get("labels") or {}
    annotations = metadata.get("annotations") or {}
    reasons = []
    if labels.get("app.kubernetes.io/managed-by") != MANAGER:
        reasons.append("missing or different managed-by label")
    if annotations.get(OWNER) != helper_id(directory):
        reasons.append("missing or different installation ID")
    if not reasons:
        return None
    identity = "/".join(filter(None, (resource.get("kind", "Resource"), metadata.get("namespace"), metadata.get("name", "unknown"))))
    return identity + ": " + "; ".join(reasons)


def ensure_owner(resource, directory):
    conflict = ownership_conflict(resource, directory)
    if conflict:
        raise ValueError("Refusing to overwrite " + conflict)


def setup_command(directory, action):
    command = ["./setup.sh"]
    if Path(directory).resolve() != (ROOT / ".local").resolve():
        command.extend(["--state-dir", str(Path(directory).resolve())])
    return [*command, action]


def reprepare_command(config, directory, provider, template):
    """Preserve prepared settings while suggesting different resource names."""
    defaults = parser().parse_args(["prepare"])
    platform = config["platform"]
    command = setup_command(directory, "prepare")
    global_options = []
    for flag, actual, default in (("--context", platform["context"], "k3d-host-cluster"),
                                  ("--namespace", platform["namespace"], "vcluster-platform")):
        if actual != default:
            global_options.extend([flag, actual])
    command[-1:-1] = global_options
    worker = config["worker_size"]
    for flag, value in (("--platform-host", config["platform_hosts"][0]),
                         ("--cpus", worker["cpus"]), ("--memory-gib", worker["memory_gib"]),
                         ("--disk-gib", worker["disk_gib"]), ("--max-nodes", config["max_nodes"]),
                         ("--pool-nodes", config["pool_nodes"]),
                         ("--provider-name", provider), ("--template-name", template)):
        command.extend([flag, str(value)])
    for flag, value, default in (
        ("--port", config["port"], defaults.port),
        ("--api-url", config["api_url"], f"https://host.docker.internal:{config['port']}"),
        ("--listen-host", config["listen_host"], defaults.listen_host),
        ("--deployment", platform["deployment"], defaults.deployment),
        ("--chart-version", platform["chart_version"], defaults.chart_version),
        ("--pod-cidr", config["pod_cidr"], defaults.pod_cidr),
        ("--service-cidr", config["service_cidr"], defaults.service_cidr),
    ):
        if value != default:
            command.extend([flag, str(value)])
    default_limactl = shutil.which("limactl")
    if not default_limactl or Path(config["limactl"]).resolve() != Path(default_limactl).resolve():
        command.extend(["--limactl", config["limactl"]])
    if config.get("lima_home"):
        command.extend(["--lima-home", config["lima_home"]])
    return command


def shell_command(command):
    groups = []
    index = 0
    while index < len(command):
        group = [str(command[index])]
        if group[0].startswith("--") and index + 1 < len(command):
            index += 1
            group.append(str(command[index]))
        groups.append(shlex.join(group))
        index += 1
    lines = []
    line = "  "
    for group in groups:
        if line.strip() and len(line) + len(group) + 1 > 100:
            lines.append(line + " " + chr(92))
            line = "    " + group
        else:
            line += (" " if line.strip() else "") + group
    return "\n".join([*lines, line])


def deployment_conflict(config, directory, conflicts):
    platform = config["platform"]
    suffix = secrets.token_hex(3)
    provider = platform["provider"][:33].rstrip("-") + "-" + suffix
    template = platform["template"][:56].rstrip("-") + "-" + suffix
    lines = [
        f"Deployment blocked by {len(conflicts)} existing Platform " + ("resource." if len(conflicts) == 1 else "resources."),
        f"Host context: {platform['context']}",
        f"Installation directory: {Path(directory).resolve()}",
        *["- " + conflict for conflict in conflicts],
        "",
        "These resources lack ownership metadata matching this installation.",
        "Deployment stopped before changing any Platform resources.",
        "",
        "To remove your earlier setup and reuse the original names:",
        shell_command([*setup_command(directory, "teardown"), "--dry-run"]),
        shell_command(setup_command(directory, "teardown")),
        "Cleanup deletes this installation's tenants and their data. Repeat prepare/start/check/deploy afterward.",
        "For inactive older resources without ownership metadata, add --include-unmanaged to both commands.",
        "Resources owned by another installation require its original private directory.",
        "",
        "To keep the existing resources, choose different names instead:",
        "Run the following from the repository root; your current settings are preserved:",
    ]
    if list(Path(config["state_dir"]).glob("*.json")):
        lines.append("First remove this installation's managed tenants and wait for worker cleanup.")
    lines.extend([
        shell_command(setup_command(directory, "stop")),
        shell_command(reprepare_command(config, directory, provider, template)),
        shell_command(setup_command(directory, "start")),
        shell_command(setup_command(directory, "check")),
        shell_command(setup_command(directory, "deploy")),
        "",
        f"When creating tenants, use --template {template} and allow that ID in your project.",
        "",
        "Guide: docs/troubleshooting.md#resolve-existing-resource-conflicts",
    ])
    return "\n".join(lines)


def deploy(args):
    config = configuration(args)
    directory = local_directory(args)
    check(args)
    platform = config["platform"]
    conflicts = []
    for resource, name, namespace in (("nodeproviders.storage.loft.sh", platform["provider"], None),
                                       ("virtualclustertemplates.storage.loft.sh", platform["template"], None),
                                       ("secret", platform["provider"] + "-credentials", platform["namespace"])):
        conflict = ownership_conflict(get_resource(config, resource, name, namespace), directory)
        if conflict:
            conflicts.append(conflict)
    if conflicts:
        raise ValueError(deployment_conflict(config, directory, conflicts))
    for filename in ("node-provider.yaml", "tenant-template.yaml", "credentials.json"):
        run(kube(config, "apply", "--dry-run=server", "-f", str(directory / filename)))
    secret = json.loads((directory / "credentials.json").read_text())
    existing = get_resource(config, "secret", secret["metadata"]["name"], platform["namespace"])
    operation = "create"
    if existing:
        secret["metadata"]["resourceVersion"] = existing["metadata"]["resourceVersion"]
        operation = "replace"
    run(kube(config, operation, "-f", "-"), input=json.dumps(secret).encode())
    for filename in ("node-provider.yaml", "tenant-template.yaml"):
        run(kube(config, "apply", "-f", str(directory / filename)))
    print("Installed provider and tenant template. No tenant or VM was created by this command.")


def status(args):
    config = configuration(args)
    print("Helper health:", health(config))
    for record in sorted(Path(config["state_dir"]).glob("*.json")):
        data = json.loads(record.read_text())
        print(data["vm_name"], "claim=" + data["claim"], "bootstrap-complete=" + str(data["ready"]))
    for title, resource in (("NodeProviders", "nodeproviders.storage.loft.sh"),
                            ("Node types", "nodetypes.storage.loft.sh"),
                            ("NodeClaims", "nodeclaims.storage.loft.sh")):
        result = run(kube(config, "get", resource, "-A"))
        print(title + ":")
        print(result.stdout.decode().strip() or "No resources found.")


def stop(args):
    config = configuration(args)
    if list(Path(config["state_dir"]).glob("*.json")):
        raise ValueError("Managed nodes remain; deprovision them before stopping the helper")
    domain = service_domain(config)
    existing = subprocess.run(["launchctl", "print", domain], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if existing.returncode == 0:
        process = re.search(rb"^\s*pid = ([0-9]+)\s*$", existing.stdout, re.M)
        run(["launchctl", "bootout", domain])
        if process:
            pid = int(process.group(1))
            deadline = time.monotonic() + STARTUP_TIMEOUT
            while True:
                try:
                    os.kill(pid, 0)  # Query this service's process; do not signal it.
                except ProcessLookupError:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        f"Helper service stop requested, but process {pid} has not exited; "
                        "inspect the private helper.err log before restarting."
                    )
                time.sleep(min(0.1, remaining))
    print("Helper stopped; private credentials and state directory preserved.")


def teardown(args):
    from helper.cleanup import run_teardown
    run_teardown(args)


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--state-dir", type=Path, default=ROOT / ".local")
    result.add_argument("--context", help="Host context saved by prepare")
    result.add_argument("--namespace", help="Platform namespace saved by prepare")
    commands = result.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    teardown_parser = commands.add_parser("teardown", help="Remove this installation and its managed tenants/workers")
    teardown_parser.add_argument("--dry-run", action="store_true", help="Preview without making changes")
    teardown_parser.add_argument("--include-unmanaged", action="store_true", help="Include inactive legacy resources at the configured names")
    teardown_parser.add_argument("--timeout", type=positive, default=900, help="Cleanup timeout in seconds")
    teardown_parser.set_defaults(action=teardown)
    for flag, default in (("port", 18443), ("max-nodes", 8), ("cpus", 1), ("memory-gib", 2), ("disk-gib", 30)):
        prepare_parser.add_argument("--" + flag, type=positive, default=default)
    prepare_parser.add_argument("--pool-nodes", type=positive)
    for flag, default in (("platform-host", None), ("api-url", None), ("listen-host", WILDCARD_HOSTS[0]),
                          ("provider-name", "lima-local"), ("template-name", "private-nodes-lima-auto"),
                          ("deployment", "loft"), ("chart-version", "0.37.4"), ("pod-cidr", "10.66.0.0/16"),
                          ("service-cidr", "10.130.0.0/16"), ("limactl", None)):
        prepare_parser.add_argument("--" + flag, default=default)
    prepare_parser.add_argument("--lima-home", type=Path)
    for command, action in (("prepare", prepare), ("start", start), ("check", check), ("deploy", deploy), ("status", status), ("stop", stop)):
        subparser = prepare_parser if command == "prepare" else commands.add_parser(command)
        subparser.set_defaults(action=action)
    return result


def main():
    args = parser().parse_args()
    try:
        args.action(args)
    except (ValueError, RuntimeError, OSError) as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main()
