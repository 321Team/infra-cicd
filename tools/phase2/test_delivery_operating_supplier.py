"""SYNTHETIC auth/issuance/revocation responses; no network or SSH."""
import copy
from dataclasses import replace
import pickle
import unittest
from unittest.mock import patch

import delivery_operating_supplier as supply
import delivery_ready_pull as ready
import delivery_ssh_bridge as ssh
import delivery_transport as wire
import delivery_adapter as core
import test_delivery_actor_channel as fixture
import test_delivery_transport as http
from test_delivery_ready_pull import selected
from test_delivery_ssh_bridge import config


class MemorySupplyTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.ActorChannelTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.request=self.f.f.request();self.actor=self.f.client()
        base=config()
        self.bridge=ssh.SshCredentialBridge(replace(base,remote_helper='/synthetic/delivery_ssh_bridge_helper.py'),deadline=20,monotonic=self.f.clock)
        self.pull=ready.SshReadyPull(replace(base,remote_helper='/synthetic/delivery_ready_pull_helper.py'),selected(self.request),deadline=20,monotonic=self.f.clock)
        self.sources=[{'repository':'synthetic/evidence','token':'SYNTHETIC_SOURCE_TOKEN_12345'}]

    def make(self,**kw):
        value=supply.MemoryOperatingSupplier(**(dict(ready_pull=self.pull,bridge=self.bridge,actor_channel=self.actor,source_tokens=self.sources)|kw))
        self.addCleanup(value.close);return value

    def test_constructor_inert_copy_and_no_serialization(self):
        with patch.object(ready.SshReadyPull,'pull') as pull,patch.object(wire.socket,'socket') as sock:
            value=self.make()
        pull.assert_not_called();sock.assert_not_called()
        self.sources[0]['token']='CALLER_CHANGED'
        self.assertNotEqual(self.sources,value._sources)
        for clone in (copy.copy,copy.deepcopy,pickle.dumps):
            with self.assertRaises(Exception):clone(value)
        value.close()
        self.assertEqual('NOT_ATTEMPTED',value.status()['actor_server_revocation'])
        self.assertTrue(value.status()['local_references_released'])
        with self.assertRaisesRegex(supply.SupplyError,'CONSUMED'):value.send()

    def test_mismatched_trust_clock_binding_and_sources_rejected_without_consuming_actor(self):
        bad=ready.SshReadyPull(self.bridge._config, self.pull._selected,
                              deadline=20, monotonic=self.f.clock)
        with self.assertRaises(supply.SupplyError):self.make(ready_pull=bad)
        for field,new in [('port',2222),('host_key_alias','different'),('remote_python','/other/python')]:
            b=ssh.SshCredentialBridge(replace(self.bridge._config,**{field:new}),deadline=20,monotonic=self.f.clock)
            with self.subTest(field=field),self.assertRaises(supply.SupplyError):self.make(bridge=b)
        bad=ssh.SshCredentialBridge(self.bridge._config,deadline=21,monotonic=self.f.clock)
        with self.assertRaises(supply.SupplyError):self.make(bridge=bad)
        bad=ssh.SshCredentialBridge(self.bridge._config,deadline=20,monotonic=lambda:10)
        with self.assertRaises(supply.SupplyError):self.make(bridge=bad)
        for refs in ([],[{'repository':'synthetic/other','token':self.sources[0]['token']}],self.sources*2):
            with self.assertRaises(supply.SupplyError):self.make(source_tokens=refs)
        self.assertFalse(self.actor._closed)

    def test_ready_failure_mismatch_or_cancellation_never_authenticates_or_issues(self):
        for error in (ValueError(fixture.TOKEN),KeyboardInterrupt(fixture.TOKEN),SystemExit(fixture.TOKEN)):
            self.actor=self.f.client();value=self.make()
            with patch.object(self.pull,'pull',side_effect=error),patch.object(wire.socket,'socket') as sock:
                with self.assertRaises(type(error) if isinstance(error,(KeyboardInterrupt,SystemExit)) else supply.SupplyError) as caught:value.send()
            sock.assert_not_called();self.assertIsNone(caught.exception.__context__)
            self.assertNotIn(fixture.TOKEN,str(caught.exception));self.assertIsNone(value.take_receipt())
            self.assertEqual('NOT_ATTEMPTED',value.status()['actor_server_revocation'])
            self.assertTrue(value.status()['local_references_released'])
        self.actor=self.f.client();value=self.make()
        other=copy.deepcopy(self.request);other['target']['binding_key']='f'*64
        with patch.object(self.pull,'pull',return_value=other),patch.object(wire.socket,'socket') as sock:
            with self.assertRaises(supply.SupplyError):value.send()
        sock.assert_not_called()

    def execute(self,forward_error=None,revoke_status=200):
        value=self.make();responses=[(self.f.user(),200),(self.f.f.response(),201),(self.f.revoked(),revoke_status)]
        sockets=[http.FakeSocket(http.response(core.encoded(obj),status)) for obj,status in responses]
        self.f.f.f.context.wrap_socket.side_effect=lambda raw,**kw:raw
        def forward(request,*,provide_credentials):
            self.assertTrue(value.status()['public_ready_received'])
            token,sources=provide_credentials(request,20)
            self.assertEqual(self.f.f.token(),token);self.assertEqual(self.sources,sources)
            if forward_error:raise forward_error
            return True
        with patch.object(self.pull,'pull',return_value=self.request), \
             patch.object(self.bridge,'send_from_supplier',side_effect=forward), \
             patch.object(wire.ssl,'SSLContext',return_value=self.f.f.f.context), \
             patch.object(wire.socket,'socket',side_effect=sockets) as opened, \
             patch.object(ssh.subprocess,'Popen',side_effect=AssertionError('NO_SSH')):
            if forward_error or revoke_status!=200:
                expected=type(forward_error) if isinstance(forward_error,(KeyboardInterrupt,SystemExit)) else supply.SupplyError
                with self.assertRaises(expected) as caught:value.send()
                self.assertIsNone(caught.exception.__context__)
                self.assertNotIn(fixture.TOKEN,str(caught.exception))
            else:value.send()
        self.assertEqual(3,opened.call_count);self.assertTrue(all(s.closed for s in sockets))
        self.assertIn(b'GET /apis/user.openshift.io/v1/users/~',bytes(sockets[0].sent))
        self.assertIn(b'POST /api/v1/namespaces/',bytes(sockets[1].sent))
        self.assertIn(b'DELETE /apis/oauth.openshift.io/v1/oauthaccesstokens/',bytes(sockets[2].sent))
        self.assertIsNotNone(value.take_receipt());self.assertIsNone(value.take_receipt())
        self.assertIsNone(value._sources);self.assertIsNone(value._actor)
        return value.status()

    def test_public_ready_then_auth_tokenrequest_forward_revoke_receipt(self):
        status=self.execute()
        self.assertEqual('ACKNOWLEDGED',status['actor_server_revocation'])
        self.assertTrue(status['credential_forwarded']);self.assertFalse(status['job_accepted'])

    def test_post_issue_failure_cancel_and_revoke_uncertainty_preserve_receipt(self):
        for error in (ValueError(fixture.TOKEN),KeyboardInterrupt(fixture.TOKEN),SystemExit(fixture.TOKEN)):
            self.actor=self.f.client()
            status=self.execute(forward_error=error,revoke_status=500)
            self.assertFalse(status['credential_forwarded'])
            self.assertEqual('UNCONFIRMED',status['actor_server_revocation'])
            self.assertTrue(status['local_references_released'])


if __name__=='__main__':unittest.main()
