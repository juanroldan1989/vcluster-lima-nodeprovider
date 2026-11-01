# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path
import tempfile
import unittest

from helper.server import load_config


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "config.json"
        self.config = {"state_dir": "nodes", "limactl": "/usr/local/bin/limactl", "max_nodes": 8,
                       "platform_hosts": ["platform.example.test"], "listen_host": "127.0.0.1", "port": 18443,
                       "token_file": "token", "cert_file": "server.crt", "key_file": "server.key"}

    def load(self):
        self.path.write_text(json.dumps(self.config))
        return load_config(self.path)

    def test_paths_are_relative_to_config_and_not_current_directory(self):
        config = self.load()
        self.assertEqual(config["state_dir"], str(self.path.parent.resolve() / "nodes"))
        self.assertEqual(config["limactl"], "/usr/local/bin/limactl")

    def test_invalid_capacity_port_and_host_are_rejected(self):
        for field, value in (("max_nodes", 0), ("max_nodes", True), ("max_nodes", 9), ("port", 65536),
                             ("platform_hosts", ["https://platform.example/path"]), ("listen_host", "")):
            original = self.config[field]
            self.config[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.load()
            self.config[field] = original

    def test_missing_path_is_reported_without_reading_credentials(self):
        del self.config["key_file"]
        with self.assertRaisesRegex(ValueError, "key_file"):
            self.load()
