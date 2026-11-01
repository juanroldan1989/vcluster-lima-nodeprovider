#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Real Terraform/OpenTofu + HTTPS integration using fake Lima; never creates VMs."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from helper import manage  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terraform", default=shutil.which("tofu") or shutil.which("terraform"))
    args = parser.parse_args()
    if not args.terraform or not shutil.which("openssl"):
        raise SystemExit("Install OpenTofu or Terraform and openssl to run this integration test")
    with tempfile.TemporaryDirectory(prefix="lima-provider-integration-") as name:
        temp = Path(name)
        os.chmod(temp, 0o700)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        cert = manage.certificate(temp, f"https://localhost:{port}", "127.0.0.1")
        password = secrets.token_urlsafe(48)
        manage.write_private(temp / "token", password)
        worker_size = {"cpus": 2, "memory_gib": 4, "disk_gib": 40}
        config = {"state_dir": str(temp / "nodes"), "limactl": str(ROOT / "tests/fake_limactl.py"),
                  "max_nodes": 2, "worker_size": worker_size, "platform_hosts": [],
                  "token_file": str(temp / "token"), "listen_host": "127.0.0.1", "port": port,
                  "cert_file": str(temp / "server.crt"), "key_file": str(temp / "server.key")}
        manage.write_private(temp / "config.json", json.dumps(config))
        env = dict(os.environ, FAKE_LIMA_STATE=str(temp / "fake-vms"), REST_API_PASSWORD=password, CHECKPOINT_DISABLE="1")
        process = subprocess.Popen([sys.executable, "-m", "helper.server", "--config", str(temp / "config.json")],
                                   cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        context = ssl.create_default_context(cafile=str(temp / "server.crt"))
        url = f"https://localhost:{port}"
        auth = "Basic " + base64.b64encode(("platform:" + password).encode()).decode()
        try:
            for _ in range(100):
                try:
                    request = urllib.request.Request(url + "/healthz", headers={"Authorization": auth})
                    with urllib.request.urlopen(request, context=context, timeout=2) as response:
                        assert json.load(response)["worker_size"] == worker_size
                    break
                except OSError:
                    if process.poll() is not None:
                        raise RuntimeError("Test helper exited before becoming healthy") from None
                    time.sleep(0.1)
            else:
                raise RuntimeError("Test helper failed to start")
            try:
                urllib.request.urlopen(url + "/healthz", context=context, timeout=5)
                raise AssertionError("Unauthenticated request succeeded")
            except urllib.error.HTTPError as error:
                assert error.code == 401
            workspace = temp / "terraform"
            workspace.mkdir()
            module = manage.render_terraform(url, cert, worker_size)
            manage.write_private(workspace / "main.tf", module + '\nvariable "vcluster" { type = any }\n')
            fixture = {"vcluster": {"name": "integration-test", "namespace": "p-example",
                                    "nodeClaim": {"metadata": {"name": "test-claim", "namespace": "p-example"}},
                                    "userData": "#!/bin/sh\ntrue\n"}}
            manage.write_private(workspace / "terraform.tfvars.json", json.dumps(fixture))

            def terraform(*command, expected=0):
                result = subprocess.run([args.terraform, *command], cwd=workspace, env=env,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                if result.returncode != expected:
                    print(result.stdout.decode().replace(password, "[redacted]"))
                    raise RuntimeError("Integration command failed: " + " ".join(command))

            terraform("init", "-backend=false", "-input=false", "-no-color")
            terraform("validate", "-no-color")
            terraform("apply", "-auto-approve", "-input=false", "-no-color")
            terraform("plan", "-detailed-exitcode", "-input=false", "-no-color")
            identity = hashlib.sha256(b"p-example/test-claim").hexdigest()[:24]
            fake_vm = temp / "fake-vms" / ("vclima-" + identity)
            assert fake_vm.exists()
            fake_vm.unlink()
            terraform("plan", "-detailed-exitcode", "-input=false", "-no-color", expected=2)
            terraform("apply", "-auto-approve", "-input=false", "-no-color")
            assert fake_vm.exists()
            terraform("destroy", "-auto-approve", "-input=false", "-no-color")
            assert not fake_vm.exists()
            assert not list((temp / "nodes").glob("*.json"))
            print("PASS: TLS/auth, configurable sizing, UID-free Platform input, create/read/no-drift, repair and destroy. No VMs created.")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            process.stderr.close()


if __name__ == "__main__":
    main()
