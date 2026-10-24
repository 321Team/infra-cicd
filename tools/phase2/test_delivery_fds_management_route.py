"""SYNTHETIC installation arguments; selected historical route is not executed."""
from pathlib import Path
import socket
import subprocess
import unittest
from unittest.mock import Mock, patch

import delivery_fds_management_route as route
import delivery_fds_operating as operating
import delivery_job_site as declaration
import delivery_ssh_bridge as bridge
import test_delivery_fds_job_site as fixture


class ManagementRouteTests(unittest.TestCase):
    def setUp(self):
        self.args = dict(port=2222, host_key_alias='SYNTHETIC-host',
            remote_python='/synthetic/python', remote_helper='/synthetic/helper.py',
            remote_timeout_seconds=5)

    def test_existing_values_bind_to_inert_bridge_without_file_process_clock_or_credential(self):
        clock = Mock(side_effect=AssertionError('NO_CLOCK'))
        with patch.object(Path, 'open', side_effect=AssertionError('NO_FILE')), \
                patch.object(subprocess, 'Popen', side_effect=AssertionError('NO_PROCESS')), \
                patch.object(socket, 'socket', side_effect=AssertionError('NO_NETWORK')):
            value = operating.build_management_bridge_configuration(**self.args)
            sender = bridge.SshCredentialBridge(value, deadline=10, monotonic=clock)
        clock.assert_not_called()
        self.assertFalse(sender.supply_attempted)
        self.assertEqual(('10.250.10.20', 'server01_cicd-runner', 1000),
            (value.host_ip, value.user, value.listener_uid))
        self.assertEqual('/home/server01_ansible/.ssh/p1_ansible_ed25519', value.identity_file)
        self.assertEqual('/home/server01_ansible/.ssh/known_hosts', value.known_hosts)
        self.assertEqual('/synthetic/helper.py', value.remote_helper)
        self.assertIn('StrictHostKeyChecking=yes', value.argv())
        self.assertIn('IdentityAgent=none', value.argv())
        self.assertIsNone(declaration.SITE)

    def test_route_override_and_missing_installation_do_not_select_defaults(self):
        for field in ('host_ip', 'user', 'identity_file', 'known_hosts', 'listener_uid'):
            with self.subTest(field=field), self.assertRaises(TypeError):
                route.build_configuration(**self.args, **{field: 'SYNTHETIC_OVERRIDE'})
        for field in self.args:
            missing = self.args.copy(); del missing[field]
            with self.subTest(field=field), self.assertRaises(TypeError):
                route.build_configuration(**missing)

    def test_existing_bridge_validation_is_not_bypassed_for_explicit_inputs(self):
        for change in (dict(port=0), dict(host_key_alias='-unsafe'),
                       dict(remote_python='/synthetic/../python'),
                       dict(remote_helper='/synthetic/$(command)'), dict(remote_timeout_seconds=True)):
            with self.subTest(change=change), self.assertRaises(bridge.BridgeError):
                route.build_configuration(**(self.args | change))

    def test_same_account_uid_is_explicitly_connected_to_existing_parent_without_fallback(self):
        f = fixture.AssemblyTests('runTest'); f.setUp(); self.addCleanup(f.doCleanups)
        args = {k: v for k, v in f.args.items() if k != 'runner'}
        selected = operating.assemble_job(**(args | {'expected_helper_uid': route.EXPECTED_HELPER_UID}))
        config = route.build_configuration(**self.args)
        self.assertEqual('server01_cicd-runner', selected.runner.account)
        self.assertEqual(1000, selected.build._owner._uid)
        self.assertEqual(config.listener_uid, selected.runner.uid)
        self.assertIs(operating.EXPECTED_RUNNER, route.EXPECTED_RUNNER)
        self.assertIsNone(selected.build._owner._listener)
        f.clock.assert_not_called(); f.publish.assert_not_called()
        del args['expected_helper_uid']
        with self.assertRaises(TypeError): operating.assemble_job(**args)

    def test_status_never_turns_historical_route_into_issuer_helper_or_authority(self):
        first = route.selection_status()
        self.assertTrue(first['same_account_helper_selected'])
        for field in ('current_transport_verified', 'source_identity_authenticated',
                      'issuer_location_selected', 'helper_deployment_verified', 'execution_authorized'):
            self.assertFalse(first[field])
        first['source_account'] = 'SYNTHETIC_CHANGED'
        second = operating.configuration_status()['selected']['management_route_expectation']
        self.assertEqual('server01_ansible', second['source_account'])
        self.assertFalse(operating.configuration_status()['execution_authorized'])
        self.assertIsNone(declaration.SITE)


if __name__ == '__main__': unittest.main()
