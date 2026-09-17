"""Local inventory and privacy tests; no external service is contacted."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import collect_supply_context as supply


class SupplyContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.object(supply.os, 'getuid', return_value=1000).start()
        patch.object(supply.os, 'geteuid', return_value=1000).start()
        account = supply.pwd.struct_passwd(('server01_cicd-runner', 'x', 1000, 1000,
                                           'Fixture', str(self.home), '/bin/bash'))
        patch.object(supply.pwd, 'getpwuid', return_value=account).start()
        self.identity = patch.object(supply.prep, 'identity', return_value={'status': 'MATCHED'}).start()
        self.query = patch.object(supply.prep, 'query', return_value={'status': 'COLLECTED', 'data': []}).start()
        patch.object(supply.prep, 'resolver_servers', return_value=['192.0.2.53']).start()
        patch.object(supply.prep, 'text_query', return_value={'status': 'UNAVAILABLE'}).start()
        self.run = patch.object(supply.prep, 'run', return_value={
            'rc': 0, 'text': 'LoadState=loaded\nActiveState=active\nSubState=running\nUser=server01_cicd-runner\nMainPID=42\n'}).start()
        self.network = patch.object(supply.prep, 'github_probe', side_effect=AssertionError('network prohibited')).start()

    def invoke(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = supply.main([])
        return rc, out.getvalue()

    def test_wrong_uid_or_host_stops_before_inventory_and_writes(self):
        with patch.object(supply.os, 'geteuid', return_value=0):
            self.assertEqual(self.invoke()[0], 3)
            self.identity.assert_not_called()
        self.identity.return_value = {'status': 'MISMATCH'}
        self.assertEqual(self.invoke()[0], 3)
        self.query.assert_not_called()
        self.assertEqual(list(self.home.iterdir()), [])

    def test_home_bundle_checksums_and_no_delivery_or_network_pass(self):
        with patch.dict(os.environ, {'HOME': '/wrong-home'}):
            rc, out = self.invoke()
        self.assertEqual(rc, 0)
        self.assertIn('NETWORK_SUPPLY=NOT_TESTED', out)
        folder, = self.home.glob('P1_CICD_Parallel_*')
        archive, = folder.glob('*.tar.gz')
        self.assertEqual(Path(str(archive) + '.sha256').read_text().split()[0],
                         hashlib.sha256(archive.read_bytes()).hexdigest())
        with tarfile.open(archive) as bundle:
            self.assertEqual(set(bundle.getnames()), {'preparation.json', 'SHA256SUMS'})
            raw = bundle.extractfile('preparation.json').read()
            self.assertEqual(bundle.extractfile('SHA256SUMS').read().decode().split()[0],
                             hashlib.sha256(raw).hexdigest())
            report = json.loads(raw)
        self.assertFalse(report['changes_applied'])
        self.assertEqual(report['supply_approval'], 'NOT_VERIFIED')
        self.assertEqual(report['delivery_gate'], 'NOT_VERIFIED')
        self.assertEqual(len(report['source_hashes']), 2)
        self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
        self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
        self.network.assert_not_called()

    def test_environment_and_config_values_are_not_collected(self):
        config = self.home / '.config/pip/pip.conf'
        config.parent.mkdir(parents=True)
        config.write_text('[global]\nindex-url=https://user:SENTINEL_CONFIG@example.invalid/simple\n')
        with patch.dict(os.environ, {'HTTPS_PROXY': 'https://user:SENTINEL_PROXY@proxy.invalid',
                                     'PIP_INDEX_URL': 'https://SENTINEL_INDEX@private.invalid'}):
            rc, out = self.invoke()
        self.assertEqual(rc, 0)
        raw = next(self.home.glob('P1_CICD_Parallel_*/preparation.json')).read_text()
        self.assertNotIn('SENTINEL', raw + out)
        data = json.loads(raw)['inventory']
        self.assertIs(data['login_environment_variable_present']['HTTPS_PROXY'], True)
        self.assertEqual(data['effective_pip_registry_proxy_settings'], 'NOT_INSPECTED')
        # Collection uses fixed local queries only, never pip/config/proc-env reads.
        self.assertEqual([c.args[0] for c in self.query.call_args_list],
                         [['ip', '-j', '-4', 'route', 'show', 'table', 'all'],
                          ['ip', '-j', '-4', 'rule', 'show']])
        self.assertNotIn('Environment', self.run.call_args.args[0])

    def test_missing_or_malformed_local_data_is_partial_and_still_saved(self):
        for data in ({'status': 'UNAVAILABLE'}, {'status': 'COLLECTED', 'data': {}}):
            with self.subTest(data=data):
                self.query.return_value = data
                rc, out = self.invoke()
                self.assertEqual(rc, 2)
                self.assertIn('LOCAL_COLLECTION=PARTIAL', out)
                self.assertIn('ARCHIVE=', out)

    def test_empty_resolver_list_is_collected_without_supply_pass(self):
        with patch.object(supply.prep, 'resolver_servers', return_value=[]):
            rc, out = self.invoke()
        self.assertEqual(rc, 0)
        self.assertIn('LOCAL_COLLECTION=COMPLETE', out)
        report = json.loads(next(self.home.glob('P1_CICD_Parallel_*/preparation.json')).read_text())
        self.assertEqual(report['inventory']['resolver_nameservers'], [])
        self.assertEqual(report['inventory']['local_collection'], 'COMPLETE')
        self.assertEqual(report['network_probes'], 'NOT_EXECUTED')
        self.assertEqual(report['supply_approval'], 'NOT_VERIFIED')
        self.assertEqual(report['delivery_gate'], 'NOT_VERIFIED')
        self.network.assert_not_called()

    def test_unreadable_resolver_is_partial_and_still_saved(self):
        with patch.object(supply.prep, 'resolver_servers', return_value=None):
            rc, out = self.invoke()
        self.assertEqual(rc, 2)
        self.assertIn('LOCAL_COLLECTION=PARTIAL', out)
        self.assertIn('ARCHIVE=', out)
        report = json.loads(next(self.home.glob('P1_CICD_Parallel_*/preparation.json')).read_text())
        self.assertIsNone(report['inventory']['resolver_nameservers'])
        self.assertEqual(report['inventory']['local_collection'], 'PARTIAL')

    def test_systemd_unexpected_or_duplicate_fields_are_not_exported(self):
        for raw in ('Environment=SENTINEL\n', 'LoadState=loaded\nLoadState=loaded\n', 'LoadState=loaded\n'):
            self.run.return_value = {'rc': 0, 'text': raw}
            self.assertEqual(supply.service_state(), {'status': 'UNAVAILABLE'})

    def test_collection_failure_has_no_exception_or_secret_output(self):
        self.query.side_effect = OSError('SENTINEL_ERROR')
        rc, out = self.invoke()
        self.assertEqual(rc, 1)
        self.assertNotIn('SENTINEL', out)


if __name__ == '__main__':
    unittest.main()
