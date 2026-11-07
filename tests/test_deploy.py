# SPDX-License-Identifier: Apache-2.0
"""Check actionable ownership diagnostics without changing Platform resources."""
import json
from pathlib import Path
import shlex
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from helper import manage


class DeploymentGuidanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.args = manage.parser().parse_args(['--state-dir', str(self.root), 'deploy'])
        self.config = {
            'listen_host': '0.0.0.0', 'port': 18443, 'max_nodes': 4, 'pool_nodes': 2,
            'worker_size': {'cpus': 1, 'memory_gib': 2, 'disk_gib': 30},
            'state_dir': str(self.root/'nodes'), 'limactl': '/usr/bin/true',
            'platform_hosts': ['platform.example.test'],
            'api_url': 'https://host.docker.internal:18443',
            'pod_cidr': '10.66.0.0/16', 'service_cidr': '10.130.0.0/16',
            'platform': {'context': 'k3d-host-cluster', 'namespace': 'vcluster-platform',
                         'deployment': 'loft', 'provider': 'lima-local',
                         'template': 'private-nodes-lima-auto', 'chart_version': '0.37.1'},
        }

    def test_all_conflicts_are_reported_before_platform_changes(self):
        template = {'kind': 'VirtualClusterTemplate', 'metadata': {'name': 'private-nodes-lima-auto'}}
        secret = {'kind': 'Secret', 'metadata': {'namespace': 'vcluster-platform', 'name': 'lima-local-credentials'},
                  'data': {'REST_API_PASSWORD': 'SENSITIVE_VALUE_MUST_NOT_APPEAR'}}
        with patch.object(manage, 'configuration', return_value=self.config), \
             patch.object(manage, 'check'), \
             patch.object(manage, 'get_resource', side_effect=[None, template, secret]) as lookup, \
             patch.object(manage, 'run') as command, \
             patch.object(manage.secrets, 'token_hex', return_value='abcdef'):
            with self.assertRaisesRegex(ValueError, 'Deployment blocked by 2') as caught:
                manage.deploy(self.args)
        message = str(caught.exception)
        self.assertIn('VirtualClusterTemplate/private-nodes-lima-auto', message)
        self.assertIn('Secret/vcluster-platform/lima-local-credentials', message)
        self.assertIn('missing or different installation ID', message)
        self.assertIn('start', message)
        self.assertIn('teardown --dry-run', message)
        self.assertIn('--include-unmanaged', message)
        self.assertIn('tenants and their data', message)
        self.assertIn('--provider-name lima-local-abcdef', message)
        self.assertIn('--template-name private-nodes-lima-auto-abcdef', message)
        self.assertIn('--max-nodes 4', message)
        self.assertIn('--pool-nodes 2', message)
        self.assertNotIn('SENSITIVE_VALUE_MUST_NOT_APPEAR', message)
        self.assertEqual(lookup.call_count, 3)
        command.assert_not_called()

    def test_recovery_command_preserves_custom_settings_and_shell_quoting(self):
        config = json.loads(json.dumps(self.config))
        config.update(port=19443, listen_host='127.0.0.1', max_nodes=6, pool_nodes=3,
                      api_url='https://helper.example.test:19443',
                      pod_cidr='10.68.0.0/16', service_cidr='10.131.0.0/16',
                      lima_home=str(self.root/"Lima's inventory"),
                      limactl=str(self.root/'Lima tools/limactl'),
                      worker_size={'cpus': 2, 'memory_gib': 4, 'disk_gib': 40})
        config['platform'].update(context='custom-context', namespace='platform-test',
                                  deployment='platform-control', chart_version='0.37.2')
        directory = self.root/"custom state's files"
        command = manage.reprepare_command(config, directory, 'new-provider', 'new-template')
        rendered = manage.shell_command(command)
        self.assertEqual(shlex.split(rendered.replace('\\\n', '')), command)
        args = manage.parser().parse_args(command[1:])
        self.assertEqual(args.state_dir, directory.resolve())
        for field, value in {'port': 19443, 'listen_host': '127.0.0.1', 'max_nodes': 6,
                             'pool_nodes': 3, 'cpus': 2, 'memory_gib': 4, 'disk_gib': 40,
                             'context': 'custom-context', 'namespace': 'platform-test',
                             'deployment': 'platform-control', 'chart_version': '0.37.2',
                             'provider_name': 'new-provider', 'template_name': 'new-template',
                             'api_url': config['api_url'], 'pod_cidr': config['pod_cidr'],
                             'service_cidr': config['service_cidr'], 'limactl': config['limactl'],
                             'lima_home': Path(config['lima_home'])}.items():
            self.assertEqual(getattr(args, field), value, field)

    def test_matching_ownership_is_accepted(self):
        resource = {'kind': 'Secret', 'metadata': {
            'name': 'lima-local-credentials',
            'labels': {'app.kubernetes.io/managed-by': manage.MANAGER},
            'annotations': {manage.OWNER: manage.helper_id(self.root)},
        }}
        self.assertIsNone(manage.ownership_conflict(resource, self.root))
        manage.ensure_owner(resource, self.root)
        self.assertIsNone(manage.ownership_conflict(None, self.root))

    def test_proposed_names_fit_supported_resource_name_lengths(self):
        self.config['platform'].update(provider='a'*40, template='b'*63)
        with patch.object(manage.secrets, 'token_hex', return_value='abcdef'), \
             patch.object(manage, 'reprepare_command', wraps=manage.reprepare_command) as recovery:
            manage.deployment_conflict(self.config, self.root, ['existing resource'])
        provider, template = recovery.call_args.args[-2:]
        self.assertEqual(len(provider), 40)
        self.assertEqual(len(template), 63)
        manage.name(provider, maximum=40)
        manage.name(template)

    def test_status_explicitly_reports_empty_resource_lists(self):
        with patch.object(manage, 'configuration', return_value=self.config), \
             patch.object(manage, 'health', return_value={'status': 'ok'}), \
             patch.object(manage, 'run', return_value=SimpleNamespace(stdout=b'')), \
             patch('builtins.print') as output:
            manage.status(self.args)
        messages = [call.args[0] for call in output.call_args_list]
        self.assertIn('NodeProviders:', messages)
        self.assertIn('Node types:', messages)
        self.assertIn('NodeClaims:', messages)
        self.assertEqual(messages.count('No resources found.'), 3)
