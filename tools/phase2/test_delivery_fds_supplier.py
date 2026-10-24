"""SYNTHETIC explicit assembly inputs. No login, network, SSH or real SITE."""
import copy
from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_fds_operating as operating
import delivery_job_site as declaration
import delivery_login_channel as deferred
import delivery_oauth_login as login
import delivery_operating_supplier as supply
import delivery_ready_pull as ready
import delivery_ssh_bridge as ssh
import test_delivery_fds_operating as fixture
import test_delivery_oauth_login as oauth
import test_delivery_transport as http
from test_delivery_job import CONTEXT


class SupplierAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.OperatingSelectionTests('runTest')
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        context = Mock(); context.load_verify_locations = Mock()
        p = patch.object(login.ssl, 'SSLContext', return_value=context)
        p.start(); self.addCleanup(p.stop)
        self.provider = login.BasicChallengeLogin(origin=oauth.ORIGIN,
            ca_pem=http.CA, ca_sha256=core.digest(http.CA), endpoint_ip='192.0.2.20',
            username=operating.ISSUANCE_OPERATOR, password=oauth.PASSWORD,
            deadline=20, monotonic=self.f.f.clock)
        self.addCleanup(self.provider.close)
        self.config = self.f.args['config']
        repos = tuple(r.repository for r in self.f.args['routes'])
        self.args = dict(login_provider=self.provider, endpoint_ip='192.0.2.10',
            repositories=repos, source_tokens=[{'repository':r.owner+'/'+r.name,
                'token':'SYNTHETIC_SOURCE_TOKEN_NOT_REAL'} for r in repos],
            context=CONTEXT, workspace='/synthetic/workspace', port=2222,
            host_key_alias='SYNTHETIC-runner', remote_python='/synthetic/python3',
            remote_timeout_seconds=5, operation_deadline=20, cleanup_deadline=30,
            monotonic=self.f.f.clock, wall=self.f.f.wall)
        for target, attribute in ((ssh.subprocess,'Popen'),(Path,'open')):
            p = patch.object(target, attribute, side_effect=AssertionError('NO_IO'))
            p.start(); self.addCleanup(p.stop)

    def build(self, **changes):
        value = operating.build_login_supplier(self.config, **(self.args | changes))
        self.addCleanup(value.close)
        return value

    def assert_unconsumed(self):
        self.assertFalse(self.provider._used)
        self.assertEqual(oauth.PASSWORD, self.provider._password)
        self.assertEqual('NOT_ATTEMPTED',self.provider.status()['login'])
        self.f.f.clock.assert_not_called(); self.f.f.wall.assert_not_called()
        self.assertIsNone(declaration.SITE)

    def test_inert_exact_components_trust_context_and_copy_then_owned_close(self):
        value = self.build()
        self.assertIs(type(value), supply.MemoryOperatingSupplier)
        self.assertIs(type(value._actor), deferred.LoginActorChannel)
        self.assertIs(type(value._ready), ready.SshReadyPull)
        self.assertIs(type(value._bridge), ssh.SshCredentialBridge)
        self.assertIs(self.provider, value._actor._provider)
        self.assertEqual(self.config.binding.key, value._ready._selected['target']['binding_key'])
        self.assertEqual(CONTEXT.run_id, value._ready._selected['target']['run_id'])
        self.assertEqual([r.owner+'/'+r.name for r in self.args['repositories']],
                         value._ready._selected['repositories'])
        b, r = value._bridge._config, value._ready._config
        self.assertEqual(b,replace(r,remote_helper=b.remote_helper))
        self.assertTrue(r.remote_helper.endswith('/'+operating.READY_ENTRYPOINT))
        self.assertIn('/p2-job-'+CONTEXT.run_id+'-'+str(CONTEXT.attempt)+'/',b.remote_helper)
        self.assertEqual((2222,'SYNTHETIC-runner','/synthetic/python3'),
                         (b.port,b.host_key_alias,b.remote_python))
        self.assertIs(value._bridge._budget.clock,self.f.f.clock)
        self.assertEqual(20,value._ready._budget.limit)
        self.assert_unconsumed()
        original = copy.deepcopy(value._sources)
        self.args['source_tokens'][0]['token'] = 'CALLER_CHANGED'
        self.assertEqual(original,value._sources)
        for secret in (oauth.PASSWORD,original[0]['token']):
            self.assertNotIn(secret,repr(value)+json.dumps(value.status()))
        value.close(); self.assertTrue(self.provider._used)
        self.assertIsNone(self.provider._password)
        self.assertEqual('NOT_ATTEMPTED',value.status()['actor_server_revocation'])

    def test_every_input_required_and_no_provider_consumed_on_missing_value(self):
        for key in self.args:
            with self.subTest(missing=key), self.assertRaises(TypeError):
                operating.build_login_supplier(self.config,**{k:v for k,v in self.args.items() if k!=key})
            self.assert_unconsumed()

    def test_invalid_context_trust_repository_or_sources_leave_caller_ownership(self):
        cases = ({'port':None},{'host_key_alias':''},{'workspace':''},{'remote_python':''},
            {'context':replace(CONTEXT,run_id='')},{'repositories':()},
            {'repositories':self.args['repositories']*2},
            {'repositories':(replace(self.args['repositories'][0],repository_id=0),)},
            {'source_tokens':[]},{'source_tokens':[{'repository':'synthetic/other','token':'SYNTHETIC_TOKEN'}]},
            {'endpoint_ip':'127.0.0.1'},{'operation_deadline':19},
            {'cleanup_deadline':20},{'monotonic':lambda:10})
        for change in cases:
            with self.subTest(fields=tuple(change)),self.assertRaises(Exception):self.build(**change)
            self.assert_unconsumed()

    def test_late_failure_and_interrupt_keep_borrowed_provider_and_sanitize(self):
        for error in (ValueError(oauth.PASSWORD), KeyboardInterrupt(oauth.PASSWORD), SystemExit(oauth.PASSWORD)):
            with patch.object(supply,'MemoryOperatingSupplier',side_effect=error):
                with self.assertRaises(type(error) if isinstance(error,(KeyboardInterrupt,SystemExit)) else Exception) as caught:
                    self.build()
            self.assertIsNone(caught.exception.__context__)
            self.assertNotIn(oauth.PASSWORD,str(caught.exception))
            self.assert_unconsumed()

    def test_assembled_ready_failure_does_not_login_and_releases_owned_password(self):
        value = self.build()
        with (patch.object(value._ready,'pull',side_effect=ValueError('SYNTHETIC_FAILURE')),
              patch.object(self.provider,'login') as mint):
            with self.assertRaises(supply.SupplyError):value.send()
            mint.assert_not_called()
        self.assertTrue(self.provider._used); self.assertIsNone(self.provider._password)
        self.assertIsNone(value.take_receipt()); self.assertIsNone(value.take_login_metadata())
        self.assertIsNone(declaration.SITE)


if __name__ == '__main__': unittest.main()
