# SPDX-License-Identifier: Apache-2.0
"""Remove one prepared installation through Platform's managed lifecycle."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import quote

from helper import manage

VCI = 'virtualclusterinstances.storage.loft.sh'
CLAIMS = 'nodeclaims.storage.loft.sh'
TYPES = 'nodetypes.storage.loft.sh'
GENERATED = (
    'api-token', 'server.key', 'server.crt', 'openssl.cnf', 'node.tf',
    'node-provider.yaml', 'tenant-template.yaml', 'credentials.json',
    'launchagent.plist', 'helper.log', 'helper.err',
)


def ref_name(value):
    return value.get('name', '') if isinstance(value, dict) else value or ''


def key(resource):
    metadata = resource['metadata']
    return metadata.get('namespace', ''), metadata['name']


def items(config, resource):
    try:
        result = manage.run(manage.kube(config, 'get', resource, '-A', '-o', 'json', '--request-timeout=15s'), timeout=25)
    except subprocess.TimeoutExpired:
        raise RuntimeError('Platform request timed out; private configuration is preserved') from None
    return json.loads(result.stdout)['items']


def records(config):
    result = []
    for path in sorted(Path(config['state_dir']).glob('*.json')):
        node = json.loads(path.read_text())
        identity = node.get('id', '')
        if not re.fullmatch(r'[a-f0-9]{24}', identity) or node.get('vm_name') != 'vclima-' + identity:
            raise ValueError('Invalid ownership record; reconcile it before teardown: ' + path.name)
        if not all(isinstance(node.get(field), str) and node[field] for field in ('project', 'tenant', 'claim')):
            raise ValueError('Incomplete ownership record; reconcile it before teardown: ' + path.name)
        result.append(node)
    return result


def provider_claim(claim, provider):
    spec = claim.get('spec', {})
    return (ref_name(spec.get('providerRef')) == provider or
            ref_name(spec.get('typeRef')).startswith(provider + '.'))


def claim_tenant(claim):
    return claim['metadata'].get('namespace', ''), ref_name(claim.get('spec', {}).get('vClusterRef'))


def managed_objects(config):
    p = config['platform']
    return [
        ('virtualclustertemplates.storage.loft.sh', p['template'], None),
        ('nodeproviders.storage.loft.sh', p['provider'], None),
        ('secret', p['provider'] + '-credentials', p['namespace']),
    ]


def allowed_resource(resource, directory, include_unmanaged):
    conflict = manage.ownership_conflict(resource, directory)
    if not conflict:
        return False
    metadata = resource.get('metadata', {})
    annotations = metadata.get('annotations') or {}
    labels = metadata.get('labels') or {}
    if annotations.get(manage.OWNER) or labels.get('app.kubernetes.io/managed-by') not in (None, '', manage.MANAGER):
        raise ValueError('Teardown refused: ' + conflict + '. Use the original installation directory.')
    if not include_unmanaged:
        raise ValueError(
            'Teardown found an unmanaged resource: ' + conflict + '. '
            'For inactive leftovers from an older setup, preview with teardown --include-unmanaged --dry-run, '
            'then use teardown --include-unmanaged. Other installations are never included.'
        )
    return True


def plan(config, directory, include_unmanaged=False):
    p = config['platform']
    targets = []
    legacy = False
    for resource, name, namespace in managed_objects(config):
        obj = manage.get_resource(config, resource, name, namespace)
        if obj:
            legacy = allowed_resource(obj, directory, include_unmanaged) or legacy
            targets.append((resource, obj))
    owned = records(config)
    all_claims = items(config, CLAIMS)
    claims = [c for c in all_claims if provider_claim(c, p['provider'])]
    tenant_keys = {claim_tenant(c) for c in claims}
    tenant_keys.update((n['project'], n['tenant']) for n in owned)
    tenants = [v for v in items(config, VCI)
               if ref_name(v.get('spec', {}).get('templateRef')) == p['template'] or key(v) in tenant_keys]
    tenant_keys.update(key(v) for v in tenants)
    unrelated = [c for c in all_claims if claim_tenant(c) in tenant_keys and not provider_claim(c, p['provider'])]
    if unrelated:
        raise ValueError('A selected tenant also has claims from another provider; remove that tenant explicitly before teardown.')
    if legacy and (tenants or claims or owned):
        raise ValueError('Unmanaged legacy resources still have tenants, claims or worker records. Clean them through the original installation first.')
    node_types = [t for t in items(config, TYPES) if t['metadata']['name'].startswith(p['provider'] + '.')]
    namespaces = sorted({v.get('spec', {}).get('clusterRef', {}).get('namespace') for v in tenants} - {None, ''})
    vms = {n['vm_name'] for n in owned}
    vms.update('vclima-' + hashlib.sha256((c['metadata']['namespace'] + '/' + c['metadata']['name']).encode()).hexdigest()[:24] for c in claims)
    return {'targets': targets, 'tenants': tenants, 'claims': claims, 'types': node_types,
            'namespaces': namespaces, 'vms': vms, 'legacy': legacy}


def delete_object(config, resource, expected):
    metadata = expected['metadata']
    namespace, name = key(expected)
    current = manage.get_resource(config, resource, name, namespace or None)
    if current is None:
        return
    uid = metadata.get('uid')
    if not uid or current['metadata'].get('uid') != uid:
        raise ValueError('Resource identity changed during teardown; no deletion attempted: ' + name)
    api_version = current['apiVersion']
    prefix = '/api/v1' if api_version == 'v1' else '/apis/' + api_version
    plural = {'secret': 'secrets', 'namespace': 'namespaces'}.get(resource, resource.split('.', 1)[0])
    path = prefix + ('/namespaces/' + quote(namespace, safe='') if namespace else '')
    path += '/' + plural + '/' + quote(name, safe='')
    for field, ownership_key in (('labels', 'app.kubernetes.io/managed-by'), ('annotations', manage.OWNER)):
        if (metadata.get(field) or {}).get(ownership_key) != (current['metadata'].get(field) or {}).get(ownership_key):
            raise ValueError('Resource ownership changed during teardown; no deletion attempted: ' + name)
    conditions = {'uid': uid}
    if current['metadata'].get('resourceVersion'):
        conditions['resourceVersion'] = current['metadata']['resourceVersion']
    body = {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'propagationPolicy': 'Background',
            'preconditions': conditions}
    try:
        manage.run(manage.kube(config, 'delete', '--raw', path, '-f', '-', '--request-timeout=15s'),
                   input=json.dumps(body).encode(), timeout=25)
    except RuntimeError:
        if manage.get_resource(config, resource, name, namespace or None) is None:
            return
        raise RuntimeError('Deletion failed for ' + current['kind'] + '/' + name +
                           '; private state is preserved. Inspect Platform status and retry teardown.') from None
    print('Deletion requested:', current['kind'] + '/' + (namespace + '/' if namespace else '') + name, flush=True)


def vm_inventory(config):
    env = dict(os.environ, LIMA_HOME=config['lima_home']) if config.get('lima_home') else None
    result = manage.run([config['limactl'], '--tty=false', 'list', '--format', '{{.Name}}'], env=env, timeout=30)
    return set(result.stdout.decode().splitlines())


def show_plan(config, directory, selected):
    print('Teardown plan — host context:', config['platform']['context'])
    for tenant in selected['tenants']:
        print('  Tenant:', '/'.join(key(tenant)))
    for claim in selected['claims']:
        print('  NodeClaim:', '/'.join(key(claim)))
    for vm in sorted(selected['vms']):
        print('  Managed VM:', vm)
    for resource, obj in selected['targets']:
        print('  Resource:', obj['kind'] + '/' + '/'.join(filter(None, key(obj))))
    print('  Helper service:', config['service_label'])
    print('  Generated configuration, credentials, manifests and logs:', directory)
    print('  Lab kubeconfig exports:', directory / 'autoscaling' / '*.kubeconfig')
    if selected['legacy']:
        print('  Including inactive unmanaged resources at the configured names.')


def wait_workers(config, selected, deadline):
    tenant_ids = {key(v): v['metadata']['uid'] for v in selected['tenants']}
    requested = set()
    while True:
        owned = records(config)
        selected['vms'].update(n['vm_name'] for n in owned)
        claims = [c for c in items(config, CLAIMS) if provider_claim(c, config['platform']['provider'])]
        selected['vms'].update('vclima-' + hashlib.sha256((c['metadata']['namespace'] + '/' + c['metadata']['name']).encode()).hexdigest()[:24] for c in claims)
        all_tenants = items(config, VCI)
        all_active = {key(v) for v in all_tenants}
        if any(claim_tenant(c) in all_active and claim_tenant(c) not in tenant_ids for c in claims):
            raise RuntimeError('A new tenant started using the provider during teardown; private state is preserved.')
        tenants = [v for v in all_tenants if key(v) in tenant_ids]
        for tenant in tenants:
            if tenant['metadata']['uid'] != tenant_ids[key(tenant)]:
                raise ValueError('A tenant was recreated during teardown; private state is preserved.')
        # Orphan claims need an explicit managed deletion request once their tenant is absent.
        active = all_active
        for claim in claims:
            identity = claim['metadata']['uid']
            if claim_tenant(claim) not in active and identity not in requested:
                delete_object(config, CLAIMS, claim)
                requested.add(identity)
        namespaces = [ns for ns in selected['namespaces'] if manage.get_resource(config, 'namespace', ns)]
        vms = vm_inventory(config) & selected['vms'] if selected['vms'] else set()
        if not tenants and not claims and not owned and not namespaces and not vms:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('Teardown timed out while waiting for tenant/worker cleanup. Helper, credentials and private state are preserved; retry teardown after resolving the pending resources.')
        print(f'Waiting for cleanup: tenants={len(tenants)} claims={len(claims)} records={len(owned)} namespaces={len(namespaces)} VMs={len(vms)}', flush=True)
        time.sleep(min(2, max(0, deadline - time.monotonic())))


def purge_files(config, directory, selected):
    state = Path(config['state_dir'])
    if state.exists() and any(state.iterdir()):
        raise ValueError('Worker state directory is not empty; local files are preserved for recovery.')
    exports = directory / 'autoscaling'
    if state.is_symlink() or exports.is_symlink():
        raise ValueError('Generated state/export directory is a symlink; local files are preserved for review.')
    # Remove only known generated files, never an arbitrary directory tree.
    for filename in GENERATED:
        (directory / filename).unlink(missing_ok=True)
    for path in exports.glob('*.kubeconfig'):
        path.unlink()
    if exports.exists() and not any(exports.iterdir()):
        exports.rmdir()
    if state.exists():
        state.rmdir()
    (directory / 'config.json').unlink()
    if not any(directory.iterdir()):
        directory.rmdir()


def run_teardown(args):
    directory = manage.local_directory(args)
    if not (directory / 'config.json').exists():
        print('No prepared configuration found in', directory, '— no resources were changed.')
        print('If resources remain, restore the original private configuration before teardown.')
        return
    config = manage.configuration(args)
    selected = plan(config, directory, args.include_unmanaged)
    show_plan(config, directory, selected)
    if args.dry_run:
        print('Dry run: no resources, services or files changed.')
        return
    if selected['claims'] or selected['vms']:
        manage.start(args)
        manage.check(args)
    deadline = time.monotonic() + args.timeout
    # Platform resolves the template while finalizing tenant worker cleanup.
    # Keep it, the provider and credentials until all dependent workers are gone.
    for tenant in selected['tenants']:
        delete_object(config, VCI, tenant)
    wait_workers(config, selected, deadline)
    # Recheck ownership and dependents immediately before removing provider resources.
    refreshed = plan(config, directory, args.include_unmanaged)
    if refreshed['tenants'] or refreshed['claims'] or refreshed['vms']:
        raise RuntimeError('New dependents appeared during teardown; keep the helper running and retry.')
    for resource, obj in selected['targets']:
        delete_object(config, resource, obj)
    while True:
        remaining = [(resource, obj) for resource, obj in selected['targets']
                     if manage.get_resource(config, resource, key(obj)[1], key(obj)[0] or None)]
        types = [t for t in items(config, TYPES) if t['metadata']['name'].startswith(config['platform']['provider'] + '.')]
        provider_present = any(resource == 'nodeproviders.storage.loft.sh' for resource, obj in remaining)
        if not provider_present:
            for node_type in types:
                delete_object(config, TYPES, node_type)
        if not remaining and not types:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError('Provider resource cleanup timed out; helper and private configuration are preserved.')
        time.sleep(1)
    manage.stop(args)
    purge_files(config, directory, selected)
    print('Teardown complete. Repeat prepare → start → check → deploy with the original default names.')
