# SPDX-License-Identifier: Apache-2.0
"""Prepare and deploy the local Lima NodeProvider without printing credentials."""
import argparse
import base64
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import plistlib
import re
import secrets
import shutil
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


def start(args):
    config = configuration(args)
    domain = service_domain(config)
    existing = subprocess.run(["launchctl", "print", domain], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if existing.returncode:
        run(["launchctl", "bootstrap", domain.rsplit("/", 1)[0], str(local_directory(args) / "launchagent.plist")])
    for _ in range(20):
        try:
            print("Helper health:", health(config))
            return
        except (OSError, urllib.error.URLError):
            time.sleep(0.25)
    raise RuntimeError("Helper did not become healthy; inspect its private helper.err file")


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


def ensure_owner(resource, directory):
    if resource is None:
        return
    metadata = resource.get("metadata", {})
    if metadata.get("labels", {}).get("app.kubernetes.io/managed-by") != MANAGER or metadata.get("annotations", {}).get(OWNER) != helper_id(directory):
        raise ValueError("Refusing to overwrite an existing resource owned by another installation; choose distinct provider/template names")


def deploy(args):
    config = configuration(args)
    directory = local_directory(args)
    check(args)
    platform = config["platform"]
    for resource, name, namespace in (("nodeproviders.storage.loft.sh", platform["provider"], None),
                                       ("virtualclustertemplates.storage.loft.sh", platform["template"], None),
                                       ("secret", platform["provider"] + "-credentials", platform["namespace"])):
        ensure_owner(get_resource(config, resource, name, namespace), directory)
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
    for resource in ("nodeproviders.storage.loft.sh", "nodetypes.storage.loft.sh", "nodeclaims.storage.loft.sh"):
        result = run(kube(config, "get", resource, "-A"))
        print(result.stdout.decode().strip())


def stop(args):
    config = configuration(args)
    if list(Path(config["state_dir"]).glob("*.json")):
        raise ValueError("Managed nodes remain; deprovision them before stopping the helper")
    domain = service_domain(config)
    existing = subprocess.run(["launchctl", "print", domain], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if existing.returncode == 0:
        run(["launchctl", "bootout", domain])
    print("Helper stopped; private credentials and state directory preserved.")


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--state-dir", type=Path, default=ROOT / ".local")
    result.add_argument("--context", help="Host context saved by prepare")
    result.add_argument("--namespace", help="Platform namespace saved by prepare")
    commands = result.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    for flag, default in (("port", 18443), ("max-nodes", 8), ("cpus", 1), ("memory-gib", 2), ("disk-gib", 30)):
        prepare_parser.add_argument("--" + flag, type=positive, default=default)
    prepare_parser.add_argument("--pool-nodes", type=positive)
    for flag, default in (("platform-host", None), ("api-url", None), ("listen-host", WILDCARD_HOSTS[0]),
                          ("provider-name", "lima-local"), ("template-name", "private-nodes-lima-auto"),
                          ("deployment", "loft"), ("chart-version", "0.37.1"), ("pod-cidr", "10.66.0.0/16"),
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
