# SPDX-License-Identifier: Apache-2.0
"""Run the real process adapter against a fake executable, never installed Lima."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch

from helper import server
from tests.test_helper import request


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fake = Path(__file__).with_name("fake_limactl.py")
        self.env = patch.dict(os.environ, {"FAKE_LIMA_STATE": str(self.root / "vms")}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.manager = server.Lima({"state_dir": str(self.root / "records"), "limactl": str(self.fake),
                                    "max_nodes": 8, "platform_hosts": []})

    def calls(self):
        return [json.loads(line) for line in (self.root / "calls.jsonl").read_text().splitlines()]

    def test_real_subprocess_lifecycle_and_private_record(self):
        node = self.manager.create(request())
        record = self.manager.path(node["id"])
        self.assertEqual(stat.S_IMODE(record.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(record.parent.stat().st_mode), 0o700)
        self.assertNotIn("user_data", json.loads(record.read_text()))
        self.assertEqual(node, self.manager.create(request()))
        starts = [call for call in self.calls() if call[0] == "start"]
        self.assertEqual(len(starts), 1)
        self.assertIn("--network=lima:user-v2", starts[0])
        self.assertIn("--mount-none", starts[0])
        self.assertEqual(self.manager.read(node["id"]), node)
        self.manager.delete(node["id"])
        self.assertFalse((self.root / "vms" / node["vm_name"]).exists())
        self.assertFalse(record.exists())

    def test_inventory_supports_json_array(self):
        self.manager.create(request())
        with patch.dict(os.environ, {"FAKE_LIMA_ARRAY": "1"}):
            self.assertTrue(self.manager.exists("vclima-" + "a" * 24))
            self.assertFalse(self.manager.exists("vclima-" + "b" * 24))

    def test_failed_creation_reserves_slot_and_retry_reuses_identity(self):
        self.manager.config["max_nodes"] = 1
        with patch.dict(os.environ, {"FAKE_LIMA_FAIL": "start"}):
            with self.assertRaises(server.APIError) as error:
                self.manager.create(request())
        self.assertEqual(error.exception.status, 502)
        self.assertNotIn("synthetic-secret", error.exception.message)
        self.assertFalse(self.manager.load("a" * 24)["ready"])
        with self.assertRaises(server.APIError) as full:
            self.manager.create(request("b" * 24))
        self.assertEqual(full.exception.status, 409)
        self.assertTrue(self.manager.create(request())["ready"])

    def test_failed_bootstrap_can_resume_after_helper_restart(self):
        with patch.dict(os.environ, {"FAKE_LIMA_FAIL": "bootstrap"}):
            with self.assertRaises(server.APIError):
                self.manager.create(request())
        restarted = server.Lima(self.manager.config)
        self.assertTrue(restarted.create(request())["ready"])
        fresh_starts = [call for call in self.calls() if call[0] == "start" and any(arg.startswith("--name=") for arg in call)]
        self.assertEqual(len(fresh_starts), 1)

    def test_missing_vm_can_be_repaired(self):
        node = self.manager.create(request())
        (self.root / "vms" / node["vm_name"]).unlink()
        self.assertFalse(self.manager.read(node["id"])["ready"])
        self.assertTrue(self.manager.create(request())["ready"])
        self.assertTrue((self.root / "vms" / node["vm_name"]).exists())

    def test_delete_failure_retains_ownership_for_retry(self):
        node = self.manager.create(request())
        with patch.dict(os.environ, {"FAKE_LIMA_FAIL": "delete"}):
            with self.assertRaises(server.APIError):
                self.manager.delete(node["id"])
        self.assertTrue(self.manager.path(node["id"]).exists())
        self.manager.delete(node["id"])
        self.assertFalse(self.manager.path(node["id"]).exists())

    def test_changed_tenant_or_shape_cannot_adopt_existing_record(self):
        self.manager.create(request())
        data = request()
        data["tenant"] = "another-tenant"
        with self.assertRaises(server.APIError) as error:
            self.manager.create(data)
        self.assertEqual(error.exception.status, 409)

    def test_corrupt_record_cannot_delete_unrelated_vm(self):
        node = self.manager.create(request())
        node["vm_name"] = "manual-worker"
        self.manager.path(node["id"]).write_text(json.dumps(node))
        with self.assertRaises(server.APIError) as error:
            self.manager.delete(node["id"])
        self.assertEqual(error.exception.status, 409)
        self.assertFalse(any(call[0] == "delete" for call in self.calls()))

    def test_parallel_claims_cannot_consume_the_same_last_slot(self):
        self.manager.config["max_nodes"] = 1
        gate = threading.Barrier(2)
        def create(identity):
            gate.wait(timeout=5)
            try:
                self.manager.create(request(identity))
                return 200
            except server.APIError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(create, ("a" * 24, "b" * 24)))
        self.assertEqual(sorted(statuses), [200, 409])
        self.assertEqual(len(list((self.root / "vms").iterdir())), 1)

    def test_invalid_types_do_not_invoke_lima(self):
        for field, value in (("id", None), ("cpus", True), ("memory_gib", 2.0), ("user_data", None)):
            data = request()
            data[field] = value
            with self.subTest(field=field), self.assertRaises(server.APIError):
                self.manager.create(data)
        self.assertFalse((self.root / "calls.jsonl").exists())

    def test_configured_resources_reach_lima(self):
        self.manager.config["worker_size"] = {"cpus": 2, "memory_gib": 4, "disk_gib": 40}
        data = request()
        data.update(cpus=2, memory_gib=4, disk_gib=40)
        self.manager.create(data)
        start = next(call for call in self.calls() if call[0] == "start")
        self.assertTrue({"--cpus=2", "--memory=4", "--disk=40"}.issubset(start))
