"""Dated real non-secret input selection; credentials/context are SYNTHETIC.

All I/O is forbidden. Tests assemble existing objects and never send/log in.
"""
import hashlib
from pathlib import Path
import socket
import unittest
from unittest.mock import Mock, patch

import delivery_fds_observed_connection as observed
from delivery_actor_channel import ActorChannelError
import delivery_fds_operating as operating
import delivery_fds_site_inputs as profile
import delivery_job_site as declaration
import test_delivery_fds_supplier as fixture


class ObservedInputsTests(unittest.TestCase):
    def setUp(self):
        for name in ('socket', 'create_connection', 'getaddrinfo'):
            block = patch.object(socket,name,side_effect=AssertionError('NO_NETWORK'))
            block.start(); self.addCleanup(block.stop)

    def test_dated_trust_is_oauth_metadata_not_sa_issuer_and_does_not_discover(self):
        with patch.object(socket, 'socket', side_effect=AssertionError('NO_NETWORK')), \
             patch.object(Path, 'open', side_effect=AssertionError('NO_FILE')):
            inputs = operating.observed_login_inputs()
            state = observed.selection_status()
        self.assertEqual(observed.OAUTH_ORIGIN, inputs['origin'])
        self.assertNotEqual(profile.ISSUER, inputs['origin'])
        self.assertNotEqual(profile.API_ORIGIN, inputs['origin'])
        self.assertEqual(hashlib.sha256(inputs['ca_pem']).hexdigest(), inputs['ca_sha256'])
        self.assertEqual(operating.ISSUANCE_OPERATOR, inputs['username'])
        self.assertNotIn('password', inputs)
        self.assertFalse(state['basic_login_verified'])
        self.assertFalse(state['job_checkout_helper_deployment_verified'])
        self.assertFalse(state['runtime_authorized'])
        self.assertIsNone(declaration.SITE)

    def test_selected_oauth_builds_existing_provider_without_prompt_clock_or_login(self):
        from delivery_oauth_login import BasicChallengeLogin
        clock=Mock(side_effect=AssertionError('NO_CLOCK'))
        inputs=operating.observed_login_inputs()
        provider=BasicChallengeLogin(**inputs,password='SYNTHETIC_PASSWORD_ONLY',
                                     deadline=20,monotonic=clock)
        self.addCleanup(provider.close)
        self.assertEqual(observed.OAUTH_ENDPOINT_IP,provider._ip)
        self.assertEqual(observed.OAUTH_ORIGIN,provider._origin)
        self.assertEqual('NOT_ATTEMPTED',provider.status()['login'])
        self.assertIsNone(provider.take_metadata())
        clock.assert_not_called()
        self.assertIsNone(declaration.SITE)

    def test_returned_input_mutation_cannot_change_later_selection_or_gate(self):
        value = observed.supplier_inputs(); value['port'] = 1
        login = observed.login_inputs(); login['origin'] = 'https://SYNTHETIC.invalid'
        state = observed.selection_status(); state['runtime_authorized'] = True
        self.assertEqual(22, observed.supplier_inputs()['port'])
        self.assertEqual(observed.OAUTH_ORIGIN, observed.login_inputs()['origin'])
        self.assertFalse(observed.selection_status()['runtime_authorized'])
        self.assertIsNone(declaration.SITE)


class ObservedSupplierTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.SupplierAssemblyTests('runTest')
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.args = {k:v for k,v in self.f.args.items()
                     if k not in ('endpoint_ip','port','host_key_alias','remote_python')}

    def build(self, **changes):
        value = operating.build_observed_login_supplier(self.f.config, **(self.args | changes))
        self.addCleanup(value.close)
        return value

    def test_existing_supplier_uses_selected_transport_but_actual_job_workspace(self):
        value = self.build()
        bridge, ready = value._bridge._config, value._ready._config
        self.assertEqual((22,observed.SSH_HOST_KEY_ALIAS,observed.REMOTE_PYTHON),
                         (bridge.port,bridge.host_key_alias,bridge.remote_python))
        self.assertEqual(observed.API_ENDPOINT_IP,value._actor._args['endpoint_ip'])
        self.assertEqual(self.args['context'].run_id,value._ready._selected['target']['run_id'])
        self.assertTrue(bridge.remote_helper.startswith('/synthetic/workspace/p2-job-'))
        self.assertTrue(bridge.remote_helper.endswith(operating.HELPER_ENTRYPOINT))
        self.assertTrue(ready.remote_helper.endswith(operating.READY_ENTRYPOINT))
        self.assertNotIn(observed.OBSERVED_WORKSPACE,bridge.remote_helper)
        self.assertIs(self.f.provider,value._actor._provider)
        self.f.assert_unconsumed()

    def test_missing_dynamic_input_or_override_has_no_credential_or_path_fallback(self):
        for field in self.args:
            with self.subTest(field=field), self.assertRaises(TypeError):
                operating.build_observed_login_supplier(self.f.config,
                    **{k:v for k,v in self.args.items() if k!=field})
            self.f.assert_unconsumed()
        with self.assertRaises(TypeError):self.build(port=1)
        self.f.assert_unconsumed()

    def test_bad_workspace_or_late_failure_preserves_provider_ownership(self):
        for workspace in ('',observed.OBSERVED_WORKSPACE+'/../other'):
            with self.subTest(workspace=workspace),self.assertRaises(Exception):
                self.build(workspace=workspace)
            self.f.assert_unconsumed()
        with patch.object(operating,'build_login_supplier',side_effect=ValueError('SYNTHETIC_FAILURE')):
            with self.assertRaises(ActorChannelError) as caught:self.build()
            self.assertIsNone(caught.exception.__context__)
            self.assertEqual('P2_FDS_LOGIN_SUPPLIER_INVALID',str(caught.exception))
        self.f.assert_unconsumed()


if __name__ == '__main__':unittest.main()
