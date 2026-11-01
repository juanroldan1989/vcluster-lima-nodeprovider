# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The vcluster-lima-nodeprovider contributors
"""Executed as root inside the new guest. Never print the bootstrap payload."""
import base64
import gzip
import grp
import json
import os
from pathlib import Path
import pwd
import socket
import subprocess
import sys
import tempfile


def commands(items):
    for item in items:
        if item is None:
            continue
        if isinstance(item, str):
            subprocess.run(["/bin/sh", "-c", item], check=True)
        elif isinstance(item, list) and all(isinstance(part, str) for part in item):
            subprocess.run(item, check=True)
        else:
            raise ValueError("Unsupported cloud-init command")


def configure_platform_hosts(hosts):
    if not hosts:
        return
    try:
        gateway = socket.gethostbyname("host.lima.internal")
    except socket.gaierror:
        gateway = None
    path = Path("/etc/hosts")
    for hostname in hosts:
        existing = path.read_text().splitlines()
        # Remove an entry written by the old adapter before testing normal DNS.
        cleaned = [line for line in existing if line.split("#", 1)[0].split() != [gateway, hostname]]
        if cleaned != existing:
            path.write_text("\n".join(cleaned) + "\n")
        probe = ["curl", "--fail", "--silent", "--insecure", "--head", "--max-time", "10", "https://" + hostname]
        reachable = subprocess.run(probe, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if reachable.returncode == 0:
            continue
        # Public DNS is normally correct for loft.host. Use the Mac gateway
        # only when the endpoint is unreachable through its normal address.
        if gateway is None:
            raise RuntimeError("Platform endpoint is unreachable and the Lima gateway cannot be resolved")
        original = path.read_text()
        with path.open("a") as stream:
            if original and not original.endswith("\n"):
                stream.write("\n")
            stream.write(gateway + " " + hostname + " # vcluster-lima-platform\n")
        try:
            subprocess.run(probe, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            path.write_text(original)
            raise


def bootstrap(payload):
    raw = base64.b64decode(payload["user_data"], validate=True)
    if raw.startswith(b"\x1f\x8b"):
        raw = gzip.decompress(raw)
    document = None
    if not raw.startswith(b"#!"):
        import yaml  # Installed in the guest by the host helper.
        document = yaml.safe_load(raw)
        allowed = {"runcmd", "write_files", "packages", "package_update", "package_upgrade", "bootcmd", "final_message", "hostname", "fqdn", "preserve_hostname"}
        if not isinstance(document, dict) or set(document) - allowed:
            raise ValueError("Unsupported cloud-init shape; extend the adapter before provisioning")
        if not document.get("runcmd"):
            raise ValueError("Bootstrap must contain registration commands")
    configure_platform_hosts(payload.get("platform_hosts", []))
    if raw.startswith(b"#!"):
        descriptor, name = tempfile.mkstemp(prefix="vcluster-bootstrap-", dir="/root")
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
            os.chmod(name, 0o700)
            subprocess.run([name], check=True)
        finally:
            Path(name).unlink(missing_ok=True)
        return
    hostname = document.get("fqdn") or document.get("hostname")
    if hostname and not document.get("preserve_hostname"):
        subprocess.run(["hostnamectl", "set-hostname", hostname], check=True)
    commands(document.get("bootcmd", []))
    if document.get("package_update"):
        subprocess.run(["apt-get", "update"], check=True)
    if document.get("package_upgrade"):
        subprocess.run(["apt-get", "upgrade", "-y"], check=True)
    packages = document.get("packages", [])
    if packages:
        names = [item if isinstance(item, str) else "=".join(item) for item in packages]
        subprocess.run(["apt-get", "install", "-y", *names], check=True)
    for entry in document.get("write_files", []):
        target = Path(entry["path"])
        if not target.is_absolute():
            raise ValueError("Cloud-init file paths must be absolute")
        content = entry.get("content", "").encode()
        encoding = entry.get("encoding", "text/plain")
        if encoding in ("b64", "base64", "gz+b64", "gzip+base64"):
            content = base64.b64decode(content)
        if encoding in ("gz", "gzip", "gz+b64", "gzip+base64"):
            content = gzip.decompress(content)
        if encoding not in ("text/plain", "b64", "base64", "gz", "gzip", "gz+b64", "gzip+base64"):
            raise ValueError("Unsupported file encoding")
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | (os.O_APPEND if entry.get("append") else os.O_TRUNC), 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        os.chmod(target, int(str(entry.get("permissions", "0644")), 8))
        owner = entry.get("owner", "root:root").split(":", 1)
        os.chown(target, pwd.getpwnam(owner[0]).pw_uid, grp.getgrnam(owner[-1]).gr_gid)
    commands(document["runcmd"])
    if document.get("final_message"):
        print(document["final_message"])


def main():
    payload = json.load(sys.stdin)
    # Logs are guest-local and root-readable: generated scripts can contain tokens.
    descriptor = os.open("/var/log/vcluster-lima-bootstrap.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(descriptor, 1)
    os.dup2(descriptor, 2)
    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    bootstrap(payload)


if __name__ == "__main__":
    main()
