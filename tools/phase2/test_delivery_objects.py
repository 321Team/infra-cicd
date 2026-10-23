"""Synthetic reconciliation and lifecycle tests; no API, credentials or network."""
import copy
from dataclasses import replace
import json
import socket
import unittest
from unittest.mock import patch

import delivery_adapter as core
from delivery_objects import ObjectReconciler, REVISION, REVISION_FIELDS, decode
from delivery_verifier import ObjectEvidence, Progress, VerificationError
from test_delivery_adapter import fixture, validator
from test_delivery_operational import ContractHarness


def owned(obj, number):
    obj = copy.deepcopy(obj)
    obj['metadata'].update(uid='synthetic-uid-' + str(number), resourceVersion='1',
        managedFields=[{'manager': 'fds-deployer', 'operation': 'Apply',
                       'apiVersion': obj['apiVersion'], 'fieldsType': 'FieldsV1',
                       'fieldsV1': {'f:spec': {}}, 'time': '2026-10-16T00:00:00Z'}])
    return obj


def controller_revision(obj, value='1', status=False, map_owned=False):
    obj['metadata'].setdefault('annotations', {})[REVISION] = value
    entry = {'manager': 'kube-controller-manager', 'operation': 'Update',
             'apiVersion': 'apps/v1', 'fieldsType': 'FieldsV1',
             'fieldsV1': copy.deepcopy(REVISION_FIELDS)}
    if map_owned:
        entry['fieldsV1']['f:metadata']['f:annotations']['.'] = {}
    if status:
        entry['subresource'] = 'status'
        entry['fieldsV1']['f:status'] = {'f:observedGeneration': {}}
    obj['metadata']['managedFields'].append(entry)
    return entry


class ObjectTests(unittest.TestCase):
    def setUp(self):
        for name in ('socket', 'getaddrinfo', 'create_connection'):
            guard = patch.object(socket, name, side_effect=AssertionError('NETWORK_FORBIDDEN'))
            guard.start()
            self.addCleanup(guard.stop)
        self.b, self.render, self.contract = fixture()
        self.docs = tuple(owned(o, i) for i, o in enumerate(json.loads(self.render)))
        self.raws = tuple(core.encoded(o) for o in self.docs)
        self.absent = (None,) * 6
        self.r = self.make(self.absent)

    def make(self, baseline):
        return ObjectReconciler(self.b, self.render, self.contract, validator, baseline)

    def progress(self, count, pending=False):
        journal = {'schema': 'P2-CICD-apply-core-1', 'status': 'WRITE_PENDING' if pending else
                   'WRITE_ACKNOWLEDGED' if count else 'PREPARED', 'binding_key': self.b.key,
                   'acknowledged': list('/'.join(k) for k in core.OBJECTS[:count]),
                   'pending': '/'.join(core.OBJECTS[count]) if pending else None,
                   'rollout': [], 'error_code': None, 'delivery_gate': 'NOT_VERIFIED',
                   'health_e2e': 'NOT_VERIFIED', 'pod_image_id': 'NOT_VERIFIED'}
        return Progress(core.encoded(journal), self.raws[:count])

    def reject_change(self, change, index=0):
        docs = copy.deepcopy(self.docs)
        change(docs[index])
        observed = tuple(core.encoded(x) for x in docs)
        with self.assertRaises(VerificationError):
            self.r.check(self.b, self.progress(6), observed)

    def test_initial_absence_and_each_ack_prefix(self):
        self.assertEqual(self.r.check(self.b, Progress(), self.absent), Progress().key)
        for count in range(7):
            p = self.progress(count)
            self.assertEqual(self.r.check(self.b, p, self.raws[:count]+self.absent[count:]), p.key)

    def test_pending_before_send_keeps_previous_state(self):
        for count in range(6):
            p = self.progress(count, pending=True)
            self.assertEqual(self.r.check(self.b, p, self.raws[:count]+self.absent[count:]), p.key)

    def test_reply_without_durable_ack_is_never_promoted(self):
        p = replace(self.progress(0, pending=True), responses=self.raws[:1])
        with self.assertRaisesRegex(VerificationError, 'REPLY_ACK'):
            self.r.check(self.b, p, self.raws[:1]+self.absent[1:])

    def test_ack_without_reply_blocks(self):
        with self.assertRaisesRegex(VerificationError, 'REPLY_ACK'):
            self.r.check(self.b, replace(self.progress(1), responses=()), self.raws)

    def test_unacknowledged_new_object_blocks(self):
        with self.assertRaisesRegex(VerificationError, 'DRIFT'):
            self.r.check(self.b, self.progress(0, pending=True), self.raws[:1]+self.absent[1:])

    def test_deleted_or_replaced_uid_blocks(self):
        self.reject_change(lambda o: o['metadata'].update(uid='foreign'))
        with self.assertRaises(VerificationError):
            self.r.check(self.b, self.progress(6), self.absent)

    def test_uid_replacement_in_ack_reply_blocks(self):
        r = self.make(self.raws)
        reply = copy.deepcopy(self.docs[0]); reply['metadata']['uid'] = 'foreign'
        raw = core.encoded(reply)
        with self.assertRaisesRegex(VerificationError, 'UID_REPLACED'):
            r.check(self.b, replace(self.progress(1), responses=(raw,)), (raw,)+self.raws[1:])

    def test_spec_label_annotation_and_owner_drift_block(self):
        for mutate in (lambda o: o['spec'].update(selector={'app': 'foreign'}),
                       lambda o: o['metadata'].update(labels={'foreign': 'true'}),
                       lambda o: o['metadata'].update(annotations={'foreign': 'true'}),
                       lambda o: o['metadata']['managedFields'][0].update(manager='foreign'),
                       lambda o: o['metadata'].update(finalizers=['foreign'])):
            self.reject_change(mutate)

    def test_image_drift_and_external_service_status_block(self):
        self.reject_change(lambda o: o['spec']['template']['spec']['containers'][0].update(
            image='registry.example.com/fds/app@sha256:'+'e'*64), index=2)
        self.reject_change(lambda o: o.update(status={'loadBalancer': {'ingress': [{'ip': '192.0.2.1'}]}}))

    def test_volatile_status_version_and_ownership_time_allowed(self):
        observed = copy.deepcopy(self.docs)
        for obj in observed:
            obj['metadata']['resourceVersion'] = '9'
            obj['metadata']['managedFields'][0]['time'] = '2026-10-16T00:00:02Z'
            obj['metadata']['managedFields'].append({'manager': 'controller', 'operation': 'Update',
                'apiVersion': obj['apiVersion'], 'fieldsType': 'FieldsV1', 'subresource': 'status',
                'fieldsV1': {'f:status': {}}})
            obj['status'] = {'observedGeneration': 2}
        self.r.check(self.b, self.progress(6), tuple(core.encoded(x) for x in observed))

    def test_status_owner_cannot_hide_spec_fields(self):
        self.reject_change(lambda o: o['metadata']['managedFields'].append({
            'manager': 'controller', 'operation': 'Update', 'apiVersion': 'v1',
            'fieldsType': 'FieldsV1', 'subresource': 'status', 'fieldsV1': {'f:spec': {}}}))

    def test_controller_revision_after_deployment_ack_and_at_finish(self):
        for index in (2, 3):
            for count in (index + 1, 6):
                for status in (False, True):
                    with self.subTest(index=index, count=count, status=status):
                        docs = copy.deepcopy(self.docs)
                        controller_revision(docs[index], status=status)
                        observed = tuple(core.encoded(x) for x in docs[:count])+self.absent[count:]
                        self.make(self.absent).check(self.b, self.progress(count), observed)

    def test_existing_revision_survives_baseline_then_increases_after_ack(self):
        for status in (False, True):
            docs = copy.deepcopy(self.docs)
            controller_revision(docs[2], '2', status=status)
            baseline = tuple(core.encoded(x) for x in docs)
            r = self.make(baseline)
            r.check(self.b, Progress(), baseline)
            docs[2]['metadata']['annotations'][REVISION] = '3'
            observed = tuple(core.encoded(x) for x in docs)
            r.check(self.b, replace(self.progress(6), responses=baseline), observed)

    def test_revision_annotations_map_marker_after_ack(self):
        for index in (2, 3):
            for count in (index + 1, 6):
                for status in (False, True):
                    with self.subTest(index=index, count=count, status=status):
                        docs = copy.deepcopy(self.docs)
                        controller_revision(docs[index], status=status, map_owned=True)
                        observed = tuple(core.encoded(x) for x in docs[:count])+self.absent[count:]
                        self.make(self.absent).check(self.b, self.progress(count), observed)

    def test_revision_map_marker_does_not_hide_extra_fields_or_bad_marker(self):
        mutations = (
            lambda e: e['fieldsV1']['f:metadata']['f:annotations'].update({'f:other': {}}),
            lambda e: e['fieldsV1']['f:metadata']['f:annotations'].update({'.': {'f:other': {}}}),
            lambda e: e['fieldsV1']['f:metadata']['f:annotations'].update({'.': None}),
            lambda e: e['fieldsV1']['f:metadata']['f:annotations'].update({'.': []}),
            lambda e: e['fieldsV1']['f:metadata']['f:annotations'].update({'f:'+REVISION: {'x': {}}}),
            lambda e: e['fieldsV1']['f:metadata'].update({'.': {}}),
            lambda e: e['fieldsV1'].update({'f:spec': {}}),
        )
        for status in (False, True):
            for mutate in mutations:
                def change(o):
                    mutate(controller_revision(o, status=status, map_owned=True))
                self.reject_change(change, index=2)
            def changed_annotation(o):
                controller_revision(o, status=status, map_owned=True)
                o['metadata']['annotations']['unrelated'] = 'changed'
            self.reject_change(changed_annotation, index=2)

    def test_revision_map_marker_baseline_and_ack_reply_keep_guards(self):
        for status in (False, True):
            docs = copy.deepcopy(self.docs)
            controller_revision(docs[2], '2', status=status, map_owned=True)
            baseline = tuple(core.encoded(x) for x in docs)
            r = self.make(baseline)
            r.check(self.b, Progress(), baseline)
            docs[2]['metadata']['annotations'][REVISION] = '3'
            observed = tuple(core.encoded(x) for x in docs)
            with self.assertRaisesRegex(VerificationError, 'OBJECT_DRIFT'):
                r.check(self.b, Progress(), observed)
            r.check(self.b, replace(self.progress(6), responses=baseline), observed)
            with self.assertRaisesRegex(VerificationError, 'OBJECT_REVISION_REVERSED'):
                r.check(self.b, replace(self.progress(6), responses=baseline), baseline)

    def test_revision_before_own_ack_is_still_drift(self):
        docs = copy.deepcopy(self.docs)
        controller_revision(docs[2])
        with self.assertRaisesRegex(VerificationError, 'OBJECT_DRIFT'):
            self.make(self.raws).check(self.b, Progress(), tuple(core.encoded(x) for x in docs))

    def test_revision_exception_does_not_cover_service_or_route(self):
        for index in (0, 4):
            self.reject_change(lambda o: controller_revision(o), index=index)

    def test_revision_owner_must_be_exact_and_unambiguous(self):
        for mutation in (lambda e: e.update(manager='foreign'),
                         lambda e: e.update(operation='Apply'),
                         lambda e: e.update(apiVersion='apps/v1beta1'),
                         lambda e: e.update(subresource='scale'),
                         lambda e: e['fieldsV1'].update({'f:spec': {}}),
                         lambda e: e['fieldsV1']['f:metadata']['f:annotations'].update({'f:other': {}})):
            for status in (False, True):
                def change(o):
                    mutation(controller_revision(o, status=status))
                self.reject_change(change, index=2)
        self.reject_change(lambda o: (controller_revision(o), controller_revision(o)), index=2)

    def test_same_controller_cannot_hide_other_annotation_or_spec_change(self):
        for mutation in (lambda o: o['metadata']['annotations'].update(other='changed'),
                         lambda o: o['spec'].update(replicas=99)):
            for status in (False, True):
                def change(o):
                    controller_revision(o, status=status)
                    mutation(o)
                self.reject_change(change, index=2)

    def test_revision_invalid_values_and_owner_without_annotation_block(self):
        for value in ('', '0', '-1', '01', '+1', '1.0', '1e3', ' 1', '١', '9'*20,
                      str(2**63), 1, None):
            self.reject_change(lambda o: controller_revision(o, value), index=2)
        def owner_only(o):
            controller_revision(o)
            del o['metadata']['annotations'][REVISION]
        self.reject_change(owner_only, index=2)

    def test_revision_without_owner_is_not_ignored(self):
        self.reject_change(lambda o: o['metadata'].setdefault('annotations', {}).update(
            {REVISION: '1'}), index=2)

    def test_revision_cannot_reverse_or_disappear_after_observation(self):
        docs = list(copy.deepcopy(self.docs))
        controller_revision(docs[2], '3')
        self.r.check(self.b, self.progress(6), tuple(core.encoded(x) for x in docs))
        for value in ('2', None):
            if value is None:
                docs[2] = copy.deepcopy(self.docs[2])
            else:
                docs[2]['metadata']['annotations'][REVISION] = value
            with self.assertRaisesRegex(VerificationError, 'OBJECT_REVISION_REVERSED'):
                self.r.check(self.b, self.progress(6), tuple(core.encoded(x) for x in docs))

    def test_revision_cannot_be_below_baseline_or_ack_reply(self):
        docs = copy.deepcopy(self.docs)
        controller_revision(docs[2], '3')
        previous = tuple(core.encoded(x) for x in docs)
        docs[2]['metadata']['annotations'][REVISION] = '2'
        observed = tuple(core.encoded(x) for x in docs)
        for baseline, replies in ((previous, self.raws), (self.absent, previous)):
            with self.assertRaisesRegex(VerificationError, 'OBJECT_REVISION_REVERSED'):
                self.make(baseline).check(self.b, replace(self.progress(6), responses=replies), observed)

    def test_missing_or_malformed_ownership_is_unknown(self):
        for value in (None, {}, 'UNKNOWN', [{'manager': 'fake'}]):
            self.reject_change(lambda o: o['metadata'].update(managedFields=value))

    def test_ack_must_have_apply_manager(self):
        docs = copy.deepcopy(self.docs)
        docs[0]['metadata']['managedFields'][0]['operation'] = 'Update'
        raw = core.encoded(docs[0])
        with self.assertRaisesRegex(VerificationError, 'APPLY_OWNERSHIP'):
            self.r.check(self.b, replace(self.progress(1), responses=(raw,)), (raw,)+self.absent[1:])

    def test_ack_must_preserve_render_selector(self):
        doc = copy.deepcopy(self.docs[0]); doc['spec']['selector']['app'] = 'foreign'
        raw = core.encoded(doc)
        with self.assertRaisesRegex(VerificationError, 'APPLY_MISMATCH'):
            self.r.check(self.b, replace(self.progress(1), responses=(raw,)), (raw,)+self.absent[1:])

    def test_baseline_is_not_forced_to_new_image(self):
        docs = copy.deepcopy(self.docs)
        docs[2]['spec']['template']['spec']['containers'][0]['image'] = 'old.example.com/app@sha256:'+'b'*64
        raw = tuple(core.encoded(o) for o in docs)
        r = self.make(raw)
        r.check(self.b, Progress(), raw)
        r.check(self.b, self.progress(6), self.raws)

    def replicas_baseline(self, *, current=0, manager='kubectl-patch', operation='Update',
                          fields=None, version='apps/v1'):
        docs = copy.deepcopy(self.docs)
        docs[2]['spec']['replicas'] = current
        docs[2]['metadata']['managedFields'].append({
            'manager': manager, 'operation': operation, 'apiVersion': version,
            'fieldsType': 'FieldsV1',
            'fieldsV1': {'f:spec': {'f:replicas': {}}} if fields is None else fields})
        return tuple(core.encoded(o) for o in docs)

    def test_changed_replicas_owned_by_other_workflow_is_blocked_without_raw_rewrite(self):
        for manager, operation, version in (
                ('kubectl-patch', 'Update', 'apps/v1'), ('another-apply', 'Apply', 'apps/v1'),
                ('fds-deployer', 'Update', 'apps/v1'), ('fds-deployer', 'Apply', 'old/v1')):
            raw = self.replicas_baseline(manager=manager, operation=operation, version=version)
            before = tuple(raw)
            with self.subTest(manager=manager, operation=operation, version=version):
                with self.assertRaisesRegex(VerificationError, '^OBJECT_REPLICAS_OWNERSHIP_CONFLICT$'):
                    self.make(raw)
                self.assertEqual(before, raw)

    def test_same_value_shared_ownership_and_exact_apply_owner_do_not_conflict(self):
        target = self.docs[2]['spec']['replicas']
        for raw in (self.replicas_baseline(current=target),
                    self.replicas_baseline(manager='fds-deployer', operation='Apply')):
            self.make(raw).check(self.b, Progress(), raw)

    def test_other_fields_or_map_marker_do_not_mean_replicas_owned(self):
        for fields in ({}, {'f:metadata': {'f:labels': {}}},
                       {'f:spec': {'.': {}, 'f:template': {}}}):
            raw = self.replicas_baseline(fields=fields)
            self.make(raw).check(self.b, Progress(), raw)

    def test_atomic_or_unknown_spec_and_bad_replica_values_are_blocked(self):
        for fields in ({'f:spec': {}}, {'f:spec': None}):
            with self.assertRaisesRegex(VerificationError, '^OBJECT_REPLICAS_OWNERSHIP_'):
                self.make(self.replicas_baseline(fields=fields))
        for current in (None, True, -1, '0', 2**31):
            with self.subTest(current=current), self.assertRaisesRegex(
                    VerificationError, '^OBJECT_REPLICAS_INVALID$'):
                self.make(self.replicas_baseline(current=current))

    def test_baseline_and_progress_require_exact_binding(self):
        with self.assertRaisesRegex(VerificationError, 'BINDING_MISMATCH'):
            self.r.check(replace(self.b, run_id='different'), Progress(), self.absent)
        journal = json.loads(self.progress(0).journal); journal['binding_key'] = 'a'*64
        with self.assertRaises(VerificationError):
            self.r.check(self.b, Progress(core.encoded(journal)), self.absent)

    def test_stopped_and_failed_checkpoint_cannot_continue(self):
        for change in ({'status': 'STOPPED', 'error_code': 'WRITE_OUTCOME_UNKNOWN'},
                       {'checkpoint_error': 'CHECKPOINT_FAILED'}):
            journal = json.loads(self.progress(0).journal); journal.update(change)
            with self.assertRaises(VerificationError):
                self.r.check(self.b, Progress(core.encoded(journal)), self.absent)

    def test_object_set_scope_and_order_are_fixed(self):
        for raw in (self.raws[:-1], tuple(reversed(self.raws)), list(self.raws)):
            with self.assertRaises(VerificationError):
                self.r.check(self.b, self.progress(6), raw)
        self.reject_change(lambda o: o['metadata'].update(namespace='rosa-app'))

    def test_invalid_duplicate_nonfinite_or_oversize_json_rejected(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}', b'[]', b'', b'x'*(1024*1024+1)):
            with self.assertRaises(VerificationError): decode(raw)

    def test_exception_does_not_echo_sensitive_input(self):
        with self.assertRaises(VerificationError) as caught:
            self.r.check(self.b, Progress(), (b'SYNTHETIC_SECRET',)+self.absent[1:])
        self.assertNotIn('SYNTHETIC_SECRET', str(caught.exception))

    def connected(self):
        c = ContractHarness()
        r = ObjectReconciler(c.h.binding, c.h.render, c.h.contract, validator, self.absent)
        observed = dict()
        original = c.h.send
        def send(request):
            obj = original(request)
            if request.method == 'PATCH':
                key = core.identity(obj); obj = owned(obj, core.OBJECTS.index(key))
                observed[key] = core.encoded(obj)
                c.h.objects[core.path_for(key)] = copy.deepcopy(obj)
            return obj
        c.h.send = send
        def objects(request):
            key = r.check(request.binding, request.progress,
                          tuple(observed.get(k) for k in core.OBJECTS))
            return ObjectEvidence(request, key)
        c.providers.objects = objects
        return c, observed

    def test_session_six_acknowledged_writes_and_status_reads(self):
        c, _ = self.connected()
        result = c.run(finalize=False)
        self.assertEqual(result['status'], 'REVIEW_REQUIRED', result)
        self.assertEqual(len(c.h.requests), 8)
        self.assertEqual(c.storage.complete_calls, 0)

    def test_session_foreign_change_stops_next_write_and_keeps_fence(self):
        c, observed = self.connected()
        def drift(name, request):
            if name == 'producer' and len(observed) == 1:
                obj = json.loads(observed[core.OBJECTS[0]])
                obj['metadata']['uid'] = 'foreign'
                observed[core.OBJECTS[0]] = core.encoded(obj)
        c.providers.before = drift
        result = c.run()
        self.assertEqual(result['status'], 'STOPPED')
        self.assertEqual(len(c.h.requests), 1)
        self.assertEqual(c.storage.complete_calls, 0)
        self.assertIsNotNone(c.storage.active)

    def test_session_controller_revision_through_remaining_writes_and_rollout(self):
        for status, map_owned in ((False, False), (True, False), (False, True), (True, True)):
            c, observed = self.connected()
            def revise(name, request):
                if name != 'producer':
                    return
                for key in core.OBJECTS[2:4]:
                    if key in observed:
                        obj = json.loads(observed[key])
                        if REVISION not in obj['metadata'].get('annotations', {}):
                            controller_revision(obj, status=status, map_owned=map_owned)
                            observed[key] = core.encoded(obj)
                            c.h.objects[core.path_for(key)] = copy.deepcopy(obj)
            c.providers.before = revise
            result = c.run(finalize=True)
            self.assertEqual(result['status'], 'SESSION_COMPLETED', result)
            self.assertEqual(len(c.h.requests), 8)
            self.assertEqual(c.storage.complete_calls, 1)


if __name__ == '__main__':
    unittest.main()
