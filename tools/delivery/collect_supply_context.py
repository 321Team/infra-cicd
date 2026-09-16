#!/usr/bin/env python3
"""Collect local Runner supply context under its account home; no network probes."""
import argparse
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import pwd
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools' / 'parallel-prep'))
import collect_parallel_prep as prep  # noqa: E402

UNIT = 'actions.runner.321Team-infra-cicd.cicd-runner01.service'
PROPERTIES = ('LoadState', 'ActiveState', 'SubState', 'User', 'MainPID')
ENV_NAMES = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
             'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy',
             'PIP_INDEX_URL', 'PIP_EXTRA_INDEX_URL', 'PIP_CONFIG_FILE',
             'PIP_TRUSTED_HOST', 'REQUESTS_CA_BUNDLE', 'SSL_CERT_FILE')


def service_state():
    argv = ['systemctl', 'show', UNIT, '--no-pager']
    for name in PROPERTIES:
        argv += ['--property', name]
    record = prep.run(argv)
    if record.get('rc') != 0 or 'text' not in record:
        return {'status': 'UNAVAILABLE'}
    fields = {}
    for line in record['text'].splitlines():
        name, sep, value = line.partition('=')
        if not sep or name not in PROPERTIES or name in fields:
            return {'status': 'UNAVAILABLE'}
        fields[name] = value
    if set(fields) != set(PROPERTIES):
        return {'status': 'UNAVAILABLE'}
    return {'status': 'COLLECTED', 'fields': fields}


def collect(home, env):
    routes = prep.query(['ip', '-j', '-4', 'route', 'show', 'table', 'all'])
    rules = prep.query(['ip', '-j', '-4', 'rule', 'show'])
    for item in (routes, rules):
        if item.get('status') == 'COLLECTED' and not isinstance(item.get('data'), list):
            item.clear()
            item['status'] = 'UNAVAILABLE'
    servers = prep.resolver_servers()
    service = service_state()
    config_paths = (Path('/etc/pip.conf'), home / '.config/pip/pip.conf',
                    home / '.pip/pip.conf', Path('/etc/containers/registries.conf'),
                    Path('/etc/containers/registries.conf.d'),
                    home / '.config/containers/registries.conf',
                    home / '.config/containers/registries.conf.d')
    tools = {name: prep.text_query([name, '--version'])
             for name in ('python3', 'podman', 'skopeo')}
    complete = (all(r.get('status') == 'COLLECTED' for r in (routes, rules, service))
                and bool(servers))
    return {
        'local_collection': 'COMPLETE' if complete else 'PARTIAL',
        'ipv4_routes': routes, 'ipv4_rules': rules, 'resolver_nameservers': servers,
        'runner_service': service, 'tools': tools,
        'login_environment_variable_present': {name: name in env for name in ENV_NAMES},
        'configuration_metadata': [prep.metadata(path) for path in config_paths],
        'home_disk_free_bytes': shutil.disk_usage(home).free,
        'effective_pip_registry_proxy_settings': 'NOT_INSPECTED',
        'runner_job_environment': 'NOT_INSPECTED',
    }


def main(argv=None):
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    if sys.version_info < (3, 11):
        print('LOCAL_COLLECTION=FAIL\nERROR_CODE=PYTHON_3_11_REQUIRED')
        return 1
    os.umask(0o077)
    try:
        if os.getuid() != 1000 or os.geteuid() != 1000:
            print('LOCAL_COLLECTION=FAIL\nERROR_CODE=RUNNER_UID_REQUIRED')
            return 3
        ident = prep.identity('runner')
        if ident['status'] != 'MATCHED':
            print('LOCAL_COLLECTION=FAIL\nERROR_CODE=HOST_IP_OR_USER_MISMATCH')
            return 3
        # Resolve the account home, not a possibly overridden HOME variable.
        home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        started = datetime.now(timezone.utc).isoformat()
        inventory = collect(home, os.environ)
        report = {
            'schema': 'P1-CICD-supply-local-1', 'role': 'runner-supply',
            'started_at_utc': started, 'finished_at_utc': datetime.now(timezone.utc).isoformat(),
            'identity': {**ident, 'uid': 1000}, 'inventory': inventory,
            'source_hashes': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in (Path(__file__).resolve(), Path(prep.__file__).resolve())},
            'network_probes': 'NOT_EXECUTED', 'changes_applied': False,
            'supply_approval': 'NOT_VERIFIED', 'delivery_gate': 'NOT_VERIFIED',
            'limitations': [
                'COMPLETE means local route/rule/resolver/service data was collected, not network readiness.',
                'Tool failures and missing config files remain individual observations.',
                'No environment values, pip/registry config contents, credentials or process environment are read.',
                'Login environment presence does not establish the actual Runner job environment.',
                'No external DNS, TLS, HTTP, registry query, package install, image pull or service change is requested.',
            ],
        }
        _, archive, sidecar = prep.save_report(report, home)
        print('LOCAL_COLLECTION=' + inventory['local_collection'])
        print('NETWORK_SUPPLY=NOT_TESTED\nSUPPLY_APPROVAL=NOT_VERIFIED\nDELIVERY_GATE=NOT_VERIFIED')
        print('ARCHIVE=' + str(archive) + '\nSIDECAR=' + str(sidecar))
        return 0 if inventory['local_collection'] == 'COMPLETE' else 2
    except (OSError, ValueError, KeyError, TypeError):
        print('LOCAL_COLLECTION=FAIL\nERROR_CODE=LOCAL_COLLECTION_OR_WRITE_ERROR')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
