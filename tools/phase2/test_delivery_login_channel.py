"""SYNTHETIC full login/actor/issuance wire fixtures; SSH and network denied."""
import copy
import unittest
from unittest.mock import patch

import delivery_adapter as core
import delivery_login_channel as deferred
import delivery_oauth_login as login
import delivery_operating_supplier as supply
import delivery_transport as wire
import delivery_ssh_bridge as ssh
import test_delivery_transport as http
import test_delivery_operating_supplier as existing
import test_delivery_oauth_login as oauth
from test_delivery_actor_channel import TOKEN


class DeferredTests(unittest.TestCase):
    def setUp(self):
        self.f=existing.MemorySupplyTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.context=self.f.f.f.f.context
        self.context.wrap_socket.side_effect=lambda raw,**kw:raw
        self.addCleanup(patch.stopall)
        patch.object(login.secrets,'token_hex',return_value=oauth.STATE).start()
        patch.object(wire.socket,'socket',side_effect=AssertionError('NO_NETWORK')).start()
        patch.object(ssh.subprocess,'Popen',side_effect=AssertionError('NO_SSH')).start()

    def channel(self, **changes):
        with patch.object(wire.ssl,'SSLContext',return_value=self.context):
            provider=login.BasicChallengeLogin(origin=oauth.ORIGIN,ca_pem=http.CA,
                ca_sha256=core.digest(http.CA),endpoint_ip='192.0.2.20',username='synthetic-user',
                password=oauth.PASSWORD,deadline=20,monotonic=self.f.f.clock)
            self.addCleanup(provider.close)
            args={k:v for k,v in self.f.f.args.items() if k!='actor'}
            c=deferred.LoginActorChannel(self.f.actor._binding,login_provider=provider,**(args|changes))
        self.addCleanup(c.close);return c

    def supplier(self,c):
        s=supply.MemoryOperatingSupplier(ready_pull=self.f.pull,bridge=self.f.bridge,
                                        actor_channel=c,source_tokens=self.f.sources)
        self.addCleanup(s.close);return s

    def test_ready_failure_never_logs_in_or_creates_actor(self):
        c=self.channel();s=self.supplier(c)
        with patch.object(self.f.pull,'pull',side_effect=ValueError(TOKEN)):
            with self.assertRaises(supply.SupplyError):s.send()
        self.assertEqual('NOT_ATTEMPTED',s.status()['login'])
        self.assertEqual('NOT_ATTEMPTED',s.status()['actor_server_revocation'])
        self.assertTrue(s.status()['password_reference_released'])
        self.assertIsNone(s.take_login_metadata());self.assertIsNone(s.take_receipt())

    def execute(self, auth_ok=True, forward_error=None, login_error=False):
        c=self.channel();s=self.supplier(c)
        replies=[oauth.challenge(),oauth.challenge() if login_error else oauth.granted()]
        user=self.f.f.user()
        if not auth_ok:user['metadata']['name']='other-user'
        if not login_error:
            replies.append(http.response(core.encoded(user)))
            if auth_ok:
                replies.extend([http.response(core.encoded(self.f.f.f.response()),201),
                                http.response(core.encoded(self.f.f.revoked()))])
        sockets=[http.FakeSocket(r) for r in replies]
        def ready():
            self.assertEqual('NOT_ATTEMPTED',c.status()['login'])
            return self.f.request
        def forward(request,*,provide_credentials):
            self.assertTrue(s.status()['public_ready_received'])
            token,sources=provide_credentials(request,20)
            self.assertEqual(self.f.f.f.token(),token)
            self.assertEqual(self.f.sources,sources)
            if forward_error:raise forward_error
            return True
        with patch.object(self.f.pull,'pull',side_effect=ready) as pulled, \
             patch.object(self.f.bridge,'send_from_supplier',side_effect=forward), \
             patch.object(wire.ssl,'SSLContext',return_value=self.context), \
             patch.object(wire.socket,'socket',side_effect=sockets) as opened:
            if login_error or not auth_ok or forward_error:
                with self.assertRaises(type(forward_error) if isinstance(forward_error,(KeyboardInterrupt,SystemExit)) else supply.SupplyError) as caught:s.send()
                self.assertIsNone(caught.exception.__context__)
                self.assertNotIn(TOKEN,str(caught.exception));self.assertNotIn(oauth.PASSWORD,str(caught.exception))
            else:s.send()
        self.assertEqual(1,pulled.call_count);self.assertEqual(len(replies),opened.call_count)
        self.assertTrue(all(x.closed for x in sockets))
        self.assertTrue(s.status()['password_reference_released'])
        self.assertTrue(s.status()['local_references_released'])
        return s,sockets

    def test_full_ready_login_identity_issue_bridge_revoke_and_two_handoffs(self):
        s,sockets=self.execute()
        paths=[bytes(x.sent).split(b'\r\n',1)[0] for x in sockets]
        self.assertIn(b'/oauth/authorize?',paths[0]);self.assertEqual(paths[0],paths[1])
        self.assertIn(b'users/~',paths[2]);self.assertTrue(paths[3].startswith(b'POST '))
        self.assertTrue(paths[4].startswith(b'DELETE '))
        self.assertEqual('ACKNOWLEDGED',s.status()['actor_server_revocation'])
        self.assertFalse(s.status()['job_accepted']);self.assertTrue(s.status()['credential_forwarded'])
        metadata=s.take_login_metadata();self.assertEqual(3600,metadata['expires_in'])
        self.assertEqual(self.f.f.revoked()['metadata']['name'],metadata['oauth_object_name'])
        self.assertIsNone(s.take_login_metadata())
        self.assertIsNotNone(s.take_receipt());self.assertIsNone(s.take_receipt())

    def test_failed_login_or_identity_never_issues_or_deletes_other_identity(self):
        for args in ({'login_error':True},{'auth_ok':False}):
            s,_=self.execute(**args)
            self.assertFalse(s.status()['issuance_attempted'])
            self.assertEqual('NOT_ATTEMPTED',s.status()['actor_server_revocation'])
            if not args.get('login_error'):self.assertIsNotNone(s.take_login_metadata())

    def test_forward_failure_and_cancellation_preserve_private_original(self):
        for exc in (ValueError(TOKEN),KeyboardInterrupt(oauth.PASSWORD)):
            s,_=self.execute(forward_error=exc)
            self.assertEqual('ACKNOWLEDGED',s.status()['actor_server_revocation'])
            self.assertTrue(s.status()['issuance_attempted'])
            self.assertIsNotNone(s.take_receipt());self.assertIsNotNone(s.take_login_metadata())

    def test_constructor_and_sources_reject_before_any_login(self):
        for change in ({'expected_username':'other-user'}, {'operation_deadline':19},
                       {'cleanup_deadline':20}, {'endpoint_ip':'127.0.0.1'},
                       {'monotonic':lambda:10}, {'ca_pem':http.CA+b'changed'}):
            with self.assertRaises(Exception):self.channel(**change)
        c=self.channel()
        for values in ([], self.f.sources*2,[{'repository':'synthetic/other','token':TOKEN}],
                       [{'repository':'synthetic/evidence','token':'bad\n'}]):
            with self.assertRaises(Exception):c.check_sources(values,['synthetic/evidence'])
        self.assertEqual('NOT_ATTEMPTED',c.status()['login'])

    def test_each_prelogin_boundary_rejects_without_minting_actor(self):
        other = copy.deepcopy(self.f.request)
        other['target']['binding_key'] = 'f' * 64
        cases = (
            ('clock', ssh.SshCredentialBridge(self.f.bridge._config,
                deadline=20, monotonic=lambda:10), self.f.request, self.f.sources),
            ('deadline', ssh.SshCredentialBridge(self.f.bridge._config,
                deadline=21, monotonic=self.f.f.clock), self.f.request, self.f.sources),
            ('binding', self.f.bridge, other, self.f.sources),
            ('sources', self.f.bridge, self.f.request, self.f.sources * 2),
        )
        for name, bridge, request, tokens in cases:
            c = self.channel()
            with self.subTest(boundary=name), patch.object(c._provider, 'login') as mint:
                with self.assertRaises(Exception):
                    c.send_over_bridge(bridge=bridge, request=request, source_tokens=tokens)
                mint.assert_not_called()
            self.assertEqual('NOT_ATTEMPTED', c.status()['login'])
            self.assertIsNone(c.take_receipt())


if __name__=='__main__':unittest.main()
