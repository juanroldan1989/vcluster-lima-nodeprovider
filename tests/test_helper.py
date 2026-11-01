# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The vcluster-lima-nodeprovider contributors
import base64
import gzip
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from helper import server
from helper import guest_bootstrap as guest


class FakeLima(server.Lima):
    def __init__(self, state):
        super().__init__({"state_dir": state, "max_nodes": 8, "platform_hosts": []})
        self.vms = set()
        self.fail_bootstrap = False
        self.started = 0

    def command(self, args, stdin=None, timeout=120):
        if args[0] == "list":
            if args[1] == "--format=json":
                if len(args) != 2:
                    raise server.APIError(502, "Named list fails for missing instances in Lima 2.1")
                return b"\n".join(json.dumps({"name": name}).encode() for name in sorted(self.vms))
            return "\n".join(self.vms).encode()
        if args[0] == "start":
            name = next((a.split("=", 1)[1] for a in args if a.startswith("--name=")), args[1])
            if name not in self.vms:
                self.started += 1
            self.vms.add(name)
        if args[0] == "delete":
            self.vms.discard(args[-1])
        if stdin and self.fail_bootstrap:
            raise server.APIError(502, "simulated failure")
        return b""


def request(identity="a" * 24):
    return {"id": identity, "vm_name": "vclima-" + identity, "claim": "node-a", "tenant": "lima-test", "project": "p-default",
            "cpus": 1, "memory_gib": 2, "disk_gib": 30, "ready": True,
            "user_data": base64.b64encode(b"#!/bin/sh\ntrue\n").decode()}


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manager = FakeLima(self.temp.name)

    def test_idempotency_and_no_token_persistence(self):
        first = self.manager.create(request())
        self.assertEqual(first, self.manager.create(request()))
        self.assertEqual(self.manager.started, 1)
        self.assertNotIn("user_data", json.loads(self.manager.path(first["id"]).read_text()))

    def test_capacity_includes_unrecorded_owned_prefix(self):
        self.manager.vms.update({f"vclima-orphan-{i}" for i in range(8)} | {"manual-team-d"})
        with self.assertRaises(server.APIError) as error:
            self.manager.create(request())
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.manager.started, 0)

    def test_eight_workers_across_tenants_then_ninth_blocked_until_release(self):
        self.manager.vms.add("manual-team-d")
        for number in range(1, 9):
            data = request(f"{number:024x}")
            data["tenant"] = f"tenant-{number}"
            data["claim"] = f"claim-{number}"
            self.manager.create(data)
        self.assertEqual(self.manager.started, 8)
        with self.assertRaises(server.APIError) as error:
            self.manager.create(request(f"{9:024x}"))
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.manager.started, 8)
        self.manager.delete(f"{1:024x}")
        self.assertTrue(self.manager.create(request(f"{9:024x}"))["ready"])
        self.assertEqual(len(self.manager.vms - {"manual-team-d"}), 8)

    def test_old_large_worker_can_be_deleted_after_size_change(self):
        old = request()
        old.pop("user_data")
        old.update(cpus=2, memory_gib=4)
        self.manager.save(old)
        self.manager.vms.add(old["vm_name"])
        self.manager.delete(old["id"])
        self.assertFalse(self.manager.path(old["id"]).exists())
        self.assertNotIn(old["vm_name"], self.manager.vms)

    def test_failed_bootstrap_retries_same_vm(self):
        self.manager.fail_bootstrap = True
        with self.assertRaises(server.APIError):
            self.manager.create(request())
        self.assertFalse(self.manager.load("a" * 24)["ready"])
        self.manager.fail_bootstrap = False
        self.assertTrue(self.manager.create(request())["ready"])
        self.assertEqual(self.manager.started, 1)

    def test_missing_vm_repaired_and_retry_not_marked_ready(self):
        node = self.manager.create(request())
        self.manager.vms.remove(node["vm_name"])
        self.assertFalse(self.manager.read(node["id"])["ready"])
        self.manager.fail_bootstrap = True
        with self.assertRaises(server.APIError):
            self.manager.create(request())
        self.assertFalse(self.manager.load(node["id"])["ready"])
        self.manager.fail_bootstrap = False
        self.assertTrue(self.manager.create(request())["ready"])

    def test_delete_is_scoped_and_idempotent(self):
        self.manager.vms.add("manual-team-d")
        node = self.manager.create(request())
        self.manager.delete(node["id"])
        self.manager.delete(node["id"])
        self.assertEqual(self.manager.vms, {"manual-team-d"})
        self.assertFalse(self.manager.path(node["id"]).exists())

    def test_invalid_identity_and_oversizing_rejected(self):
        for field, value in (("id", "../other"), ("cpus", 12), ("user_data", "invalid!")):
            data = request()
            data[field] = value
            with self.assertRaises(server.APIError):
                self.manager.create(data)
        self.assertFalse(self.manager.vms)


class BootstrapTests(unittest.TestCase):
    def test_reachable_platform_keeps_public_dns_and_removes_old_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            hosts = Path(directory) / "hosts"
            hosts.write_text("127.0.0.1 localhost\n192.168.5.2 platform.example\n")
            with patch.object(guest, "Path", return_value=hosts), patch.object(guest.socket, "gethostbyname", return_value="192.168.5.2"), patch.object(guest.subprocess, "run", return_value=types.SimpleNamespace(returncode=0)) as run:
                guest.configure_platform_hosts(["platform.example"])
            self.assertEqual(hosts.read_text(), "127.0.0.1 localhost\n")
            self.assertEqual(run.call_count, 1)

    def test_unreachable_platform_uses_and_checks_gateway_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            hosts = Path(directory) / "hosts"
            hosts.write_text("127.0.0.1 localhost\n")
            with patch.object(guest, "Path", return_value=hosts), patch.object(guest.socket, "gethostbyname", return_value="192.168.5.2"), patch.object(guest.subprocess, "run", side_effect=[types.SimpleNamespace(returncode=22), types.SimpleNamespace(returncode=0)]) as run:
                guest.configure_platform_hosts(["platform.example"])
            self.assertIn("192.168.5.2 platform.example # vcluster-lima-platform", hosts.read_text())
            self.assertTrue(run.call_args.kwargs["check"])

    def test_failed_gateway_fallback_restores_hosts_file(self):
        with tempfile.TemporaryDirectory() as directory:
            hosts = Path(directory) / "hosts"
            original = "127.0.0.1 localhost\n"
            hosts.write_text(original)
            failure = subprocess.CalledProcessError(22, ["curl"])
            with patch.object(guest, "Path", return_value=hosts), patch.object(guest.socket, "gethostbyname", return_value="192.168.5.2"), patch.object(guest.subprocess, "run", side_effect=[types.SimpleNamespace(returncode=22), failure]):
                with self.assertRaises(subprocess.CalledProcessError):
                    guest.configure_platform_hosts(["platform.example"])
            self.assertEqual(hosts.read_text(), original)

    def test_normal_dns_does_not_require_a_resolvable_gateway(self):
        with tempfile.TemporaryDirectory() as directory:
            hosts = Path(directory) / "hosts"
            hosts.write_text("127.0.0.1 localhost\n")
            with patch.object(guest, "Path", return_value=hosts), patch.object(guest.socket, "gethostbyname", side_effect=guest.socket.gaierror()), patch.object(guest.subprocess, "run", return_value=types.SimpleNamespace(returncode=0)):
                guest.configure_platform_hosts(["platform.example"])
            self.assertEqual(hosts.read_text(), "127.0.0.1 localhost\n")

    def test_gzip_shell_script_is_private_and_removed_after_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            fd, name = tempfile.mkstemp(dir=directory)
            script = b"#!/bin/sh\ntrue\n"
            payload = {"user_data": base64.b64encode(gzip.compress(script)).decode(), "platform_hosts": []}
            def inspect(args, **_kwargs):
                self.assertEqual(args, [name])
                self.assertEqual(Path(name).read_bytes(), script)
                self.assertEqual(Path(name).stat().st_mode & 0o777, 0o700)
                raise subprocess.CalledProcessError(1, args)
            with patch.object(guest.tempfile, "mkstemp", return_value=(fd, name)), patch.object(guest.subprocess, "run", side_effect=inspect):
                with self.assertRaises(subprocess.CalledProcessError):
                    guest.bootstrap(payload)
            self.assertFalse(Path(name).exists())

    def test_fqdn_and_literal_final_message_are_handled(self):
        with patch.object(guest.subprocess, "run") as run, patch("builtins.print") as output:
            self.invoke({"hostname": "short", "fqdn": "worker.example", "final_message": "finished", "runcmd": ["true"]})
        self.assertEqual(run.call_args_list[0].args[0], ["hostnamectl", "set-hostname", "worker.example"])
        output.assert_called_once_with("finished")

    def invoke(self, document):
        # JSON is a YAML subset. The adapter's policy/command behavior is tested
        # independently of guest-installed PyYAML's established YAML parser.
        payload = {"user_data": base64.b64encode(json.dumps(document).encode()).decode(), "platform_hosts": []}
        with patch.dict(sys.modules, {"yaml": types.SimpleNamespace(safe_load=json.loads)}):
            guest.bootstrap(payload)

    def test_commands_preserve_list_arguments_and_shell_semantics(self):
        with patch.object(guest.subprocess, "run") as run:
            self.invoke({"runcmd": [["echo", "one argument"], "echo shell-command"]})
        self.assertEqual(run.call_args_list[0].args[0], ["echo", "one argument"])
        self.assertEqual(run.call_args_list[1].args[0], ["/bin/sh", "-c", "echo shell-command"])

    def test_unknown_cloud_init_module_fails_before_commands(self):
        with patch.object(guest.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                self.invoke({"runcmd": ["true"], "unsupported_module": {}})
        run.assert_not_called()

    def test_write_files_encoding_permissions_and_command_order(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "bootstrap.txt"
            with patch.object(guest.os, "chown"), patch.object(guest.pwd, "getpwnam", return_value=types.SimpleNamespace(pw_uid=0)), patch.object(guest.grp, "getgrnam", return_value=types.SimpleNamespace(gr_gid=0)), patch.object(guest.subprocess, "run") as run:
                self.invoke({"write_files": [{"path": str(file), "content": base64.b64encode(b"secret").decode(), "encoding": "b64", "permissions": "0600"}], "runcmd": [["test", "-f", str(file)]]})
            self.assertEqual(file.read_bytes(), b"secret")
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)
            self.assertEqual(run.call_args.args[0], ["test", "-f", str(file)])

    def test_relative_file_path_rejected(self):
        with self.assertRaises(ValueError):
            self.invoke({"write_files": [{"path": "../escape"}], "runcmd": ["true"]})


if __name__ == "__main__":
    unittest.main()
