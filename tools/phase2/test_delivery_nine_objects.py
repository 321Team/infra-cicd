"""고정 9객체의 합성 오프라인 시험. 현장 값·인증·Runtime 증적이 아니다."""
import copy
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import delivery_adapter as core
import delivery_intake as intake
import delivery_objects as objects
import delivery_recovery as recovery
import delivery_store as store
from delivery_verifier import Progress, VerificationError
from test_delivery_adapter import Harness, validator
from test_delivery_operational import ContractHarness
from test_delivery_objects import owned
from test_delivery_intake import fixture as intake_fixture
import test_delivery_platform_readback as platform_tests
import test_delivery_evidence_source as evidence_tests
import test_delivery_object_reader as reader_tests
import test_producer_bridge as bridge_tests
from test_delivery_transport import response


def nine(h):
    """Only synthetic example domains/digests. Never an operational input."""
    image = ('end-page', 'registry.example.com/fds/end-page@sha256:' + '9' * 64)
    h.rebind(images=h.binding.images + (image,))
    docs = json.loads(h.render)
    for api, kind, name in core.END_PAGE_OBJECTS:
        obj = {'apiVersion': api, 'kind': kind,
               'metadata': {'namespace': 'fds-app', 'name': name}, 'spec': {}}
        if kind == 'Service':
            obj['spec'] = {'ports': [{'name': 'http', 'port': 8080}], 'selector': {'app': name}}
        elif kind == 'Deployment':
            obj['spec'] = {'replicas': 3, 'template': {'metadata': {'labels': {'app': name}},
                'spec': {'serviceAccountName': name + '-sa', 'automountServiceAccountToken': False,
                    'containers': [{'name': name, 'image': image[1]}],
                    'volumes': [{'name': 'tmp', 'emptyDir': {}},
                                {'name': 'meta', 'downwardAPI': {'items': []}}]}}}
        else:
            obj['spec'] = {'host': h.binding.route_host, 'path': '/end', 'tls': dict(core.TLS),
                'to': {'kind': 'Service', 'name': name, 'weight': 100},
                'port': {'targetPort': 'http'}, 'wildcardPolicy': 'None'}
        docs.append(obj)
    h.render = core.encoded(docs)
    h.rebind(rendered_sha256=core.digest(h.render))
    return h


class NineObjectTests(unittest.TestCase):
    def setUp(self):
        for name in ('socket', 'getaddrinfo', 'create_connection'):
            guard = patch.object(socket, name, side_effect=AssertionError('NETWORK_FORBIDDEN'))
            guard.start(); self.addCleanup(guard.stop)

    def test_legacy_encoding_and_default_receipt_remain_exact(self):
        h = Harness(); before = asdict(h.binding)
        expected = core.digest(json.dumps(before, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())
        self.assertEqual(expected, h.binding.key)
        self.assertEqual(core.OBJECTS, core.objects_for(h.binding))
        receipt = h.run()
        self.assertEqual(receipt, store.receipt_checked(receipt, h.binding.key))
        self.assertEqual(receipt, store.receipt_checked(receipt, h.binding.key, binding=h.binding))
        self.assertEqual(h.binding, recovery.binding_from_bytes(core.encoded(before)))

    def test_explicit_third_image_selects_nine_without_new_binding_fields(self):
        h = nine(Harness()); core.binding_checked(h.binding)
        self.assertEqual(9, len(core.objects_for(h.binding)))
        self.assertEqual(core.OBJECTS, core.objects_for(h.binding)[:6])
        self.assertEqual(h.binding, recovery.binding_from_bytes(core.encoded(asdict(h.binding))))
        for key in core.END_PAGE_OBJECTS:
            with self.assertRaises(core.Stop): core.path_for(key)
            self.assertIn('/namespaces/fds-app/', core.path_for(key, h.binding))

    def test_wrong_order_fourth_duplicate_or_missing_image_denied_before_write(self):
        b = nine(Harness()).binding
        cases = (b.images[::-1], b.images + (b.images[-1],), b.images[:2] + (b.images[0],), b.images[:1])
        for images in cases:
            h = nine(Harness()); h.rebind(images=images)
            self.assertEqual('IMAGES_INVALID', h.run()['error_code']); self.assertFalse(h.requests)

    def test_nine_writes_three_rollouts_checkpoint_and_pending_shape(self):
        h = nine(Harness()); r = h.run()
        self.assertEqual('ROLLOUT_OBSERVED', r['status'], r)
        self.assertEqual(['PATCH'] * 9 + ['GET'] * 3, [q.method for q in h.requests])
        self.assertEqual(list(core.services_for(h.binding)), r['rollout'])
        self.assertEqual(9, sum(c['status'] == 'WRITE_PENDING' for c in h.checkpoints))
        self.assertEqual('NOT_VERIFIED', r['delivery_gate'])
        for receipt in h.checkpoints: store.receipt_checked(receipt, h.binding.key, binding=h.binding)
        with self.assertRaises(store.StoreError): store.receipt_checked(r, h.binding.key)

    def test_two_image_binding_cannot_infer_third_from_render(self):
        h = nine(Harness()); h.rebind(images=h.binding.images[:2])
        self.assertEqual('OBJECT_SET_INVALID', h.run()['error_code']); self.assertFalse(h.requests)

    def test_third_image_requires_all_nine_objects_no_extra_namespace_or_kind(self):
        changes = (lambda d: d.pop(), lambda d: d.append(copy.deepcopy(d[6])),
                   lambda d: d[6]['metadata'].update(namespace='rosa-app'),
                   lambda d: d[6].update(kind='Secret'), lambda d: d[6].update(kind='NetworkPolicy'))
        for change in changes:
            h = nine(Harness()); h.change_render(change)
            self.assertEqual('STOPPED', h.run()['status']); self.assertFalse(h.requests)

    def test_end_route_exact_path_backend_port_tls(self):
        for change in ({'path': '/'}, {'host': 'other.example.com'}, {'to': {'name': 'transaction-api'}},
                       {'port': {'targetPort': 'db'}}, {'wildcardPolicy': 'Subdomain'}, {'tls': {}}):
            h = nine(Harness()); h.change_render(lambda d: d[-1]['spec'].update(change))
            self.assertEqual('ROUTE_CONTRACT_MISMATCH', h.run()['error_code']); self.assertFalse(h.requests)

    def test_end_page_no_db_environment_or_credential_volume(self):
        changes = (lambda p: p['containers'][0].update(env=[{'name': 'DB_URL', 'value': 'SYNTHETIC'}]),
                   lambda p: p['containers'][0].update(envFrom=[{'secretRef': {'name': 'db'}}]),
                   lambda p: p.update(volumes=[{'name': 'db', 'secret': {'secretName': 'db'}}]),
                   lambda p: p.update(volumes=[{'name': 'x', 'hostPath': {'path': '/tmp'}}]),
                   lambda p: p.update(volumes=[{'name': 'x', 'projected': {'sources': []}}]))
        for change in changes:
            h = nine(Harness()); h.change_render(lambda d: change(d[7]['spec']['template']['spec']))
            r = h.run(); self.assertEqual('END_PAGE_INPUT_FORBIDDEN', r['error_code'])
            self.assertIn(r['error_code'], store.ERRORS); self.assertFalse(h.requests)

    def test_new_object_uncertain_write_retains_exact_pending_no_retry(self):
        for index in (7, 8, 9):
            c = ContractHarness(); nine(c.h); c.h.fail_at = index
            r = c.run(); self.assertNotEqual('SESSION_COMPLETED', r['status'])
            self.assertEqual(index, len(c.h.requests)); self.assertEqual(0, c.storage.complete_calls)
            d = recovery.decision(c.h.binding, c.storage.journals[0].records, active=True, complete=None)
            self.assertEqual('WRITE_OUTCOME_UNCONFIRMED', d['state'])
            self.assertEqual('/'.join(core.objects_for(c.h.binding)[index-1]), d['latest_receipt']['pending'])
            self.assertFalse(d['automatic_resume_allowed'])

    def test_new_object_known_reject_clears_pending_without_retry(self):
        h = nine(Harness()); h.reject_at = 8; r = h.run()
        self.assertEqual('API_REJECTED', r['error_code']); self.assertIsNone(r['pending'])
        self.assertEqual(7, len(r['acknowledged'])); self.assertEqual(8, len(h.requests))

    def test_session_finish_and_recovery_validate_nine_context(self):
        c = ContractHarness(); nine(c.h); r = c.run()
        self.assertEqual('SESSION_COMPLETED', r['status'], r)
        self.assertEqual(1, c.storage.complete_calls); self.assertIsNone(c.storage.active)
        records = c.storage.journals[0].records
        receipt, digest = recovery.replay(records, c.h.binding.key, binding=c.h.binding)
        self.assertEqual(9, len(receipt['acknowledged']))
        with self.assertRaises(recovery.RecoveryError): recovery.replay(records, c.h.binding.key)
        other = replace(c.h.binding, images=c.h.binding.images[:2])
        with self.assertRaises(recovery.RecoveryError): recovery.replay(records, c.h.binding.key, binding=other)
        marker = core.encoded({'schema': 'P2-CICD-complete-1', 'binding_key': c.h.binding.key,
                              'last_checkpoint_sha256': digest})
        d = recovery.decision(c.h.binding, records, active=False, complete=marker)
        self.assertEqual('COMPLETED_RECORD', d['state']); self.assertEqual('NOT_VERIFIED', d['runtime_state'])

    def test_rollout_shared_budget_stops_third_without_extending_credential(self):
        h = nine(Harness()); elapsed = {}
        def readiness(q, obj):
            if q.method != 'GET': return
            name = obj['metadata']['name']; start = elapsed.setdefault(name, h.mono)
            # Each of the first two takes 298s; third gets only the remainder.
            obj['status']['readyReplicas'] = 3 if h.mono - start >= 298 else 0
        h.after_send = readiness; r = h.run()
        self.assertEqual('ROLLOUT_TIMEOUT', r['error_code'], r)
        self.assertEqual(['transaction-api', 'fds-engine'], r['rollout'])
        self.assertEqual(1600, h.mono); self.assertEqual(1900, h.lease.token_expires_at)

    def test_reconciler_checks_all_nine_prefixes_and_final_uid(self):
        h = nine(Harness()); keys = core.objects_for(h.binding)
        raws = tuple(core.encoded(owned(o, i)) for i, o in enumerate(json.loads(h.render)))
        reconciler = objects.ObjectReconciler(h.binding, h.render, h.contract, validator, (None,) * 9)
        for count in range(10):
            receipt = {'schema': 'P2-CICD-apply-core-1', 'status': 'WRITE_ACKNOWLEDGED' if count else 'PREPARED',
                'binding_key': h.binding.key, 'acknowledged': ['/'.join(k) for k in keys[:count]],
                'pending': None, 'rollout': [], 'error_code': None, 'delivery_gate': 'NOT_VERIFIED',
                'health_e2e': 'NOT_VERIFIED', 'pod_image_id': 'NOT_VERIFIED'}
            p = Progress(core.encoded(receipt), raws[:count])
            self.assertEqual(p.key, reconciler.check(h.binding, p, raws[:count] + (None,) * (9-count)))
        changed = list(raws); obj = json.loads(changed[-1]); obj['metadata']['uid'] = 'replaced'
        changed[-1] = core.encoded(obj)
        with self.assertRaises(VerificationError): reconciler.check(h.binding, p, tuple(changed))

    def test_intake_three_workloads_is_diagnostic_only_and_unknown_fourth_denied(self):
        data = intake_fixture(); end = copy.deepcopy(data['workloads']['fds-engine'])
        end.update(deployment='end-page', container='end-page')
        data['workloads']['end-page'] = end
        report = intake.evaluate(data)
        self.assertEqual('DECLARED_INPUTS_CONSISTENT', report['status'], report)
        self.assertFalse(report['execution_authorized']); self.assertFalse(report['external_references_verified'])
        data['workloads']['other'] = copy.deepcopy(end)
        self.assertEqual('INCOMPLETE_OR_INCONSISTENT', intake.evaluate(data)['status'])

    @unittest.skipUnless(bridge_tests.YAML_READY, 'Optional PyYAML 6.0.3')
    def test_committed_bridge_strict_parser_nine_and_legacy_rejects_nine(self):
        import producer_bridge as bridge
        h = bridge_tests.BridgeTests(); h.setUp(); self.addCleanup(h.doCleanups)
        source = nine(Harness())
        render = bridge_tests.yaml.safe_dump_all(json.loads(source.render), sort_keys=False).encode()
        h.binding = replace(h.binding, images=source.binding.images)
        h.rebind(render=render)
        self.assertEqual(9, len(h.call()[1]))
        self.assertEqual(9, len(core.prepare(h.binding, render, h.contract,
                             bridge.CommittedProducerBridge(h.root, h.binding))))
        legacy = replace(h.binding, images=h.binding.images[:2])
        with self.assertRaises(bridge.BridgeError): h.call(binding=legacy)
        with self.assertRaises(bridge.BridgeError):
            bridge.strict_documents(render + b'---\nkind: Service\n', bridge_tests.yaml)

    @unittest.skipUnless(os.name == 'posix', 'POSIX durable store only')
    def test_actual_durable_store_and_read_only_recovery_nine(self):
        c = ContractHarness(); nine(c.h)
        with tempfile.TemporaryDirectory(prefix='p2-nine-', dir=Path.cwd()) as parent:
            os.chmod(parent, 0o700); root = parent + '/journal'
            os.mkdir(root, 0o700)
            r = c.run(root=root, acquire=store.acquire, journal_factory=store.Journal)
            self.assertEqual('SESSION_COMPLETED', r['status'], r)
            d = recovery.inspect(root, c.h.binding)
            self.assertEqual('COMPLETED_RECORD', d['state']); self.assertEqual(9, len(d['latest_receipt']['acknowledged']))


class NineReadbackTests(unittest.TestCase):
    def platform_helper(self):
        h = platform_tests.PlatformReaderTests(); h.setUp(); self.addCleanup(h.doCleanups)
        original = Harness(); nine(original)
        h.binding = replace(h.binding, images=original.binding.images)
        h.request = replace(h.request, binding=h.binding)
        with patch('delivery_transport.ssl.SSLContext', return_value=h.context): h.reader = h.build()
        return h

    def replies(self, h):
        original = h.objects()
        access = []
        import delivery_platform_readback as platform
        for capability in h.reader.capabilities:
            obj = json.loads(platform.access_body(capability)); obj['status'] = {'allowed': True}; access.append(obj)
        return original[:3] + access + original[-3:]

    def test_exact_24_requests_for_18_scoped_capabilities(self):
        h = self.platform_helper(); h.enqueue(self.replies(h)); value = h.reader.inspect(h.request)
        self.assertEqual(24, len(value.raw_responses)); self.assertEqual(18, len(value.required_capabilities))
        attrs = [json.loads(bytes(s.sent).split(b'\r\n\r\n', 1)[1])['spec']['resourceAttributes'] for s in h.sockets[3:21]]
        self.assertEqual(6, sum(x['name'] == 'end-page' for x in attrs))
        self.assertEqual({'get', 'patch'}, {x['verb'] for x in attrs})
        self.assertTrue(all(x['namespace'] == 'fds-app' for x in attrs))

    def test_one_valid_looking_ssar_http200_still_rejected(self):
        h = self.platform_helper(); h.enqueue(self.replies(h))
        body = self.replies(h)[20]; h.queue[20] = response(core.encoded(body), 200)
        import delivery_platform_readback as platform
        with self.assertRaises(platform.PlatformReadbackError): h.reader.inspect(h.request)

    def finish_helper(self):
        h = evidence_tests.EvidenceSourceTests(); h.setUp(); self.addCleanup(h.doCleanups); nine(h.h)
        receipt = h.h.run(); self.assertEqual('ROLLOUT_OBSERVED', receipt['status'])
        h.progress = Progress(core.encoded(receipt), tuple(core.encoded(h.h.objects[core.path_for(k, h.h.binding)])
                             for k in core.objects_for(h.h.binding)))
        return h

    def test_finish_nine_original_resolved_twice_not_six(self):
        h = self.finish_helper(); h.adapter('finish')(h.request()); self.assertEqual(2, h.read.call_count)
        h.progress = replace(h.progress, responses=h.progress.responses[:6]); h.read.reset_mock()
        import delivery_evidence_source as port
        with self.assertRaises(port.EvidenceSourceError): h.adapter('finish')(h.request())
        h.read.assert_not_called()

    def test_finish_swapped_namespace_and_deleting_reply_rejected_before_source(self):
        for mode in ('swap', 'namespace', 'delete'):
            h = self.finish_helper(); raws = list(h.progress.responses)
            if mode == 'swap': raws[-1], raws[-2] = raws[-2], raws[-1]
            else:
                obj = json.loads(raws[-1]); obj['metadata'].update(
                    **({'namespace': 'rosa-app'} if mode == 'namespace' else {'deletionTimestamp': '2026-10-20T00:00:00Z'}))
                raws[-1] = core.encoded(obj)
            h.progress = replace(h.progress, responses=tuple(raws)); h.read.reset_mock()
            import delivery_evidence_source as port
            with self.assertRaises(port.EvidenceSourceError): h.adapter('finish')(h.request())
            h.read.assert_not_called(); h.doCleanups()

    def test_reader_provider_and_first_baseline_exact_nine_404s(self):
        from delivery_baseline import FirstDeploymentBaseline
        from delivery_object_reader import ObjectReader, ObjectProvider
        from test_delivery_operational import ORIGIN
        from test_delivery_transport import CA
        h = reader_tests.ReaderTests(); h.setUp(); self.addCleanup(h.doCleanups)
        core_h = nine(Harness()); h.b = replace(core_h.binding, ca_sha256=core.digest(CA))
        h.request = replace(h.request, binding=h.b)
        with patch('delivery_transport.ssl.SSLContext', return_value=h.context):
            h.reader = ObjectReader(h.b, credential=h.credential, ca_pem=CA,
                                   endpoint_ip='192.0.2.10', monotonic=lambda: h.clock)
        h.queue = [response(core.encoded(reader_tests.absent(k)), 404) for k in core.objects_for(h.b)]
        admit = FirstDeploymentBaseline(h.reader, progress=lambda: h.progress, provenance=ORIGIN)
        self.assertEqual((None,) * 9, admit(h.request).objects)
        h.queue = [response(core.encoded(reader_tests.absent(k)), 404) for k in core.objects_for(h.b)]
        reconciler = objects.ObjectReconciler(h.b, core_h.render, core_h.contract, validator, (None,) * 9)
        provider = ObjectProvider(h.reader, reconciler, lambda: h.progress)
        self.assertEqual(h.progress.key, provider.objects(h.request).progress_key)
        self.assertEqual(18, len(h.sockets)); self.assertTrue(all(bytes(s.sent).startswith(b'GET ') for s in h.sockets))

    def test_bounded_tls_sender_accepts_nine_and_rejects_secret_before_socket(self):
        import delivery_transport as transport
        from test_delivery_transport import CA, TOKEN, FakeSocket
        import ssl
        from unittest.mock import Mock
        h = nine(Harness()); h.rebind(ca_sha256=core.digest(CA))
        h.lease = replace(h.lease, credential_fingerprint=core.digest(TOKEN.encode()))
        context = Mock(verify_mode=ssl.CERT_REQUIRED, check_hostname=True, keylog_filename=None,
                       minimum_version=ssl.TLSVersion.TLSv1_2)
        context.wrap_socket.side_effect = lambda sock, **kw: sock
        raws = []
        for i, obj in enumerate(json.loads(h.render)):
            obj['metadata'].update(uid='synthetic-' + str(i), resourceVersion='1')
            if obj['kind'] == 'Deployment': obj['metadata']['generation'] = 1
            raws.append(obj)
        queue = list(raws) + [dict(copy.deepcopy(o), status={'observedGeneration': 1, 'replicas': 3,
            'updatedReplicas': 3, 'readyReplicas': 3, 'availableReplicas': 3}) for o in raws if o['kind'] == 'Deployment']
        sockets = []
        def socket_factory(*args):
            self.assertTrue(queue, 'UNEXPECTED_SOCKET')
            sock = FakeSocket(response(core.encoded(queue.pop(0)))); sockets.append(sock); return sock
        with patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS_FORBIDDEN')), \
             patch.object(socket, 'create_connection', side_effect=AssertionError('NETWORK_FORBIDDEN')), \
             patch.object(socket, 'socket', side_effect=socket_factory), \
             patch.object(transport.ssl, 'SSLContext', return_value=context):
            sender = transport.Sender(h.binding, ca_pem=CA, token=TOKEN, endpoint_ip='192.0.2.10')
            h.adapter.send = sender
            r = h.run(); self.assertEqual('ROLLOUT_OBSERVED', r['status'], r)
            self.assertEqual(12, len(sockets)); self.assertTrue(all(s.closed for s in sockets))
            req = core.Request(h.binding.key, h.lease.credential_fingerprint, h.binding.api_origin,
                h.binding.ca_sha256, 'PATCH', '/api/v1/namespaces/fds-app/secrets/db?fieldManager=fds-deployer',
                'application/apply-patch+yaml', b'{}', 10)
            with self.assertRaises(transport.TransportError): sender(req)
            self.assertEqual(12, len(sockets))
