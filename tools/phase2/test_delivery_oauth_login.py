"""SYNTHETIC responses only. No HTTP network, login, credential or kubeconfig."""
import base64
import copy
import json
import os
import pickle
import ssl
import unittest
from urllib.parse import urlencode
from unittest.mock import patch

import delivery_adapter as core
import delivery_oauth_login as login
import test_delivery_transport as http
from test_delivery_actor_channel import TOKEN

STATE = 'a'*64
PASSWORD = 'SYNTHETIC_PASSWORD_NOT_REAL'
ORIGIN = 'https://oauth.synthetic.example.com:443'


def challenge(status=401, extra=()):
    return http.response(b'', status, [('WWW-Authenticate','Basic realm="openshift"'), *extra])


def redirect(**changes):
    fields = dict(access_token=TOKEN, token_type='Bearer', expires_in='3600', state=STATE)
    fields.update(changes)
    return ORIGIN+'/oauth/token/implicit#'+urlencode(fields)


def granted(location=None, extra=(), status=302):
    return http.response(b'', status, [('Location',redirect() if location is None else location), *extra])


class LoginTests(unittest.TestCase):
    def setUp(self):
        self.f = http.TransportTests('runTest'); self.f.setUp(); self.mono=10
        self.clock=lambda:self.mono
        self.context = self.f.context
        self.context.wrap_socket.side_effect=lambda raw,**kw:raw
        self.addCleanup(patch.stopall)
        patch.object(login.socket, 'socket', side_effect=AssertionError('NO_NETWORK')).start()
        patch.object(login.secrets, 'token_hex', return_value=STATE).start()

    def client(self, **changes):
        with patch.object(login.ssl, 'SSLContext', return_value=self.context):
            client=login.BasicChallengeLogin(**(dict(origin=ORIGIN, ca_pem=http.CA,
                ca_sha256=core.digest(http.CA), endpoint_ip='192.0.2.20',
                username='synthetic-user', password=PASSWORD, deadline=20,
                monotonic=self.clock)|changes))
        self.addCleanup(client.close);return client

    def run_login(self, client=None, responses=None):
        self.client_value=client or self.client()
        self.sockets=[http.FakeSocket(raw) for raw in (responses or [challenge(),granted()])]
        with patch.object(login.socket,'socket',side_effect=self.sockets) as opened:
            try:return self.client_value.login()
            finally:self.opens=opened.call_count

    def test_challenge_basic_once_exact_ca_sni_pin_and_private_metadata(self):
        c=self.client()
        with patch.dict(os.environ, {'HTTPS_PROXY':'http://synthetic.invalid','SSLKEYLOGFILE':'NEVER'}):
            token=self.run_login(c)
        self.assertEqual(TOKEN,token.token);self.assertEqual(2,self.opens)
        sent=[bytes(s.sent) for s in self.sockets]
        self.assertNotIn(b'Authorization:',sent[0])
        self.assertIn(b'Authorization: Basic '+base64.b64encode(('synthetic-user:'+PASSWORD).encode()),sent[1])
        self.assertEqual(sent[0].split(b'\r\n')[0],sent[1].split(b'\r\n')[0])
        self.assertIn(b'response_type=token',sent[0]);self.assertIn(b'X-CSRF-Token: 1',sent[0])
        self.assertEqual(('192.0.2.20',443),self.sockets[0].connected)
        self.assertTrue(all(s.closed for s in self.sockets))
        self.context.wrap_socket.assert_called_with(self.sockets[1],server_hostname='oauth.synthetic.example.com',do_handshake_on_connect=False)
        self.context.load_verify_locations.assert_called_once_with(cadata=http.CA.decode())
        self.assertEqual('TOKEN_RECEIVED',c.status()['login']);self.assertIsNone(c._password)
        metadata=c.take_metadata();self.assertEqual(3600,metadata['expires_in'])
        self.assertTrue(metadata['oauth_object_name'].startswith('sha256~'))
        self.assertNotEqual(TOKEN,metadata['oauth_object_name']);self.assertIsNone(c.take_metadata())
        for secret in (TOKEN,PASSWORD):self.assertNotIn(secret,repr(c)+json.dumps(c.status())+json.dumps(metadata))
        with self.assertRaisesRegex(login.LoginError,'CONSUMED'):c.login()

    def test_inert_constructor_close_and_no_copy(self):
        c=self.client()
        self.assertEqual('NOT_ATTEMPTED',c.status()['login'])
        for clone in (copy.copy,copy.deepcopy,pickle.dumps):
            with self.assertRaises(Exception):clone(c)
        c.close();self.assertTrue(c.status()['password_reference_released'])
        with self.assertRaises(login.LoginError):c.login()

    def test_configuration_rejects_unsafe_or_inferred_values(self):
        cases=[{'origin':'http://oauth.synthetic.example.com'}, {'origin':ORIGIN+'/oauth'},
               {'origin':'https://name:pass@oauth.synthetic.example.com'}, {'ca_sha256':'0'*64},
               {'endpoint_ip':'oauth.synthetic.example.com'}, {'endpoint_ip':'::ffff:127.0.0.1'},
               {'endpoint_ip':'169.254.169.254'}, {'username':'system:admin'},
               {'password':''}, {'password':'test\nsecret'}, {'deadline':True}]
        for change in cases:
            with self.subTest(fields=list(change)), self.assertRaises(login.LoginError):self.client(**change)

    def test_initial_redirect_or_wrong_challenge_never_sends_password(self):
        cases=[granted(),challenge(status=403),challenge(extra=[('Location',ORIGIN)]),
               http.response(b'',401,[('WWW-Authenticate','Negotiate')]),http.response(status=200)]
        for raw in cases:
            with self.subTest(case=cases.index(raw)),self.assertRaises(login.LoginError):self.run_login(responses=[raw])
            self.assertEqual(1,self.opens);self.assertNotIn(b'Authorization:',self.sockets[0].sent)

    def test_wrong_state_type_lifetime_duplicate_fields_and_redirects_rejected(self):
        cases=[granted(location=redirect(state='b'*64)),granted(location=redirect(token_type='Basic')),
               granted(location=redirect(expires_in='0')),granted(location=redirect(access_token='legacy')),
               granted(location=redirect()+'&access_token=other'),granted(location=redirect()+'&error=denied'),
               granted(location=redirect().replace('oauth.synthetic.example.com','other.invalid')),
               granted(location=redirect().replace('/oauth/token/implicit','/another')),
               granted(location=redirect().replace('#','?error=denied#')),
               granted(location=redirect()+'&scope=%zz'),granted(extra=[('Location',redirect())]),
               challenge(),granted(status=500)]
        for raw in cases:
            with self.subTest(case=cases.index(raw)),self.assertRaises(login.LoginError) as caught:
                self.run_login(responses=[challenge(),raw])
            self.assertEqual(2,self.opens);self.assertTrue(all(s.closed for s in self.sockets))
            self.assertIsNone(caught.exception.__context__)
            self.assertEqual('TOKEN_OUTCOME_UNCONFIRMED',self.client_value.status()['login'])
            self.assertIsNone(self.client_value._password)

    def test_tls_failures_and_cancellation_release_and_do_not_retry(self):
        for err in (ssl.SSLCertVerificationError(PASSWORD),KeyboardInterrupt(TOKEN),SystemExit(PASSWORD)):
            c=self.client();s=http.FakeSocket();s.handshake_error=err
            with patch.object(login.socket,'socket',return_value=s) as opened:
                with self.assertRaises(type(err) if isinstance(err,(KeyboardInterrupt,SystemExit)) else login.LoginError) as caught:c.login()
            self.assertEqual(1,opened.call_count);self.assertTrue(s.closed);self.assertIsNone(c._password)
            self.assertIsNone(caught.exception.__context__)
            self.assertNotIn(PASSWORD,str(caught.exception));self.assertNotIn(TOKEN,str(caught.exception))

    def test_deadline_and_clock_rollback_fail_closed(self):
        c=self.client();self.mono=20
        with self.assertRaises(login.LoginError):c.login()
        self.assertEqual('NOT_ATTEMPTED',c.status()['login'])
        self.mono=10;c=self.client();c._last=11
        with self.assertRaises(login.LoginError):c.login()

    def test_native_tls_does_not_load_ambient_roots_or_keylog(self):
        with patch.object(ssl.SSLContext,'load_verify_locations') as load, \
             patch.dict(os.environ,{'SSLKEYLOGFILE':'NEVER','HTTPS_PROXY':'http://synthetic.invalid'}):
            c=login.BasicChallengeLogin(origin=ORIGIN,ca_pem=http.CA,ca_sha256=core.digest(http.CA),
                endpoint_ip='192.0.2.20',username='synthetic-user',password=PASSWORD,deadline=20,monotonic=self.clock)
        self.addCleanup(c.close)
        self.assertEqual(ssl.CERT_REQUIRED,c._context.verify_mode);self.assertTrue(c._context.check_hostname)
        self.assertIsNone(c._context.keylog_filename);self.assertEqual(0,c._context.cert_store_stats()['x509_ca'])
        load.assert_called_once_with(cadata=http.CA.decode())


if __name__=='__main__':unittest.main()
