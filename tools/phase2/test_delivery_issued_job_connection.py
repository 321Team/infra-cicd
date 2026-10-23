"""SYNTHETIC issued-token -> local helper -> job/source/control connection.

No FDS SITE, SSH binary, DNS/TCP, token endpoint, cluster or deployment is used.
Only the HTTP/source server boundaries and SSH executable are replaced. Native
issuer/parser, bridge/helper, Unix peer, parent, pipes, source session, prepared
plan and authority adapters remain real. This is NOT an operating e2e proof.
"""
import base64
from dataclasses import replace
from datetime import datetime, timezone
import os
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_job as job
import delivery_job_handoff as handoff
import delivery_job_parent as parent
import delivery_job_rendezvous as rendezvous
import delivery_job_site as declaration
import delivery_job_sources as sources
import delivery_runtime_site as runtime
import delivery_source_session as source
import delivery_ssh_bridge as bridge
import delivery_token_request as issuer
from delivery_object_reader import BearerCredential
from delivery_approval_source import ApprovalSourceError
from delivery_private_source import PrivateGitHubOriginalAdapter
from delivery_verifier import Progress, ReadRequest
import test_delivery_job_rendezvous as local
import test_delivery_ssh_bridge as ssh_fixture
from test_delivery_job import CONTEXT, RUNNER
from test_delivery_source_session import TOKEN
from test_delivery_transport import CA


def part(value):
    return base64.urlsafe_b64encode(core.encoded(value)).decode().rstrip('=')


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


@unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'geteuid') and os.geteuid() > 0,
                     'native local helper/peer/pipes require nonroot Linux')
class IssuedJobConnectionTests(unittest.TestCase):
    def setUp(self):
        self.local = local.RendezvousLinuxTests('runTest')
        self.local.setUp()
        self.addCleanup(self.local.doCleanups)
        self.f, self.repo = self.local.f, self.local.s.repo
        self.clock, self.deadline = time.monotonic, time.monotonic() + 6
        self.peer = self.local.peer
        self.events, self.processes, self.errors, self.blobs = [], [], [], []
        self.thread = self.supplier = self.receipt = self.invocation = None
        self.target = issuer.TokenTarget('synthetic-ci', 'deployer', 'synthetic-uid',
                                        'synthetic:issuer', 'synthetic:audience')
        claims = {'iss': self.target.issuer, 'aud': [self.target.audience],
            'sub': 'system:serviceaccount:synthetic-ci:deployer', 'iat': 1000,
            'nbf': 1000, 'exp': 1900, 'kubernetes.io': {'namespace': self.target.namespace,
                'serviceaccount': {'name': self.target.name, 'uid': self.target.uid}}}
        self.token = part({'alg': 'RS256', 'kid': 'SYNTHETIC'}) + '.' + part(claims) + '.' + part('SYNTHETIC')
        self.raw = core.encoded({'apiVersion': 'authentication.k8s.io/v1', 'kind': 'TokenRequest',
            'metadata': {'namespace': self.target.namespace, 'name': self.target.name,
                         'creationTimestamp': stamp(1000)},
            'spec': {'audiences': [self.target.audience], 'expirationSeconds': 900},
            'status': {'token': self.token, 'expirationTimestamp': stamp(1900)}})
        self.client = issuer.TokenRequestIssuer(self.f.h.binding, target=self.target,
            actor=BearerCredential('SYNTHETIC_ACTOR_TOKEN_0123456789'), ca_pem=CA,
            endpoint_ip='192.0.2.10', deadline=self.deadline,
            monotonic=self.clock, wall=lambda: 1001)
        self.addCleanup(self.client.close)
        def exchange(*unused):
            self.events.append('issue')
            return self.raw  # HTTP server boundary; no TLS/issuer authority is claimed.
        self.exchange = Mock(side_effect=exchange)
        self.enterContext(patch.object(self.client, '_exchange', new=self.exchange))
        self.enterContext(patch.object(source, 'pipe_identity', new=handoff._identity))
        self.cfg = replace(ssh_fixture.config(), listener_uid=os.geteuid())
        self.enterContext(patch.object(bridge.subprocess, 'Popen', side_effect=self.start))
        def blob(reader, request, pin):
            self.assertFalse(reader._transport._closed)
            self.blobs.append(pin.path)
            return self.f.reader.blob(request, pin)  # Memory original/control server.
        self.enterContext(patch.object(PrivateGitHubOriginalAdapter, 'blob', new=blob))
        self.owner = rendezvous.UnixJobRendezvous(expected_uid=os.geteuid(),
            peer=self.peer, deadline=self.deadline, monotonic=self.clock,
            publish_ready=self.publish)
        self.addCleanup(self.clean)
        self.platform_hash = self.f.h.binding.platform_receipt_sha256

    def start(self, args, **kwargs):
        self.assertEqual(self.cfg.argv(), args)
        self.assertFalse(kwargs['shell'])
        proc = ssh_fixture.REAL_POPEN([sys.executable, '-I', str(ssh_fixture.HELPER),
                                      str(os.geteuid()), '3'], **kwargs)
        self.processes.append(proc)
        return proc  # Only the reviewed LOCAL Python helper; never /synthetic/ssh.

    def publish(self, request, deadline):
        self.events.append('ready')
        self.supplier = issuer.RequestBoundSupplier(issuer=self.client, request=request,
            source_tokens=[{'repository': self.repo.owner + '/' + self.repo.name, 'token': TOKEN}])
        def supply():
            try:
                transport = bridge.SshCredentialBridge(self.cfg, deadline=deadline, monotonic=self.clock)
                self.assertTrue(self.supplier.send_over_bridge(transport))
                self.events.append('delivered')
            except BaseException as error:
                self.errors.append(type(error).__name__)  # Never secret/callback text.
            finally:
                self.receipt = self.supplier.take_receipt()
        self.thread = threading.Thread(target=supply)
        self.thread.start()  # Publisher must not wait for receiver/ACK here.

    def clean(self):
        if self.thread:
            self.thread.join(8)
        for proc in self.processes:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=2)
        if self.thread:
            self.thread.join(2)
        if self.invocation:
            self.invocation.close()
        self.owner.close()
        if self.supplier:
            self.supplier.close()
        self.assertFalse(self.thread and self.thread.is_alive())

    def connect(self, *, wrong_fingerprint=False):
        wire = sources.SourceRoleWiring(binding=self.f.h.binding,
            routes=tuple(sources.RoleRoute(role, self.repo, self.repo) for role in sources.ROLES),
            wall=lambda: 1001, monotonic=self.clock)
        def materialize(context, credential, session, roles, deadline):
            self.events.append('materialize')
            self.assertEqual(CONTEXT, context)
            self.assertEqual('CONSUMED', session._state)
            self.assertEqual(self.receipt.credential.fingerprint, credential.fingerprint)
            self.assertEqual(self.token, credential.token)
            self.assertEqual(session.deadline, deadline)
            self.assertEqual(4, len(roles['readers']))
            p = self.f.plan()
            facts = replace(p.issuer.facts,
                credential_fingerprint='f'*64 if wrong_fingerprint else credential.fingerprint)
            principal = 'system:serviceaccount:synthetic-ci:deployer'
            return replace(p, issuer=replace(p.issuer, facts=facts,
                grant=replace(p.issuer.grant, principal=principal)),
                subject=replace(p.subject, username=principal))
        prepared = runtime.PreparedAuthorityPlan(binding=self.f.h.binding, materialize=materialize)
        self.addCleanup(prepared.close)
        routes = (self.local.routes[0],)
        routes = (replace(routes[0], local_use_until=self.deadline + 10),)
        builder = parent.ParentJobBuilder(config=self.f.config(), plan=prepared,
            finish_slot=self.f.slot, expected_peer=self.peer, routes=routes,
            deadline=self.deadline, wire=wire, monotonic=self.clock,
            publish_ready=self.owner.publish_ready, open_channel=self.owner.open_channel)
        wrapped = rendezvous.OwnedRendezvousBuilder(builder, self.owner)
        native = job.JobSite(RUNNER, wrapped)
        self.invocation = native.build(CONTEXT, 'deploy')
        self.invocation.check(CONTEXT, 'deploy')
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive()); self.assertEqual([], self.errors)
        self.exchange.assert_called_once()
        self.assertEqual(['ready', 'issue', 'delivered'], self.events)
        self.assertEqual(self.raw, self.receipt.raw)
        self.assertEqual(0, self.processes[0].returncode)
        self.assertIsNone(self.supplier.take_receipt())
        self.assertIsNone(self.client._token)
        session, receiver = self.invocation.source_session, self.invocation.receiver
        deadline = session.prepare(receiver, self.invocation.handoff_deadline)
        credential = receiver.receive(deadline)
        roles = session.open(credential)
        plan = prepared.resolve(credential=credential, source=session, roles=roles, deadline=deadline)
        wire.check_plan(plan)
        self.assertEqual(self.platform_hash, plan.approval.binding.platform_receipt_sha256)
        self.assertNotEqual(self.platform_hash, core.digest(self.raw))
        factory = runtime.RuntimeAuthorityFactory(plan, roles['readers'], self.f.slot,
                                                 wall=lambda: 1001, monotonic=self.clock)
        progress = Progress()
        bundle = factory(self.f.h.binding, credential, lambda: progress)
        self.addCleanup(bundle.close)
        return bundle, ReadRequest(self.f.h.binding, credential, progress, deadline)

    def test_issued_fingerprint_crosses_native_job_and_original_control_without_backfill(self):
        bundle, request = self.connect()
        decision = bundle.adapters.approval(request)
        self.assertEqual('ACTIVE', decision.state)
        self.assertEqual(['approval', 'control', 'approval', 'control'], self.blobs)
        self.assertEqual(['ready', 'issue', 'delivered', 'materialize'], self.events)
        self.assertEqual([], self.f.h.requests)  # No mock deployment writes either.
        self.assertEqual([], self.f.p.c.storage.claims)
        self.assertTrue(self.invocation.close())
        self.assertIsNone(self.invocation.receiver._fd)
        self.assertEqual('CLOSED', self.invocation.source_session._state)
        self.assertIsNone(declaration.SITE)

    def test_delivered_credential_cannot_adopt_foreign_plan_fingerprint(self):
        with self.assertRaisesRegex(runtime.RuntimeSiteError, 'MATERIALIZATION_FAILED'):
            self.connect(wrong_fingerprint=True)
        self.assertIn('delivered', self.events)
        self.assertEqual([], self.blobs)  # Fingerprint rejected before original/control reads.
        self.assertEqual([], self.f.h.requests)
        self.assertEqual([], self.f.p.c.storage.claims)
        self.assertIsNotNone(self.receipt)  # Real SYNTHETIC receipt retained for caller cleanup.

    def test_delivered_then_second_control_drift_still_denies_before_claim(self):
        bundle, request = self.connect()
        count = 0
        def drift(req, pin, original):
            nonlocal count
            if pin.path == 'control':
                count += 1
                if count == 2:
                    return replace(original, body=b'SYNTHETIC_WITHDRAWN_CONTROL')
            return original
        self.f.read_hook = drift
        with self.assertRaisesRegex(ApprovalSourceError, 'APPROVAL_SOURCE_UNVERIFIED'):
            bundle.adapters.approval(request)
        self.assertEqual(2, count)
        self.assertIn('delivered', self.events)
        self.assertEqual([], self.f.h.requests)
        self.assertEqual([], self.f.p.c.storage.claims)


if __name__ == '__main__':
    unittest.main()
