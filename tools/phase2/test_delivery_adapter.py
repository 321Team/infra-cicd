"""Synthetic offline state-machine tests; no Runtime/ROSA/AC evidence."""
import copy
import json
import unittest
from dataclasses import replace

import delivery_adapter as a


SENSITIVE = 'SYNTHETIC_TOKEN_MUST_NOT_APPEAR'


def fixture():
    images = tuple((name, f'registry.example.com/fds/{name}@sha256:' + 'a' * 64)
                   for name in a.SERVICES)
    docs = []
    for api, kind, name in a.OBJECTS:
        obj = {'apiVersion': api, 'kind': kind,
               'metadata': {'name': name, 'namespace': 'fds-app'}, 'spec': {}}
        if kind == 'Deployment':
            obj['spec'] = {'replicas': 3, 'template': {
                'metadata': {'labels': {'app': name, **(
                    {'onprem-egress': 'true'} if name == 'transaction-api' else {})}},
                'spec': {'serviceAccountName': name + '-sa', 'automountServiceAccountToken': False,
                         'containers': [{'name': name, 'image': dict(images)[name]}]}}}
        elif kind == 'Route':
            obj['spec'] = {'host': 'app.rosa.example.com', 'tls': dict(a.TLS)}
        else:
            obj['spec'] = {'selector': {'app': name}, 'ports': [{'port': 8000}]}
        docs.append(obj)
    # This is a synthetic parser protocol fixture, NOT a real #43 YAML validator.
    render = a.encoded(docs)
    contract = b'SYNTHETIC_CONTRACT_NOT_A_RUNTIME_INPUT'
    b = a.Binding('synthetic-run-1', 'deploy', 'https://api.rosa.example.com:6443',
                  'synthetic-cluster', 'synthetic-namespace-uid', 'b' * 64, 'c' * 40,
                  a.digest(contract), a.digest(render), 'd' * 64, 'e' * 64,
                  'app.rosa.example.com', images)
    return b, render, contract


def validator(render, contract):
    report = {'result': 'PASS', 'mode': 'app', 'namespace': 'fds-app',
              'placeholders_allowed': False, 'placeholders': [], 'errors': [],
              'approved_route_host': 'app.rosa.example.com',
              'rendered_sha256': a.digest(render), 'contract_sha256': a.digest(contract)}
    return report, json.loads(render)


class Harness:
    def __init__(self):
        self.binding, self.render, self.contract = fixture()
        self.now = self.mono = 1000.0
        self.lease = a.Lease(self.binding.key, 'f' * 64, 900, 1900, 1900)
        self.requests, self.checkpoints, self.objects = [], [], {}
        self.reject_at = self.fail_at = None
        self.ready = True
        self.before_checkpoint = lambda receipt: None
        self.after_send = lambda request, obj: None
        self.authorizer = lambda binding: self.lease
        self.adapter = a.Adapter(authorize=lambda b: self.authorizer(b), send=self.send,
                                 checkpoint=self.checkpoint, wall=lambda: self.now,
                                 monotonic=lambda: self.mono, sleep=self.sleep)

    def sleep(self, seconds):
        self.now += seconds
        self.mono += seconds

    def checkpoint(self, receipt):
        self.before_checkpoint(receipt)
        self.checkpoints.append(copy.deepcopy(receipt))

    def send(self, request):
        self.requests.append(request)
        count = len(self.requests)
        if count == self.reject_at:
            raise a.Rejected(SENSITIVE)
        if count == self.fail_at:
            raise TimeoutError(SENSITIVE)
        path = request.path.split('?')[0]
        if request.method == 'PATCH':
            obj = json.loads(request.body)
            obj['metadata'].update(uid='synthetic-uid-' + str(count), resourceVersion=str(count))
            if obj['kind'] == 'Deployment':
                obj['metadata']['generation'] = 1
            self.objects[path] = copy.deepcopy(obj)
        else:
            obj = copy.deepcopy(self.objects[path])
            obj['status'] = {'observedGeneration': 1, 'replicas': 3, 'updatedReplicas': 3,
                             'readyReplicas': 3 if self.ready else 0, 'availableReplicas': 3}
        self.after_send(request, obj)
        return obj

    def run(self, validate=validator):
        return self.adapter.run(self.binding, self.render, self.contract, validate)

    def rebind(self, **changes):
        self.binding = replace(self.binding, **changes)
        self.lease = replace(self.lease, binding_key=self.binding.key)

    def change_render(self, mutate):
        docs = json.loads(self.render)
        mutate(docs)
        self.render = a.encoded(docs)
        self.rebind(rendered_sha256=a.digest(self.render))


class AdapterTests(unittest.TestCase):
    def test_six_scoped_applies_and_two_rollouts_are_not_delivery_acceptance(self):
        h = Harness()
        r = h.run()
        self.assertEqual(r['status'], 'ROLLOUT_OBSERVED', r)
        self.assertEqual([q.method for q in h.requests], ['PATCH'] * 6 + ['GET'] * 2)
        for q in h.requests:
            self.assertIn('/namespaces/fds-app/', q.path)
            self.assertEqual(q.api_origin, h.binding.api_origin)
            self.assertEqual(q.binding_key, h.binding.key)
            self.assertEqual(q.credential_fingerprint, h.lease.credential_fingerprint)
            self.assertLessEqual(q.timeout_seconds, 10)
            self.assertNotIn('force', q.path)
            if q.method == 'PATCH':
                self.assertTrue(q.path.endswith('?fieldManager=fds-deployer'))
                self.assertEqual(q.content_type, 'application/apply-patch+yaml')
        self.assertEqual(r['delivery_gate'], 'NOT_VERIFIED')
        self.assertEqual(r['pod_image_id'], 'NOT_VERIFIED')
        self.assertEqual(r['health_e2e'], 'NOT_VERIFIED')
        intents = [c for c in h.checkpoints if c['status'] == 'WRITE_PENDING']
        self.assertEqual(len(intents), 6)

    def test_render_or_contract_byte_tampering_stops_before_sender(self):
        for field in ('render', 'contract'):
            h = Harness()
            setattr(h, field, getattr(h, field) + b' ')
            self.assertEqual(h.run()['error_code'], 'INPUT_HASH_MISMATCH')
            self.assertEqual(h.requests, [])

    def test_scope_missing_extra_duplicate_or_namespace_is_blocked(self):
        changes = [lambda d: d.pop(), lambda d: d.append(copy.deepcopy(d[0])),
                   lambda d: d[0]['metadata'].update(namespace='rosa-app'),
                   lambda d: d[0].update(kind='Secret'),
                   lambda d: d[0]['metadata'].update(ownerReferences=[{}]),
                   lambda d: d.__setitem__(1, copy.deepcopy(d[0]))]
        for change in changes:
            with self.subTest(change=change):
                h = Harness()
                h.change_render(change)
                self.assertEqual(h.run()['status'], 'STOPPED')
                self.assertEqual(h.requests, [])

    def test_host_pending_mismatch_and_tls_extra_fields_block(self):
        for change in (lambda d: d[-1]['spec'].update(host='other.example.com'),
                       lambda d: d[-1]['spec']['tls'].update(key=SENSITIVE),
                       lambda d: d[-1]['spec']['tls'].update(termination='passthrough')):
            h = Harness()
            h.change_render(change)
            self.assertEqual(h.run()['error_code'], 'ROUTE_CONTRACT_MISMATCH')
            self.assertEqual(h.requests, [])
        for value in ('PENDING', 'test.invalid', '127.0.0.1', ''):
            h = Harness()
            h.rebind(route_host=value)
            self.assertEqual(h.run()['error_code'], 'ROUTE_HOST_INVALID')
            self.assertEqual(h.requests, [])

    def test_p1_api_and_unsafe_origins_are_blocked(self):
        for value in ('https://10.1.93.109:6443', 'http://api.example.com',
                      'https://user:password@api.example.com', 'https://api.example.com/extra',
                      'https://api.example.com?token=secret', 'https://api.example.com:9999',
                      'https://api.example.com#x', 'https://api.example.com:invalid'):
            h = Harness()
            h.rebind(api_origin=value)
            self.assertEqual(h.run()['status'], 'STOPPED')
            self.assertEqual(h.requests, [])

    def test_producer_fail_placeholder_or_stale_report_never_authorizes(self):
        for field, value in [('result', 'FAIL'), ('mode', 'prereq'), ('placeholders_allowed', True),
                             ('approved_route_host', 'PENDING'), ('rendered_sha256', '0' * 64),
                             ('contract_sha256', '0' * 64), ('placeholders', ['.invalid']),
                             ('namespace', 'rosa-app')]:
            h = Harness()
            def bad(raw, contract):
                report, docs = validator(raw, contract)
                report[field] = value
                return report, docs
            self.assertEqual(h.run(bad)['error_code'], 'RENDER_CHECK_FAILED')
            self.assertEqual(h.requests, [])

    def test_wrong_image_service_account_or_egress_stops(self):
        for change in (
                lambda d: d[2]['spec']['template']['spec']['containers'][0].update(image='x:latest'),
                lambda d: d[2]['spec']['template']['spec'].update(serviceAccountName='default'),
                lambda d: d[2]['spec']['template']['spec'].update(automountServiceAccountToken=True),
                lambda d: d[3]['spec']['template']['metadata']['labels'].update(**{'onprem-egress': 'true'})):
            h = Harness()
            h.change_render(change)
            self.assertEqual(h.run()['status'], 'STOPPED')
            self.assertEqual(h.requests, [])

    def test_short_actual_token_or_approval_window_blocks_first_write(self):
        for field in ('token_expires_at', 'approval_expires_at'):
            h = Harness()
            h.lease = replace(h.lease, **{field: h.now + 659.99})
            self.assertEqual(h.run()['error_code'], 'FIRST_WRITE_WINDOW_TOO_SHORT')
            self.assertEqual(h.requests, [])
        h = Harness()
        h.lease = replace(h.lease, token_expires_at=h.now + 660)
        self.assertEqual(h.run()['status'], 'ROLLOUT_OBSERVED')

    def test_lifetime_rechecked_after_durable_intent(self):
        h = Harness()
        h.before_checkpoint = lambda r: h.sleep(250) if r['status'] == 'WRITE_PENDING' else None
        self.assertEqual(h.run()['error_code'], 'FIRST_WRITE_WINDOW_TOO_SHORT')
        self.assertEqual(h.requests, [])

    def test_expiry_mid_apply_stops_without_renewal_or_rollback(self):
        h = Harness()
        h.before_checkpoint = lambda r: h.sleep(901) if r['status'] == 'WRITE_ACKNOWLEDGED' else None
        self.assertEqual(h.run()['error_code'], 'AUTH_EXPIRED')
        self.assertEqual(len(h.requests), 1)

    def test_token_request_not_900_is_blocked(self):
        for value in (3600, 0, '900', True):
            h = Harness()
            h.lease = replace(h.lease, requested_seconds=value)
            self.assertEqual(h.run()['error_code'], 'TOKEN_REQUEST_INVALID')
            self.assertEqual(h.requests, [])

    def test_expiry_renewal_or_credential_swap_is_blocked(self):
        for changes in ({'token_expires_at': 2000}, {'credential_fingerprint': '0' * 64},
                        {'approval_expires_at': 2000}):
            h = Harness()
            h.after_send = lambda q, o: setattr(h, 'lease', replace(h.lease, **changes))
            self.assertEqual(h.run()['error_code'], 'AUTHORITY_CHANGED')
            self.assertEqual(len(h.requests), 1)

    def test_wrong_receipt_source_target_or_action_binding_is_blocked(self):
        for field, value in (('source_commit', '0' * 40), ('cluster_id', 'other-cluster'),
                             ('namespace_uid', 'other-uid'), ('platform_receipt_sha256', '0' * 64),
                             ('supply_receipt_sha256', '0' * 64), ('action', 'restore')):
            h = Harness()
            h.binding = replace(h.binding, **{field: value})  # no corresponding approval
            self.assertEqual(h.run()['error_code'], 'AUTHORIZATION_DENIED')
            self.assertEqual(h.requests, [])

    def test_fresh_authorization_loss_stops_next_write(self):
        h = Harness()
        def authorize(binding):
            if h.requests:
                raise a.Stop(SENSITIVE)
            return h.lease
        h.authorizer = authorize
        r = h.run()
        self.assertEqual(r['error_code'], 'AUTHORIZATION_DENIED')
        self.assertNotIn(SENSITIVE, json.dumps(r))
        self.assertEqual(len(h.requests), 1)

    def test_ssa_conflict_has_no_force_retry_or_automatic_restore(self):
        h = Harness()
        h.reject_at = 3
        r = h.run()
        self.assertEqual(r['error_code'], 'API_REJECTED')
        self.assertEqual(len(r['acknowledged']), 2)
        self.assertIsNone(r['pending'])
        self.assertEqual(len(h.requests), 3)

    def test_timeout_or_invalid_success_preserves_pending_and_stops(self):
        for corrupt in (False, True):
            h = Harness()
            if corrupt:
                h.after_send = lambda q, o: o['metadata'].update(namespace='other')
            else:
                h.fail_at = 1
            r = h.run()
            self.assertIn(r['error_code'], ('WRITE_OUTCOME_UNKNOWN', 'WRITE_RESPONSE_INVALID'))
            self.assertIsNotNone(r['pending'])
            self.assertEqual(len(h.requests), 1)
            self.assertNotIn(SENSITIVE, json.dumps(r))

    def test_checkpoint_failure_before_first_send_and_after_ack_stop(self):
        for phase in ('WRITE_PENDING', 'WRITE_ACKNOWLEDGED'):
            h = Harness()
            def fail(receipt):
                if receipt['status'] == phase:
                    raise RuntimeError(SENSITIVE)
            h.before_checkpoint = fail
            r = h.run()
            self.assertEqual(r['error_code'], 'CHECKPOINT_FAILED')
            self.assertEqual(len(h.requests), 0 if phase == 'WRITE_PENDING' else 1)
            self.assertNotIn(SENSITIVE, json.dumps(r))

    def test_repeat_same_instance_is_rejected_even_after_stop(self):
        h = Harness()
        h.fail_at = 1
        h.run()
        with self.assertRaisesRegex(a.Stop, 'ADAPTER_ALREADY_USED'):
            h.run()
        self.assertEqual(len(h.requests), 1)

    def test_sender_path_cannot_be_used_before_or_after_run(self):
        h = Harness()
        with self.assertRaisesRegex(a.Stop, 'SESSION_INACTIVE'):
            h.adapter.request(h.binding, a.OBJECTS[0], 'PATCH', b'{}')
        h.run()
        with self.assertRaisesRegex(a.Stop, 'SESSION_INACTIVE'):
            h.adapter.request(h.binding, a.OBJECTS[0], 'DELETE')
        self.assertEqual(len(h.requests), 8)

    def test_cross_instance_replay_requires_external_durable_claim(self):
        h = Harness()
        h.authorizer = lambda b: None  # trusted claim provider rejects an already-consumed RUN
        self.assertEqual(h.run()['error_code'], 'AUTHORIZATION_DENIED')
        self.assertEqual(h.requests, [])

    def test_restore_uses_separately_bound_previous_render(self):
        h = Harness()
        previous = tuple((name, image[:-64] + '1' * 64) for name, image in h.binding.images)
        def change(docs):
            for d in docs:
                if d['kind'] == 'Deployment':
                    d['spec']['template']['spec']['containers'][0]['image'] = dict(previous)[d['metadata']['name']]
        h.change_render(change)
        h.rebind(run_id='synthetic-restore-2', action='restore', images=previous)
        r = h.run()
        self.assertEqual(r['status'], 'ROLLOUT_OBSERVED', r)
        self.assertEqual(len(h.requests), 8)
        self.assertEqual(r['delivery_gate'], 'NOT_VERIFIED')

    def test_rollout_timeout_does_not_reapply_or_restore(self):
        h = Harness()
        h.ready = False
        r = h.run()
        self.assertEqual(r['error_code'], 'ROLLOUT_TIMEOUT')
        self.assertEqual(sum(q.method == 'PATCH' for q in h.requests), 6)
        self.assertEqual(h.now, 1300)

    def test_rollout_uid_generation_spec_drift_and_deadline_fail(self):
        mutations = [lambda o: o['metadata'].update(uid='replacement'),
                     lambda o: o['metadata'].update(generation=2),
                     lambda o: o['spec'].update(replicas=4),
                     lambda o: o['status'].update(conditions=[{'type': 'Progressing', 'status': 'False',
                                                             'reason': 'ProgressDeadlineExceeded'}])]
        for mutate in mutations:
            h = Harness()
            h.after_send = lambda q, o: mutate(o) if q.method == 'GET' else None
            r = h.run()
            self.assertEqual(r['status'], 'STOPPED')
            self.assertEqual(len(h.requests), 7)

    def test_read_failure_and_late_response_do_not_report_rollout(self):
        for late in (False, True):
            h = Harness()
            if late:
                h.after_send = lambda q, o: h.sleep(301) if q.method == 'GET' else None
            else:
                h.fail_at = 7
            r = h.run()
            self.assertEqual(r['error_code'], 'READ_DEADLINE_EXCEEDED' if late else 'READ_FAILED')
            self.assertEqual(r['rollout'], [])

    def test_nonfinite_and_backward_clocks_fail_closed(self):
        for value in (float('nan'), float('inf'), True):
            h = Harness()
            h.lease = replace(h.lease, token_expires_at=value)
            self.assertEqual(h.run()['error_code'], 'AUTH_WINDOW_INVALID')
            self.assertEqual(h.requests, [])
        h = Harness()
        h.after_send = lambda q, o: setattr(h, 'now', 999)
        self.assertEqual(h.run()['error_code'], 'CLOCK_REVERSED')
        self.assertEqual(len(h.requests), 1)

    def test_freezing_wall_clock_does_not_extend_token(self):
        h = Harness()
        h.before_checkpoint = lambda r: setattr(h, 'mono', h.mono + 901) if r['status'] == 'WRITE_ACKNOWLEDGED' else None
        self.assertEqual(h.run()['error_code'], 'AUTH_EXPIRED')
        self.assertEqual(len(h.requests), 1)

    def test_late_write_response_is_unknown_and_never_retried(self):
        h = Harness()
        h.after_send = lambda q, o: h.sleep(11)
        r = h.run()
        self.assertEqual(r['error_code'], 'WRITE_OUTCOME_UNKNOWN')
        self.assertEqual(len(h.requests), 1)
        self.assertIsNotNone(r['pending'])

    def test_mutable_validator_output_or_checkpoint_cannot_change_payloads(self):
        h = Harness()
        docs = json.loads(h.render)
        def bridge(raw, contract):
            return validator(raw, contract)[0], docs
        def mutate(receipt):
            docs[0]['metadata']['namespace'] = 'other'
            receipt['acknowledged'].append('FORGED')
        h.before_checkpoint = mutate
        r = h.run(bridge)
        self.assertEqual(r['status'], 'ROLLOUT_OBSERVED', r)
        self.assertNotIn('FORGED', r['acknowledged'])
        self.assertTrue(all(json.loads(q.body)['metadata']['namespace'] == 'fds-app'
                            for q in h.requests if q.method == 'PATCH'))

    def test_validator_error_does_not_leak_raw_input(self):
        h = Harness()
        def fail(*args):
            raise a.Stop(SENSITIVE)
        r = h.run(fail)
        self.assertEqual(r['error_code'], 'RENDER_VALIDATOR_FAILED')
        self.assertNotIn(SENSITIVE, json.dumps(r))
        self.assertEqual(h.requests, [])


if __name__ == '__main__':
    unittest.main()
