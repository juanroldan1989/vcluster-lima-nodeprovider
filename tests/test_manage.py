# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from helper import manage, server
from tests.test_helper import request


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "private"
        self.args = manage.parser().parse_args([
            "--state-dir", str(self.directory), "--context", "host-test", "--namespace", "platform-test",
            "prepare", "--platform-host", "platform.example.test", "--provider-name", "lima-test",
            "--template-name", "lima-test-template", "--cpus", "2", "--memory-gib", "4",
            "--disk-gib", "40", "--max-nodes", "12", "--pool-nodes", "3"])
        self.which = patch.object(manage.shutil, "which", return_value="/usr/bin/true")
        self.which.start()
        self.addCleanup(self.which.stop)
        self.certificate = patch.object(manage, "certificate", return_value="PUBLIC TEST CERTIFICATE\n")
        self.certificate.start()
        self.addCleanup(self.certificate.stop)
        self.runner = patch.object(manage, "run", return_value=SimpleNamespace(stdout=b"Hostname does match certificate"))
        self.run = self.runner.start()
        self.addCleanup(self.runner.stop)

    def prepare(self):
        with patch("builtins.print"):
            manage.prepare(self.args)
        return server.load_config(self.directory / "config.json")

    def test_preparation_renders_consistent_worker_and_pool_budgets(self):
        config = self.prepare()
        self.assertEqual(config["worker_size"], {"cpus": 2, "memory_gib": 4, "disk_gib": 40})
        provider = (self.directory / "node-provider.yaml").read_text()
        tenant = (self.directory / "tenant-template.yaml").read_text()
        self.assertIn("maxCapacity: 12", provider)
        self.assertIn('cpu: "2"', provider)
        self.assertIn("memory: 4Gi", provider)
        self.assertIn("lima-test-2cpu-4gi", provider)
        self.assertIn("lima-test-2cpu-4gi", tenant)
        self.assertIn('nodes: "3"', tenant)
        self.assertIn('cpu: "6"', tenant)
        self.assertIn("memory: 12Gi", tenant)
        self.assertNotIn("@@", provider + tenant)
        self.assertNotIn("__WORKER_", (self.directory / "node.tf").read_text())

    def test_private_artifacts_and_credentials_survive_repeated_prepare(self):
        config = self.prepare()
        token = Path(config["token_file"]).read_text()
        self.prepare()
        self.assertEqual(Path(config["token_file"]).read_text(), token)
        for filename in ("api-token", "config.json", "credentials.json", "node-provider.yaml", "launchagent.plist"):
            self.assertEqual(stat.S_IMODE((self.directory / filename).stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        secret = json.loads((self.directory / "credentials.json").read_text())
        self.assertEqual(secret["metadata"]["namespace"], "platform-test")
        self.assertEqual(secret["metadata"]["labels"]["terraform.vcluster.com/provider"], "lima-test")
        self.assertNotIn(token.strip(), (self.directory / "node-provider.yaml").read_text())

    def test_preparation_rejects_changes_with_managed_workers(self):
        config = self.prepare()
        state = Path(config["state_dir"])
        state.mkdir()
        (state / "record.json").write_text(json.dumps(request()))
        self.args.memory_gib = 8
        with self.assertRaisesRegex(ValueError, "Managed workers exist"):
            self.prepare()

    def test_existing_resources_cannot_be_overwritten(self):
        with self.assertRaisesRegex(ValueError, "Refusing to overwrite"):
            manage.ensure_owner({"metadata": {"name": "existing"}}, self.directory)
        manage.ensure_owner({"metadata": {"labels": {"app.kubernetes.io/managed-by": manage.MANAGER},
                                          "annotations": {manage.OWNER: manage.helper_id(self.directory)}}}, self.directory)

    def test_installation_identity_resolves_directory_aliases(self):
        self.directory.mkdir()
        alias = Path(self.temp.name) / "alias"
        alias.symlink_to(self.directory, target_is_directory=True)
        self.assertEqual(manage.helper_id(alias), manage.helper_id(self.directory))

    def test_endpoint_rejects_credentials_paths_and_http(self):
        for value in ("http://helper.test", "https://user:password@helper.test", "https://helper.test/path", "https://helper.test?token=secret"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                manage.endpoint(value)

    def test_pool_limit_cannot_exceed_shared_limit(self):
        self.args.pool_nodes = 13
        with self.assertRaisesRegex(ValueError, "pool node limit"):
            self.prepare()

    def test_hostname_discovery_rejects_ambiguous_cli_configuration(self):
        path = Path(self.temp.name) / "cli.json"
        path.write_text(json.dumps({"platform": {"one": {"host": "https://first.example"}}}))
        self.assertEqual(manage.platform_hostname(path), "first.example")
        path.write_text(json.dumps({"platform": {"one": {"host": "first.example"}, "two": {"host": "second.example"}}}))
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            manage.platform_hostname(path)

    def test_terraform_identity_uses_exposed_fields_and_safe_string_rendering(self):
        module = manage.render_terraform("https://localhost:18443", 'a "quoted" certificate\n', {"cpus": 1, "memory_gib": 2, "disk_gib": 30})
        self.assertNotIn("metadata.uid)", module)
        self.assertIn('${var.vcluster.namespace}/${var.vcluster.nodeClaim.metadata.name}', module)
        self.assertIn('a \\"quoted\\" certificate\\n', module)
        self.assertIn('ignore_changes_to = ["user_data"]', module)

    def test_failed_kubectl_lookup_is_not_treated_as_missing_resource(self):
        self.run.side_effect = RuntimeError("kubectl failed")
        with self.assertRaises(RuntimeError):
            manage.get_resource({"platform": {"context": "host-test"}}, "nodeprovider", "lima-test")

    def test_health_configuration_id_changes_with_worker_settings(self):
        config = self.prepare()
        previous = server.configuration_id(config)
        config["worker_size"]["cpus"] = 4
        self.assertNotEqual(previous, server.configuration_id(config))
