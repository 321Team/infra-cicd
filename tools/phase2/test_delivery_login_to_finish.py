"""SYNTHETIC offline cross-layer proof against PR135 40355f4.

HTTP sockets, SSH/READY and pipe receive are test boundaries. No native IPC,
host, credential, network, Runtime or operating SITE is exercised/adopted.
Uses existing production supplier, login/actor/issuer parsers, source authority,
reconciler and finish collector; never represents synthetic output as evidence.
"""
import copy
from dataclasses import replace
import json
import subprocess
import unittest
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_crosshost_ingress as ingress
import delivery_fds_operating as operating
import delivery_fds_site_inputs as profile
import delivery_finish_observation as observation
import delivery_job_site as declaration
import delivery_oauth_login as login
import delivery_terminal_login as terminal
import delivery_transport as wire
from delivery_platform_readback import ExpectedSubject
import test_delivery_fds_operating as source_fixture
import test_delivery_token_request as token_fixture
import test_delivery_actor_channel as actor_fixture
import test_delivery_oauth_login as oauth
import test_delivery_transport as http
from test_delivery_job import CONTEXT


class LoginToFinishTests(unittest.TestCase):
    def setUp(self):
        self.f = source_fixture.AdoptedSourceConnectionTests('runTest')
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.t = token_fixture.TokenRequestTests('runTest')
        self.t.setUp(); self.addCleanup(self.t.doCleanups)
        self.base = self.f.f.base
        self.clock = self.f.f.session.now
        self.context = self.t.f.context
        self.context.wrap_socket.side_effect = lambda raw, **kw: raw
        self.addCleanup(patch.stopall)
        patch.object(login.secrets, 'token_hex', return_value=oauth.STATE).start()
        patch.object(subprocess, 'Popen', side_effect=AssertionError('NO_PROCESS_OR_SSH')).start()
        self.sent = []

    def supply(self):
        b = self.base.h.binding
        subject = self.t.claims['sub']
        target = self.t.target
        values = dict(API_ORIGIN=b.api_origin, CLUSTER_UUID=b.cluster_id,
            NAMESPACE_UID=b.namespace_uid, SUBJECT=subject, SUBJECT_UID=target.uid,
            ISSUER=target.issuer, AUDIENCE=target.audience, CA_PEM=http.CA,
            CA_PEM_SHA256=b.ca_sha256, SOURCE_COMMIT=b.source_commit,
            CONTRACT_SHA256=b.contract_sha256, RENDERED_SHA256=b.rendered_sha256,
            ROUTE_HOST=b.route_host, IMAGES=b.images)
        operation_end, cleanup_end = self.base.h.mono+5, self.base.h.mono+10
        tty = Mock(); tty.isatty.return_value = True
        with patch.object(wire.ssl, 'SSLContext', return_value=self.context), \
             patch.object(terminal.sys, 'stdin', tty), \
             patch.object(terminal.getpass, 'getpass', return_value=oauth.PASSWORD):
            provider = terminal.read_login_provider(origin=oauth.ORIGIN, ca_pem=http.CA,
                ca_sha256=core.digest(http.CA), endpoint_ip='192.0.2.20',
                username='synthetic-user',
                deadline=operation_end, monotonic=self.clock)
            self.addCleanup(provider.close)
            with patch.multiple(profile, **values), patch.object(operating, 'ISSUANCE_OPERATOR', 'synthetic-user'):
                supplier = operating.build_login_supplier(self.base.config(),
                    login_provider=provider, endpoint_ip='192.0.2.10',
                    repositories=(self.f.f.repo,),
                    source_tokens=[{'repository':'synthetic-owner/synthetic-repo',
                                    'token':'SYNTHETIC_PRIVATE_SOURCE_TOKEN_0123456789'}],
                    context=CONTEXT, workspace='/synthetic/workspace', port=2222,
                    host_key_alias='synthetic-reviewed-host', remote_python='/synthetic/python3',
                    remote_timeout_seconds=3, operation_deadline=operation_end,
                    cleanup_deadline=cleanup_end, monotonic=self.clock,
                    wall=lambda:self.base.h.now)
        self.addCleanup(supplier.close)
        selected = copy.deepcopy(supplier._ready._selected)
        request = dict(selected, schema=ingress.REQUEST_SCHEMA, nonce='a'*64)
        af = actor_fixture.ActorChannelTests('runTest')
        replies = [oauth.challenge(), oauth.granted(), http.response(core.encoded(af.user())),
                   http.response(core.encoded(self.t.response()), 201),
                   http.response(core.encoded(af.revoked()))]
        sockets = [http.FakeSocket(r) for r in replies]
        def ready():
            self.assertEqual('NOT_ATTEMPTED', supplier.status()['login'])
            self.assertEqual([], self.sent)
            return request
        def forward(req, *, provide_credentials):
            token, sources = provide_credentials(req, operation_end)
            self.sent.append((token, sources))
            self.assertEqual(self.t.token(), token)
            self.assertEqual('synthetic-owner/synthetic-repo', sources[0]['repository'])
            return True
        with patch.object(supplier._ready, 'pull', side_effect=ready), \
             patch.object(supplier._bridge, 'send_from_supplier', side_effect=forward), \
             patch.object(wire.ssl, 'SSLContext', return_value=self.context), \
             patch.object(wire.socket, 'socket', side_effect=sockets) as opened:
            supplier.send()
        self.assertEqual(5, opened.call_count)
        self.assertTrue(all(s.closed for s in sockets))
        methods = [bytes(s.sent).split(b' ', 1)[0] for s in sockets]
        self.assertEqual([b'GET', b'GET', b'GET', b'POST', b'DELETE'], methods)
        self.assertEqual('ACKNOWLEDGED', supplier.status()['actor_server_revocation'])
        self.assertFalse(supplier.status()['job_accepted'])
        self.assertTrue(supplier.status()['password_reference_released'])
        self.receipt = supplier.take_receipt()
        self.assertIsNotNone(self.receipt)
        self.assertIsNone(supplier.take_receipt())
        self.assertIsNotNone(supplier.take_login_metadata())
        self.assertIsNone(supplier.take_login_metadata())
        self.assertEqual(self.sent[0][0], self.receipt.credential.token)
        self.supplier = supplier
        return self.receipt

    def connect(self, receipt, *, wrong_fingerprint=False):
        # Test peer hands over the same parsed in-memory receipt. This boundary
        # is mocked; native Linux/SSH transport is deliberately not claimed.
        base = self.base
        base.credential = base.p.credential = base.p.c.credential = receipt.credential
        base.p.c.providers.credential = receipt.credential
        self.f.f.f.rosa.receive.return_value = receipt.credential
        facts = replace(self.f.plan.issuer.facts,
            credential_fingerprint=('0'*64 if wrong_fingerprint else receipt.credential.fingerprint),
            issuer=receipt.target.issuer, audience=receipt.target.audience,
            issued_at=receipt.issued_at, expires_at=receipt.expires_at,
            requested_seconds=receipt.requested_seconds)
        subject = ExpectedSubject(self.t.claims['sub'], receipt.target.uid, ('system:authenticated',))
        grant = replace(self.f.plan.issuer.grant, principal=subject.username)
        self.plan = replace(self.f.plan, issuer=replace(self.f.plan.issuer, grant=grant, facts=facts),
                            subject=subject)
        # No grants/controls are derived from receipt or transport ACK. The
        # independent SYNTHETIC fixture grants and their original pins remain.
        return self.f.f.f.prepared(lambda *unused:self.plan)

    def run_job(self, plan):
        result = self.f.f.f.call(plan=plan)
        self.f.f.f.closed()
        public = json.dumps(result)
        for secret in (oauth.PASSWORD, actor_fixture.TOKEN, self.sent[0][0]):
            self.assertNotIn(secret, public)
        self.assertIsNone(declaration.SITE)
        return result

    def test_same_issued_credential_reaches_original_control_baseline_and_finish(self):
        receipt = self.supply()
        rc, result = self.run_job(self.connect(receipt))
        self.assertEqual((0, 'SESSION_COMPLETED', 'RELEASED'),
            (rc, result['status'], result['fence']), result)
        self.assertIn('baseline', self.f.f.f.blobs)
        self.assertIn('control', self.f.f.f.blobs)
        self.assertIn('completion-scope', self.f.f.f.blobs)
        self.assertEqual(1, len(self.f.saved))
        doc = json.loads(self.f.saved[0])
        self.assertEqual(6, len(doc['observations']))
        self.assertFalse(doc['execution_authorized'])
        self.assertEqual(1, self.base.p.c.storage.complete_calls)

    def test_ack_and_receipt_cannot_override_changed_control_before_claim(self):
        receipt = self.supply()
        self.base.read_hook = lambda req, pin, original: (
            replace(original, body=b'SYNTHETIC_WITHDRAWN') if pin.path=='control' else original)
        rc, result = self.run_job(self.connect(receipt))
        self.assertEqual(2, rc, result)
        self.assertEqual([], self.base.p.c.storage.claims)
        self.assertEqual([], self.base.h.requests)
        self.assertEqual([], self.f.saved)
        self.assertIs(receipt, self.receipt)
        self.assertEqual('ACKNOWLEDGED', self.supplier.status()['actor_server_revocation'])

    def test_control_withdrawal_after_sender_close_retains_fence_and_receipt(self):
        receipt = self.supply()
        self.base.read_hook = lambda req, pin, original: (
            replace(original, body=b'SYNTHETIC_WITHDRAWN')
            if pin.path=='control' and self.base.p.c.sender_closes else original)
        rc, result = self.run_job(self.connect(receipt))
        self.assertEqual((2, 'RETAINED'), (rc, result['fence']), result)
        self.assertEqual(0, self.base.p.c.storage.complete_calls)
        self.assertEqual([], self.f.saved)
        self.assertIsNotNone(receipt.raw)
        self.assertEqual('ACKNOWLEDGED', self.supplier.status()['actor_server_revocation'])

    def test_wrong_receipt_fingerprint_is_rejected_before_claim(self):
        receipt = self.supply()
        rc, result = self.run_job(self.connect(receipt, wrong_fingerprint=True))
        self.assertEqual(2, rc, result)
        self.assertEqual([], self.base.p.c.storage.claims)
        self.assertEqual([], self.base.h.requests)
        self.assertEqual([], self.f.saved)

    def test_missing_original_does_not_turn_receipt_into_authority(self):
        receipt = self.supply()
        def missing(req, pin, original):
            if pin.path == 'platform':
                raise ValueError('SYNTHETIC_ORIGINAL_UNAVAILABLE')
            return original
        self.base.read_hook = missing
        rc, result = self.run_job(self.connect(receipt))
        self.assertEqual(2, rc, result)
        self.assertEqual([], self.base.p.c.storage.claims)
        self.assertEqual([], self.base.h.requests)
        self.assertEqual([], self.f.saved)
        self.assertIsNotNone(receipt.raw)
        self.assertEqual('ACKNOWLEDGED', self.supplier.status()['actor_server_revocation'])

    def test_finish_persist_failure_retains_fence_after_actual_sender_close(self):
        receipt = self.supply()
        plan = self.connect(receipt)
        def denied(*unused):
            self.assertGreater(self.base.p.c.sender_closes, 0)
            raise OSError('SYNTHETIC_PERSIST_DENIED')
        with patch.object(observation, 'persist', side_effect=denied) as persist:
            rc, result = self.run_job(plan)
        self.assertEqual((2, 'RETAINED'), (rc, result['fence']), result)
        persist.assert_called_once()
        self.assertEqual(0, self.base.p.c.storage.complete_calls)
        self.assertEqual([], self.f.saved)
        self.assertIsNotNone(receipt.raw)


if __name__ == '__main__': unittest.main(verbosity=2)
