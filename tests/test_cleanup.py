# SPDX-License-Identifier: Apache-2.0
"""Validate teardown ownership, ordering, retries and cleanup boundaries offline."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from helper import cleanup, manage


def resource(kind, name, namespace=None, owner=None):
    obj = {'apiVersion': 'v1' if kind == 'Secret' else 'storage.loft.sh/v1', 'kind': kind,
           'metadata': {'name': name, 'uid': 'uid-'+name, 'resourceVersion': '1'}}
    if namespace:
        obj['metadata']['namespace'] = namespace
    if owner:
        obj['metadata'].update(labels={'app.kubernetes.io/managed-by': manage.MANAGER},
                               annotations={manage.OWNER: manage.helper_id(owner)})
    return obj


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.args = manage.parser().parse_args(['--state-dir', str(self.root), 'teardown', '--timeout', '5'])
        self.config = {'state_dir': str(self.root/'nodes'), 'limactl': '/usr/bin/true',
                       'service_label': 'test-helper', 'platform': {'context': 'host', 'namespace': 'platform',
                                                                 'provider': 'lima-local', 'template': 'lima-template'}}
        self.template = resource('VirtualClusterTemplate', 'lima-template', owner=self.root)
        self.provider = resource('NodeProvider', 'lima-local', owner=self.root)
        self.secret = resource('Secret', 'lima-local-credentials', 'platform', self.root)
        self.tenant = resource('VirtualClusterInstance', 'tenant-a', 'p-default')
        self.tenant['spec'] = {'templateRef': {'name': 'lima-template'}, 'clusterRef': {'namespace': 'tenant-host-ns'}}
        self.claim = resource('NodeClaim', 'claim-a', 'p-default')
        self.claim['spec'] = {'providerRef': 'lima-local', 'vClusterRef': 'tenant-a'}
        self.selected = {'targets': [('virtualclustertemplates.storage.loft.sh', self.template),
                                     ('nodeproviders.storage.loft.sh', self.provider), ('secret', self.secret)],
                         'tenants': [], 'claims': [], 'types': [], 'namespaces': [], 'vms': set(), 'legacy': False}

    def targets(self, config, kind, name, namespace=None):
        return {'virtualclustertemplates.storage.loft.sh': self.template,
                'nodeproviders.storage.loft.sh': self.provider, 'secret': self.secret}.get(kind)

    def test_plan_selects_its_tenants_and_excludes_other_providers(self):
        other = resource('VirtualClusterInstance', 'keep-me', 'p-other')
        other['spec'] = {'templateRef': {'name': 'other-template'}}
        own_inline = resource('VirtualClusterInstance', 'tenant-a', 'p-default')
        own_inline['spec'] = {'templateRef': {'name': 'custom-template'}}
        with patch.object(manage, 'get_resource', side_effect=self.targets), \
             patch.object(cleanup, 'items', side_effect=lambda c,r: {
                 cleanup.CLAIMS: [self.claim], cleanup.VCI: [own_inline, other], cleanup.TYPES: []}[r]):
            selected = cleanup.plan(self.config, self.root)
        self.assertEqual([v['metadata']['name'] for v in selected['tenants']], ['tenant-a'])
        self.assertEqual(len(selected['vms']), 1)

    def test_unmanaged_resources_need_explicit_opt_in(self):
        old = resource('Secret', 'lima-local-credentials', 'platform')
        with self.assertRaisesRegex(ValueError, '--include-unmanaged'):
            cleanup.allowed_resource(old, self.root, False)
        self.assertTrue(cleanup.allowed_resource(old, self.root, True))

    def test_another_installation_is_never_included(self):
        obj = resource('NodeProvider', 'lima-local', owner=self.root/'other')
        with self.assertRaisesRegex(ValueError, 'original installation directory'):
            cleanup.allowed_resource(obj, self.root, True)

    def test_legacy_opt_in_is_limited_to_inactive_resources(self):
        self.template = resource('VirtualClusterTemplate', 'lima-template')
        with patch.object(manage, 'get_resource', side_effect=self.targets), \
             patch.object(cleanup, 'items', side_effect=lambda c,r: {
                 cleanup.CLAIMS: [], cleanup.VCI: [self.tenant], cleanup.TYPES: []}[r]):
            with self.assertRaisesRegex(ValueError, 'still have tenants'):
                cleanup.plan(self.config, self.root, True)

    def test_raw_delete_has_uid_precondition_and_correct_secret_path(self):
        with patch.object(manage, 'get_resource', return_value=self.secret), \
             patch.object(manage, 'run') as command, patch('builtins.print'):
            cleanup.delete_object(self.config, 'secret', self.secret)
        argv = command.call_args.args[0]
        self.assertIn('/api/v1/namespaces/platform/secrets/lima-local-credentials', argv)
        body = json.loads(command.call_args.kwargs['input'])
        self.assertEqual(body['preconditions'], {'uid': self.secret['metadata']['uid'], 'resourceVersion': '1'})
        self.assertNotIn('--force', argv)

    def test_resource_replacement_prevents_delete(self):
        changed = json.loads(json.dumps(self.secret))
        changed['metadata']['uid'] = 'replacement-uid'
        with patch.object(manage, 'get_resource', return_value=changed), patch.object(manage, 'run') as command:
            with self.assertRaisesRegex(ValueError, 'identity changed'):
                cleanup.delete_object(self.config, 'secret', self.secret)
        command.assert_not_called()

    def test_dry_run_does_not_delete_stop_or_purge(self):
        (self.root/'config.json').write_text('{}')
        self.args.dry_run = True
        with patch.object(manage, 'configuration', return_value=self.config), \
             patch.object(cleanup, 'plan', return_value=self.selected), \
             patch.object(cleanup, 'delete_object') as delete, patch.object(manage, 'stop') as stop, \
             patch.object(cleanup, 'purge_files') as purge, patch('builtins.print'):
            cleanup.run_teardown(self.args)
        delete.assert_not_called()
        stop.assert_not_called()
        purge.assert_not_called()
        self.assertTrue((self.root/'config.json').exists())

    def test_worker_failure_preserves_provider_credentials_and_local_files(self):
        (self.root/'config.json').write_text('{}')
        with patch.object(manage, 'configuration', return_value=self.config), \
             patch.object(cleanup, 'plan', return_value=self.selected), \
             patch.object(cleanup, 'delete_object') as delete, \
             patch.object(cleanup, 'wait_workers', side_effect=RuntimeError('pending VM')), \
             patch.object(manage, 'stop') as stop, patch.object(cleanup, 'purge_files') as purge, \
             patch('builtins.print'):
            with self.assertRaisesRegex(RuntimeError, 'pending VM'):
                cleanup.run_teardown(self.args)
        delete.assert_not_called()
        stop.assert_not_called()
        purge.assert_not_called()
        self.assertTrue((self.root/'config.json').exists())

    def test_cleanup_keeps_helper_until_resources_are_gone(self):
        (self.root/'config.json').write_text('{}')
        events = []
        empty = dict(self.selected, targets=[])
        with patch.object(manage, 'configuration', return_value=self.config), \
             patch.object(cleanup, 'plan', side_effect=[self.selected, empty]), \
             patch.object(cleanup, 'delete_object', side_effect=lambda c,r,o: events.append('delete:'+r)), \
             patch.object(cleanup, 'wait_workers', side_effect=lambda *a: events.append('workers-gone')), \
             patch.object(manage, 'get_resource', return_value=None), patch.object(cleanup, 'items', return_value=[]), \
             patch.object(manage, 'stop', side_effect=lambda *a: events.append('stop')), \
             patch.object(cleanup, 'purge_files', side_effect=lambda *a: events.append('purge')), patch('builtins.print'):
            cleanup.run_teardown(self.args)
        self.assertLess(events.index('workers-gone'), events.index('delete:virtualclustertemplates.storage.loft.sh'))
        self.assertLess(events.index('workers-gone'), events.index('delete:secret'))
        self.assertLess(events.index('delete:secret'), events.index('stop'))
        self.assertLess(events.index('stop'), events.index('purge'))

    def test_purge_removes_generated_files_and_preserves_unrelated_files(self):
        for filename in (*cleanup.GENERATED, 'config.json'):
            (self.root/filename).write_text('private')
        (self.root/'notes.txt').write_text('keep this')
        Path(self.config['state_dir']).mkdir()
        cleanup.purge_files(self.config, self.root, self.selected)
        self.assertEqual([p.name for p in self.root.iterdir()], ['notes.txt'])

    def test_purge_removes_lab_exports_after_tenants_were_already_deleted(self):
        (self.root/'config.json').write_text('{}')
        exports = self.root/'autoscaling'
        exports.mkdir()
        (exports/'lima-demo-a.kubeconfig').write_text('private export')
        (exports/'notes.txt').write_text('keep this')
        cleanup.purge_files(self.config, self.root, self.selected)
        self.assertFalse((exports/'lima-demo-a.kubeconfig').exists())
        self.assertTrue((exports/'notes.txt').exists())

    def test_purge_refuses_nonempty_worker_state(self):
        state = Path(self.config['state_dir'])
        state.mkdir()
        (state/'reservation.tmp').write_text('unresolved')
        (self.root/'config.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'not empty'):
            cleanup.purge_files(self.config, self.root, self.selected)
        self.assertTrue((self.root/'config.json').exists())

    def test_missing_configuration_is_a_noop(self):
        with patch.object(cleanup, 'plan') as plan, patch('builtins.print'):
            cleanup.run_teardown(self.args)
        plan.assert_not_called()

    def test_pending_actual_vm_prevents_cleanup_completion(self):
        vm = 'vclima-'+'a'*24
        selected = dict(self.selected, vms={vm})
        with patch.object(cleanup, 'items', return_value=[]), patch.object(cleanup, 'records', return_value=[]), \
             patch.object(cleanup, 'vm_inventory', return_value={vm}), \
             patch.object(cleanup.time, 'monotonic', return_value=100):
            with self.assertRaisesRegex(RuntimeError, 'private state are preserved'):
                cleanup.wait_workers(self.config, selected, 99)

    def test_new_tenant_does_not_have_its_claim_deleted(self):
        selected = dict(self.selected, tenants=[self.tenant])
        other = resource('VirtualClusterInstance', 'new-tenant', 'p-default')
        claim = json.loads(json.dumps(self.claim))
        claim['spec']['vClusterRef'] = 'new-tenant'
        with patch.object(cleanup, 'records', return_value=[]), \
             patch.object(cleanup, 'items', side_effect=[[claim], [self.tenant, other]]), \
             patch.object(cleanup, 'delete_object') as delete:
            with self.assertRaisesRegex(RuntimeError, 'new tenant'):
                cleanup.wait_workers(self.config, selected, 999999)
        delete.assert_not_called()
