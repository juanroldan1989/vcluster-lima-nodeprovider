# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The vcluster-lima-nodeprovider contributors
"""Authenticated local-lab VM API. Runs as the macOS user who owns Lima."""
import argparse
import base64
import hmac
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ID = re.compile(r"^[a-f0-9]{24}$")
MAX_BODY = 2 * 1024 * 1024
DEFAULT_WORKER_SIZE = {"cpus": 1, "memory_gib": 2, "disk_gib": 30}


def normalize_resources(config):
    if type(config.get("max_nodes")) is not int or config["max_nodes"] < 1:
        raise ValueError("max_nodes must be a positive integer")
    size = config.setdefault("worker_size", dict(DEFAULT_WORKER_SIZE))
    if not isinstance(size, dict) or set(size) != set(DEFAULT_WORKER_SIZE):
        raise ValueError("worker_size must define cpus, memory_gib and disk_gib")
    if any(type(value) is not int or value < 1 for value in size.values()):
        raise ValueError("Worker resources must be positive integers")
    return config


def configuration_id(config):
    fields = ("listen_host", "port", "max_nodes", "worker_size", "platform_hosts", "state_dir", "limactl", "lima_home")
    data = {field: config.get(field) for field in fields}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


class APIError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class Lima:
    def __init__(self, config):
        self.config = normalize_resources(dict(config))
        self.state = Path(config["state_dir"])
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.state, 0o700)
        self.lock = threading.RLock()

    def command(self, args, stdin=None, timeout=120):
        try:
            return subprocess.run(
                [self.config["limactl"], "--tty=false", *args],
                input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=dict(os.environ, LIMA_HOME=self.config["lima_home"]) if self.config.get("lima_home") else None,
                timeout=timeout, check=True,
            ).stdout
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            # Bootstrap output can contain registration tokens. Never return/log it.
            raise APIError(502, "Lima operation failed; inspect the VM and its private bootstrap log") from None

    def path(self, identity):
        if not isinstance(identity, str) or not ID.fullmatch(identity):
            raise APIError(400, "Invalid node identity")
        return self.state / (identity + ".json")

    def save(self, node):
        path = self.path(node["id"])
        temporary = path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(node, stream)
        temporary.replace(path)

    def load(self, identity):
        try:
            node = json.loads(self.path(identity).read_text())
            if node.get("id") != identity or node.get("vm_name") != "vclima-" + identity:
                raise APIError(409, "Ownership record does not match its VM identity")
            return node
        except FileNotFoundError:
            raise APIError(404, "Managed node not found") from None

    def exists(self, name):
        # A named `list` exits 1 when the VM does not exist. List inventory
        # instead, so expected absence is not treated as a failed operation.
        # JSON is emitted as one object per line by Lima 2.1.
        result = self.command(["list", "--format=json"]).decode()
        decoder = json.JSONDecoder()
        while result.strip():
            item, end = decoder.raw_decode(result.lstrip())
            result = result.lstrip()[end:]
            if isinstance(item, list):
                return any(entry.get("name") == name for entry in item)
            if item.get("name") == name:
                return True
        return False

    def validate(self, request):
        allowed = {"id", "vm_name", "claim", "tenant", "project", "cpus", "memory_gib", "disk_gib", "user_data", "ready"}
        if not isinstance(request, dict) or set(request) != allowed:
            raise APIError(400, "Unexpected or missing node fields")
        self.path(request["id"])
        if request["vm_name"] != "vclima-" + request["id"]:
            raise APIError(400, "VM name must match its claim identity")
        for field in ("claim", "tenant", "project"):
            if not isinstance(request[field], str) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,252}", request[field]):
                raise APIError(400, "Invalid claim or tenant metadata")
        size = tuple(request[field] for field in ("cpus", "memory_gib", "disk_gib"))
        expected = tuple(self.config["worker_size"][field] for field in ("cpus", "memory_gib", "disk_gib"))
        if any(type(value) is not int for value in size) or size != expected:
            raise APIError(400, "Worker resources must match the configured node type")
        if request["ready"] is not True:
            raise APIError(400, "A managed node must request completed bootstrap")
        if not isinstance(request["user_data"], str):
            raise APIError(400, "user_data must be a base64 string")
        try:
            bootstrap = base64.b64decode(request["user_data"], validate=True)
        except (ValueError, TypeError):
            raise APIError(400, "user_data must be base64-encoded bootstrap data") from None
        if not bootstrap or len(bootstrap) > MAX_BODY // 2:
            raise APIError(400, "Invalid bootstrap size")
        return bootstrap

    def create(self, request):
        self.validate(request)
        identity = request["id"]
        with self.lock:
            fresh = not self.path(identity).exists()
            node = {key: value for key, value in request.items() if key != "user_data"}
            node.update(vm_name="vclima-" + identity, ready=False)
            if not fresh:
                previous = self.load(identity)
                if any(previous[key] != node[key] for key in request if key not in ("user_data", "ready")):
                    raise APIError(409, "A claim cannot change its VM identity or sizing")
                node = previous
                if node["ready"] and self.exists(node["vm_name"]):
                    return node
            else:
                # Include other vclima VMs in the ceiling even if their record was lost.
                inventory = self.command(["list", "--format", "{{.Name}}"])
                existing = {line for line in inventory.decode().splitlines() if line.startswith("vclima-")}
                reserved = {json.loads(p.read_text())["vm_name"] for p in self.state.glob("*.json")}
                if len(existing | reserved) >= self.config["max_nodes"]:
                    raise APIError(409, "Local Lima node capacity reached")
                if node["vm_name"] in existing:
                    raise APIError(409, "VM exists without its ownership record; reconcile it manually")
                self.save(node)  # Reserve ownership before provisioning; retries reuse it.
            if not self.exists(node["vm_name"]):
                node["ready"] = False
                self.save(node)
                self.command([
                    "start", "--name=" + node["vm_name"], "--cpus=" + str(node["cpus"]),
                    "--memory=" + str(node["memory_gib"]), "--disk=" + str(node["disk_gib"]),
                    "--containerd=none", "--mount-none", "--arch=aarch64", "--vm-type=vz",
                    # Default per-VM networking reuses 192.168.5.15, which
                    # breaks kubelet routing when multiple workers join.
                    "--network=lima:user-v2",
                    "template:ubuntu-22.04",
                ], timeout=900)
            else:
                self.command(["start", node["vm_name"]], timeout=300)
            self.command(["shell", node["vm_name"], "--", "sudo", "apt-get", "update"], timeout=300)
            self.command([
                "shell", node["vm_name"], "--", "sudo", "env", "DEBIAN_FRONTEND=noninteractive",
                "apt-get", "install", "-y", "curl", "ca-certificates", "python3-yaml", "iproute2",
                "iptables", "conntrack", "kmod", "socat", "ebtables", "ethtool",
            ], timeout=600)
            guest_script = Path(__file__).with_name("guest_bootstrap.py").read_text()
            payload = json.dumps({"user_data": request["user_data"], "platform_hosts": self.config["platform_hosts"]}).encode()
            self.command([
                "shell", node["vm_name"], "--", "sudo", "python3", "-c", guest_script,
            ], stdin=payload, timeout=900)
            node["ready"] = True  # Bootstrap completed; Kubernetes readiness is checked separately.
            self.save(node)
            return node

    def read(self, identity):
        with self.lock:
            node = self.load(identity)
            if not self.exists(node["vm_name"]):
                # The API resource is the durable managed-node record. Surface VM
                # drift so Terraform PUT can repair it with fresh bootstrap data.
                node["ready"] = False
                self.save(node)
            return node

    def delete(self, identity):
        with self.lock:
            try:
                node = self.load(identity)
            except APIError as error:
                if error.status == 404:
                    return
                raise
            if self.exists(node["vm_name"]):
                self.command(["delete", "--force", node["vm_name"]], timeout=300)
            self.path(identity).unlink(missing_ok=True)


def make_handler(manager, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Do not log credentials or request bodies.

        def response(self, status, body):
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def authorized(self):
            username, password = "", ""
            try:
                scheme, value = self.headers.get("Authorization", "").split(" ", 1)
                if scheme == "Basic":
                    username, password = base64.b64decode(value, validate=True).decode().split(":", 1)
            except (ValueError, UnicodeError):
                pass
            return username == "platform" and hmac.compare_digest(password.encode(), token.encode())

        def read_json(self):
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > MAX_BODY:
                raise APIError(413, "Invalid request size")
            request = json.loads(self.rfile.read(size))
            if not isinstance(request, dict):
                raise APIError(400, "Request body must be a JSON object")
            return request

        def dispatch(self):
            try:
                if not self.authorized():
                    raise APIError(401, "Authentication required")
                if self.path == "/healthz" and self.command == "GET":
                    self.response(200, {"status": "ok", "max_nodes": manager.config["max_nodes"],
                                        "worker_size": manager.config["worker_size"],
                                        "config_id": configuration_id(manager.config)})
                    return
                if self.path == "/nodes" and self.command == "POST":
                    request = self.read_json()
                    self.response(200, manager.create(request))
                    return
                identity = self.path.removeprefix("/nodes/")
                if not self.path.startswith("/nodes/") or not ID.fullmatch(identity):
                    raise APIError(404, "Unknown endpoint")
                if self.command == "GET":
                    self.response(200, manager.read(identity))
                elif self.command == "DELETE":
                    manager.delete(identity)
                    self.response(200, {"id": identity})
                elif self.command == "PUT":
                    request = self.read_json()
                    if request.get("id") != identity:
                        raise APIError(400, "Path and node identities differ")
                    self.response(200, manager.create(request))
                else:
                    raise APIError(405, "Unsupported method")
            except APIError as error:
                self.response(error.status, {"error": error.message})
            except (ValueError, TypeError, KeyError):
                self.response(400, {"error": "Invalid request"})
            except Exception:
                self.response(500, {"error": "Internal helper error; inspect local state"})

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = dispatch
    return Handler


def load_config(path):
    """Read private configuration; relative paths are resolved beside the JSON file."""
    path = Path(path).expanduser().resolve()
    config = json.loads(path.read_text())
    if not isinstance(config, dict):
        raise ValueError("Helper configuration must be a JSON object")
    for field in ("state_dir", "limactl", "token_file", "cert_file", "key_file"):
        value = config.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError("Missing configuration path: " + field)
        candidate = Path(value).expanduser()
        config[field] = str(candidate if candidate.is_absolute() else path.parent / candidate)
    normalize_resources(config)
    if config.get("lima_home"):
        candidate = Path(config["lima_home"]).expanduser()
        config["lima_home"] = str(candidate if candidate.is_absolute() else path.parent / candidate)
    if type(config.get("port")) is not int or not 1 <= config["port"] <= 65535:
        raise ValueError("port must be an integer between 1 and 65535")
    if not isinstance(config.get("listen_host"), str) or not config["listen_host"]:
        raise ValueError("listen_host must be set explicitly")
    hosts = config.setdefault("platform_hosts", [])
    if not isinstance(hosts, list) or any(
        not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", host)
        for host in hosts
    ):
        raise ValueError("platform_hosts must contain hostnames without a scheme or path")
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Path to private helper configuration JSON")
    args = parser.parse_args()
    config = load_config(args.config)
    token = Path(config["token_file"]).read_text().strip()
    if len(token.encode()) < 32:
        raise SystemExit("Helper token must contain at least 32 bytes")
    manager = Lima(config)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(config["cert_file"], config["key_file"])
    with ThreadingHTTPServer((config["listen_host"], config["port"]), make_handler(manager, token)) as server:
        server.socket = context.wrap_socket(server.socket, server_side=True)
        print("Lima helper ready on TLS port %s; node ceiling %s" % (config["port"], config["max_nodes"]), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
