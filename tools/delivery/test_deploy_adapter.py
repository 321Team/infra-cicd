"""MOCK tests only. No credentials, network, subprocess or cluster mutations."""
import copy
import json
import unittest

import deploy_adapter as d


def fixtures():
    targets = tuple(d.Target(name, f'{i:08x}-1111-2222-3333-444444444444',
                            d.REGISTRY_PREFIX + name + '@sha256:' + str(i) * 64,
                            d.REGISTRY_PREFIX + name + '@sha256:' + str(i + 2) * 64)
                    for i, name in enumerate(d.SERVICES, 1))
    objects = {}
    for t in targets:
        objects[t.service] = {
            'apiVersion': 'apps/v1', 'kind': 'Deployment',
            'metadata': {'name': t.service, 'namespace': 'fds-app', 'uid': t.uid,
                         'resourceVersion': '11', 'generation': 3},
            'spec': {'replicas': 3, 'template': {'spec': {
                'serviceAccountName': 'existing-app-account',
                'containers': [{'name': 'sidecar', 'image': 'untouched'},
                               {'name': t.service, 'image': t.previous_image,
                                'env': [{'name': 'SENSITIVE', 'value': 'never-in-receipt'}]}]}}},
            'status': {'observedGeneration': 3, 'replicas': 3, 'updatedReplicas': 3,
                       'availableReplicas': 3, 'readyReplicas': 3}}
    return targets, objects


class Fake:
    def __init__(self, objects):
        self.objects = copy.deepcopy(objects)
        self.patches = []
        self.get_hook = None
        self.patch_hook = None
        self.gets = 0

    def get(self, name):
        self.gets += 1
        if self.get_hook:
            self.get_hook(self, name)
        return copy.deepcopy(self.objects[name])

    def patch(self, name, patch):
        self.patches.append((name, copy.deepcopy(patch)))
        if self.patch_hook:
            self.patch_hook(self, name)
        obj = self.objects[name]
        for entry in patch:
            keys = entry['path'].lstrip('/').split('/')
            parent = obj
            for key in keys[:-1]:
                parent = parent[int(key)] if isinstance(parent, list) else parent[key]
            key = int(keys[-1]) if isinstance(parent, list) else keys[-1]
            if entry['op'] == 'test' and parent[key] != entry['value']:
                raise d.Rejected()
            if entry['op'] == 'replace':
                parent[key] = entry['value']
        obj['metadata']['resourceVersion'] = str(int(obj['metadata']['resourceVersion']) + 1)
        obj['metadata']['generation'] += 1
        obj['status']['observedGeneration'] = obj['metadata']['generation']
        return copy.deepcopy(obj)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.targets, self.objects = fixtures()
        self.client = Fake(self.objects)
        self.now = 0
        self.receipts = []
        self.actions = []

    def auth(self, action):
        self.actions.append(action)
        return True

    def sleep(self, seconds):
        self.now += seconds

    def adapter(self, **kwargs):
        return d.Adapter(self.client, kwargs.pop('authorize', self.auth),
                         kwargs.pop('checkpoint', self.receipts.append), timeout=3,
                         credential_remaining_seconds=kwargs.pop('credential_remaining_seconds', lambda: 900),
                         remaining_seconds=kwargs.pop('remaining_seconds', lambda: 3600),
                         interval=1, clock=lambda: self.now, sleep=self.sleep, **kwargs)

    def run_adapter(self, rollback=False, **kwargs):
        return self.adapter(**kwargs).run(self.targets, rollback_on_failure=rollback)

    def test_two_rollouts_only_replace_named_images_and_keep_gate_unverified(self):
        result = self.run_adapter()
        self.assertEqual(result['ROLLOUT'], 'READY')
        self.assertEqual(result['DELIVERY_GATE'], 'NOT_VERIFIED')
        self.assertEqual(result['HEALTH_E2E'], 'NOT_VERIFIED')
        self.assertEqual(len(self.client.patches), 2)
        for name, patch in self.client.patches:
            self.assertEqual([p['op'] for p in patch], ['test'] * 4 + ['replace'])
            self.assertEqual(patch[-1]['path'], '/spec/template/spec/containers/1/image')
            expected = copy.deepcopy(self.objects[name]['spec'])
            expected['template']['spec']['containers'][1]['image'] = next(
                t.image for t in self.targets if t.service == name)
            self.assertEqual(self.client.objects[name]['spec'], expected)
        self.assertNotIn('never-in-receipt', json.dumps(self.receipts))

    def test_all_preflights_complete_before_first_patch(self):
        self.client.objects[d.SERVICES[1]]['metadata']['uid'] = 'wrong'
        self.assertEqual(self.run_adapter()['error_code'], 'TARGET_IDENTITY_DRIFT')
        self.assertEqual(self.client.patches, [])

    def test_unready_baseline_blocks_without_patch(self):
        self.client.objects[d.SERVICES[1]]['status']['readyReplicas'] = 2
        self.assertEqual(self.run_adapter()['error_code'], 'BASELINE_NOT_READY')
        self.assertEqual(self.client.patches, [])

    def test_paused_deployment_blocks(self):
        self.client.objects[d.SERVICES[0]]['spec']['paused'] = True
        self.assertEqual(self.run_adapter()['error_code'], 'DEPLOYMENT_PAUSED')

    def test_exact_scope_digest_and_unique_uid(self):
        for bad in (self.targets[::-1], self.targets[:1],
                    (d.Target('other', self.targets[0].uid, 'x', 'y'), self.targets[1]),
                    (d.Target(d.SERVICES[0], self.targets[0].uid, self.targets[0].previous_image,
                              d.REGISTRY_PREFIX + d.SERVICES[0] + ':latest'), self.targets[1])):
            with self.subTest(bad=bad), self.assertRaises(d.Stop):
                self.adapter().run(bad)
        self.assertEqual(self.client.patches, [])

    def test_single_use_even_after_success(self):
        adapter = self.adapter()
        adapter.run(self.targets)
        with self.assertRaisesRegex(d.Stop, 'ADAPTER_ALREADY_USED'):
            adapter.run(self.targets)
        self.assertEqual(len(self.client.patches), 2)

    def test_rv_status_updates_before_patch_are_allowed(self):
        def hook(client, name):
            obj = client.objects[name]
            obj['metadata']['resourceVersion'] = str(int(obj['metadata']['resourceVersion']) + 1)
        self.client.get_hook = hook
        self.assertEqual(self.run_adapter()['ROLLOUT'], 'READY')

    def test_race_at_patch_is_rejected_without_retry(self):
        def hook(client, name):
            client.objects[name]['metadata']['resourceVersion'] = '500'
        self.client.patch_hook = hook
        result = self.run_adapter(True)
        self.assertEqual(result['error_code'], 'PATCH_REJECTED')
        self.assertEqual(len(self.client.patches), 1)
        self.assertEqual(self.client.objects[d.SERVICES[0]]['spec'], self.objects[d.SERVICES[0]]['spec'])

    def test_spec_drift_between_preflight_and_patch_blocks(self):
        def hook(client, name):
            if client.gets == 3:
                client.objects[name]['spec']['replicas'] = 4
        self.client.get_hook = hook
        self.assertEqual(self.run_adapter()['error_code'], 'SPEC_DRIFT')
        self.assertEqual(self.client.patches, [])

    def fail_second(self):
        def hook(client, name):
            if len(client.patches) == 2:
                raise d.Rejected('secret in transport exception')
        self.client.patch_hook = hook

    def test_second_rejection_restores_first_with_separate_authority(self):
        self.fail_second()
        result = self.run_adapter(True)
        self.assertEqual(result['ROLLOUT'], 'FAILED')
        self.assertEqual(result['ROLLBACK'], 'READY_HEALTH_NOT_VERIFIED')
        self.assertIn('rollback', self.actions)
        self.assertEqual(len(self.client.patches), 3)
        for name in d.SERVICES:
            self.assertEqual(self.client.objects[name]['spec'], self.objects[name]['spec'])
        self.assertNotIn('secret in transport exception', json.dumps(result))

    def test_no_implicit_rollback_without_request(self):
        self.fail_second()
        result = self.run_adapter()
        self.assertEqual(result['ROLLBACK'], 'NOT_AUTHORIZED_OR_NOT_REQUESTED')
        self.assertEqual(len(self.client.patches), 2)

    def test_rollback_authority_denied_preserves_partial_result(self):
        self.fail_second()
        result = self.run_adapter(True, authorize=lambda action: action == 'deploy')
        self.assertEqual(result['rollback_error_code'], 'AUTHORIZATION_DENIED')
        self.assertEqual(result['ROLLBACK'], 'MANUAL_RECONCILIATION_REQUIRED')
        self.assertEqual(len(self.client.patches), 2)

    def test_rollback_refuses_external_spec_change(self):
        self.fail_second()
        def hook(client, name):
            if len(client.patches) == 2:
                client.objects[d.SERVICES[0]]['spec']['replicas'] = 5
        self.client.get_hook = hook
        result = self.run_adapter(True)
        self.assertEqual(result['rollback_error_code'], 'SPEC_DRIFT')
        self.assertEqual(len(self.client.patches), 2)

    def test_lost_patch_response_stops_all_automatic_writes(self):
        def hook(client, name):
            if len(client.patches) == 2:
                client.objects[name]['spec']['template']['spec']['containers'][1]['image'] = self.targets[1].image
                raise TimeoutError('bearer never expose')
        self.client.patch_hook = hook
        result = self.run_adapter(True)
        self.assertEqual(result['error_code'], 'PATCH_OUTCOME_UNKNOWN')
        self.assertEqual(result['ROLLBACK'], 'MANUAL_RECONCILIATION_REQUIRED')
        self.assertEqual(len(self.client.patches), 2)
        self.assertNotIn('bearer never expose', json.dumps(result))

    def test_malformed_success_response_is_unknown(self):
        self.client.patch = lambda *args: {'kind': 'Status'}
        result = self.run_adapter(True)
        self.assertEqual(result['error_code'], 'PATCH_OUTCOME_UNKNOWN')
        self.assertEqual(result['images'][d.SERVICES[0]]['deploy'], 'UNKNOWN')

    def test_readiness_timeout_then_restore(self):
        def hook(client, name):
            obj = client.objects[name]
            if len(client.patches) == 1:
                obj['status']['availableReplicas'] = 1
            else:
                obj['status']['availableReplicas'] = 3
        self.client.get_hook = hook
        result = self.run_adapter(True)
        self.assertEqual(result['error_code'], 'READINESS_TIMEOUT')
        self.assertEqual(result['ROLLBACK'], 'READY_HEALTH_NOT_VERIFIED')
        self.assertEqual(len(self.client.patches), 2)

    def test_old_generation_or_extra_replicas_cannot_be_ready(self):
        obj = copy.deepcopy(self.objects[d.SERVICES[0]])
        for field, value in [('observedGeneration', 2), ('replicas', 4),
                             ('readyReplicas', True), ('terminatingReplicas', 1)]:
            candidate = copy.deepcopy(obj)
            candidate['status'][field] = value
            with self.subTest(field=field):
                self.assertFalse(d.ready(candidate))

    def test_controller_progress_deadline_is_failure(self):
        obj = copy.deepcopy(self.objects[d.SERVICES[0]])
        obj['status']['conditions'] = [{'type': 'Progressing', 'status': 'False',
                                        'reason': 'ProgressDeadlineExceeded'}]
        with self.assertRaisesRegex(d.Stop, 'PROGRESS_DEADLINE_EXCEEDED'):
            d.ready(obj)

    def test_auth_expiry_before_first_write_blocks(self):
        calls = []
        def auth(action):
            calls.append(action)
            return len(calls) < 4
        self.assertEqual(self.run_adapter(authorize=auth)['error_code'], 'AUTHORIZATION_DENIED')
        self.assertEqual(self.client.patches, [])

    def test_checkpoint_failure_before_write_blocks(self):
        def fail(receipt):
            raise OSError('disk full')
        with self.assertRaisesRegex(d.Stop, 'CHECKPOINT_FAILED'):
            self.run_adapter(checkpoint=fail)
        self.assertEqual(self.client.patches, [])

    def test_checkpoint_failure_after_patch_stops_further_writes(self):
        def fail(receipt):
            if self.client.patches:
                raise OSError('disk full')
        with self.assertRaisesRegex(d.Stop, 'CHECKPOINT_FAILED'):
            self.run_adapter(True, checkpoint=fail)
        self.assertEqual(len(self.client.patches), 1)

    def test_pending_journal_exists_before_every_write(self):
        def hook(client, name):
            self.assertEqual(self.receipts[-1]['images'][name]['deploy'], 'PATCH_PENDING')
        self.client.patch_hook = hook
        self.run_adapter()

    def test_get_failure_is_sanitized_without_patch(self):
        def fail(*args):
            raise RuntimeError('authorization bearer secret')
        self.client.get = fail
        result = self.run_adapter()
        self.assertEqual(result['error_code'], 'GET_FAILED')
        self.assertNotIn('bearer', json.dumps(result))
        self.assertEqual(self.client.patches, [])

    def test_final_recheck_catches_first_target_losing_readiness(self):
        def hook(client, name):
            if len(client.patches) == 2 and name == d.SERVICES[0]:
                client.objects[name]['status']['readyReplicas'] = 2
        self.client.get_hook = hook
        self.assertEqual(self.run_adapter()['error_code'], 'FINAL_READINESS_LOST')

    def test_both_applied_targets_restore_in_reverse_order(self):
        def hook(client, name):
            obj = client.objects[name]
            obj['status']['availableReplicas'] = 1 if len(client.patches) == 2 else 3
        self.client.get_hook = hook
        result = self.run_adapter(True)
        self.assertEqual(result['ROLLBACK'], 'READY_HEALTH_NOT_VERIFIED')
        self.assertEqual([name for name, _ in self.client.patches],
                         [*d.SERVICES, *reversed(d.SERVICES)])
        for name in d.SERVICES:
            self.assertEqual(self.client.objects[name]['spec'], self.objects[name]['spec'])

    def test_rollback_patch_rejected_preserves_failure(self):
        def hook(client, name):
            if len(client.patches) >= 2:
                raise d.Rejected()
        self.client.patch_hook = hook
        result = self.run_adapter(True)
        self.assertEqual(result['rollback_error_code'], 'PATCH_REJECTED')
        self.assertEqual(result['ROLLBACK'], 'MANUAL_RECONCILIATION_REQUIRED')

    def test_rollback_detects_change_and_revert_via_generation(self):
        self.fail_second()
        def hook(client, name):
            if len(client.patches) == 2:
                client.objects[name]['metadata']['generation'] += 2
        self.client.get_hook = hook
        result = self.run_adapter(True)
        self.assertEqual(result['rollback_error_code'], 'GENERATION_DRIFT')
        self.assertEqual(len(self.client.patches), 2)

    def test_opaque_resource_version_is_preserved_in_patch(self):
        obj = self.objects[d.SERVICES[0]]
        obj['metadata']['resourceVersion'] = 'opaque-revision'
        patch = d.image_patch(obj, self.targets[0], self.targets[0].previous_image,
                              self.targets[0].image)
        self.assertEqual(patch[1]['value'], 'opaque-revision')


    def test_polling_authority_loss_never_triggers_rollback(self):
        def auth(action):
            return action == 'rollback' or not self.client.patches
        result = self.run_adapter(True, authorize=auth)
        self.assertEqual(result['error_code'], 'AUTHORIZATION_DENIED')
        self.assertEqual(result['ROLLBACK'], 'MANUAL_RECONCILIATION_REQUIRED')
        self.assertEqual(len(self.client.patches), 1)
        self.assertEqual(result['images'][d.SERVICES[0]]['deploy'], 'APPLIED')

    def test_denied_before_patch_is_recorded_as_not_sent(self):
        calls = []
        def auth(action):
            calls.append(action)
            return len(calls) < 4
        result = self.run_adapter(True, authorize=auth)
        self.assertEqual(self.client.patches, [])
        self.assertEqual(result['images'][d.SERVICES[0]]['deploy'], 'NOT_SENT_AUTH_DENIED')

    def test_start_budget_rejects_short_invalid_and_nonfinite_values(self):
        for remaining in (35, -1, True, None, '3600', float('nan'), float('inf')):
            with self.subTest(remaining=remaining):
                result = self.run_adapter(remaining_seconds=lambda: remaining)
                self.assertIn(result['error_code'], ('AUTH_WINDOW_TOO_SHORT', 'AUTH_WINDOW_INVALID'))
                self.assertEqual(self.client.patches, [])

    def test_start_budget_exact_boundary_allowed(self):
        self.assertEqual(self.run_adapter(remaining_seconds=lambda: 36)['ROLLOUT'], 'READY')

    def test_journal_delay_rechecks_window_before_first_patch(self):
        def checkpoint(receipt):
            self.receipts.append(receipt)
            if any(item['deploy'] == 'PATCH_PENDING' for item in receipt['images'].values()):
                self.now = 1
        result = self.run_adapter(checkpoint=checkpoint, remaining_seconds=lambda: 36 - self.now)
        self.assertEqual(result['error_code'], 'AUTH_WINDOW_TOO_SHORT')
        self.assertEqual(result['images'][d.SERVICES[0]]['deploy'], 'NOT_SENT_AUTH_WINDOW')
        self.assertEqual(self.client.patches, [])

    def test_authority_loss_before_second_patch_does_not_rollback_first(self):
        calls = []
        def auth(action):
            calls.append(action)
            return len(calls) < 7
        result = self.run_adapter(True, authorize=auth)
        self.assertEqual(result['error_code'], 'AUTHORIZATION_DENIED')
        self.assertEqual(len(self.client.patches), 1)
        self.assertEqual(result['images'][d.SERVICES[1]]['deploy'], 'NOT_SENT_AUTH_DENIED')
        self.assertEqual(result['ROLLBACK'], 'MANUAL_RECONCILIATION_REQUIRED')

    def test_window_provider_exception_is_sanitized(self):
        def fail():
            raise ValueError('secret provider detail')
        result = self.run_adapter(remaining_seconds=fail)
        self.assertEqual(result['error_code'], 'AUTH_WINDOW_UNAVAILABLE')
        self.assertNotIn('secret provider detail', json.dumps(result))
        self.assertEqual(self.client.patches, [])


    def test_short_token_window_blocks_despite_long_approval(self):
        result = self.run_adapter(credential_remaining_seconds=lambda: 35)
        self.assertEqual(result['error_code'], 'CREDENTIAL_WINDOW_TOO_SHORT')
        self.assertEqual(self.client.patches, [])

    def test_exact_token_start_window_allowed(self):
        result = self.run_adapter(credential_remaining_seconds=lambda: 36)
        self.assertEqual(result['ROLLOUT'], 'READY')

    def test_token_expiry_during_polling_stops_without_rollback(self):
        def lifetime():
            return 0 if self.client.patches else 900
        result = self.run_adapter(True, credential_remaining_seconds=lifetime)
        self.assertEqual(result['error_code'], 'AUTHORIZATION_DENIED')
        self.assertEqual(result['ROLLBACK'], 'MANUAL_RECONCILIATION_REQUIRED')
        self.assertEqual(len(self.client.patches), 1)

    def test_token_expiry_while_pending_records_not_sent(self):
        expired = False
        def checkpoint(receipt):
            nonlocal expired
            self.receipts.append(receipt)
            expired = any(x['deploy'] == 'PATCH_PENDING' for x in receipt['images'].values())
        result = self.run_adapter(checkpoint=checkpoint,
                                  credential_remaining_seconds=lambda: 0 if expired else 900)
        self.assertEqual(result['images'][d.SERVICES[0]]['deploy'], 'NOT_SENT_AUTH_DENIED')
        self.assertEqual(self.client.patches, [])

    def test_invalid_token_lifetime_fails_closed(self):
        for value in (None, True, '3600', float('nan'), float('inf'), -1):
            with self.subTest(value=value):
                result = self.run_adapter(credential_remaining_seconds=lambda: value)
                self.assertEqual(result['error_code'], 'AUTHORIZATION_DENIED')
                self.assertEqual(self.client.patches, [])

    def test_token_provider_failure_does_not_leak_exception(self):
        def fail():
            raise ValueError('secret token contents')
        result = self.run_adapter(credential_remaining_seconds=fail)
        self.assertEqual(result['error_code'], 'AUTHORIZATION_DENIED')
        self.assertNotIn('secret token contents', json.dumps(result))
        self.assertEqual(self.client.patches, [])

    def test_token_does_not_replace_short_approval(self):
        result = self.run_adapter(remaining_seconds=lambda: 35,
                                  credential_remaining_seconds=lambda: 900)
        self.assertEqual(result['error_code'], 'AUTH_WINDOW_TOO_SHORT')
        self.assertEqual(self.client.patches, [])

    def test_rollback_requires_live_credential(self):
        self.fail_second()
        result = self.run_adapter(True, credential_remaining_seconds=lambda:
                                  0 if len(self.client.patches) >= 2 else 900)
        self.assertEqual(result['rollback_error_code'], 'AUTHORIZATION_DENIED')
        self.assertEqual(len(self.client.patches), 2)


if __name__ == '__main__':
    unittest.main()
