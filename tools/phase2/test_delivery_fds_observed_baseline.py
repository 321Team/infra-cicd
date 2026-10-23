"""SYNTHETIC six-object bytes/grants; fixed public image profile only.

Archive hash/root are substituted at the reviewed site-code boundary for unit
fixtures. No private actual archive, approval, API, credential or Runtime here.
"""
from dataclasses import replace
import io
import contextlib
import json
import unittest
from unittest.mock import patch

import delivery_adapter as core
import delivery_fds_observed_baseline as policy
import delivery_fds_operating as operating
import delivery_job as job
import delivery_job_site as declaration
from delivery_fds_baseline_archive import BaselineArchiveError
import test_delivery_fds_baseline_archive as fixture


class ObservedBaselineSelectionTests(unittest.TestCase):
    def test_selected_previous_matches_independent_adopted_supply_profile(self):
        # PR129 review follow-up: do not build this expectation from policy.
        # Profile literals are maintained separately from the previous choice.
        import delivery_fds_site_inputs as source
        self.assertEqual(source.IMAGES, policy.PREVIOUS_IMAGES)

    def setUp(self):
        self.f = fixture.BaselineArchiveTests('runTest'); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        data = dict(self.f.data)
        for name, raw in data.items():
            if name.startswith('deployment-'):
                obj = json.loads(raw)
                obj['spec']['template']['spec']['containers'][0]['image'] = dict(
                    policy.PREVIOUS_IMAGES)[obj['metadata']['name']]
                data[name] = core.encoded(obj) + b'\n'
        self.raw = fixture.archive(data)
        self.objects = tuple(data[name] for name in fixture.reader.OBJECT_FILES)
        self.plan = replace(self.f.plan, producer=replace(self.f.plan.producer,
            facts=replace(self.f.plan.producer.facts, previous_images=None)))
        for name, value in (('SNAPSHOT_SHA256', core.digest(self.raw)),
                            ('SNAPSHOT_ROOT', fixture.ROOT)):
            p = patch.object(policy, name, value); p.start(); self.addCleanup(p.stop)

    def connect(self, **kw):
        return policy.connect_selected_baseline(self.f.f.config, self.plan,
            **(dict(received_archive=self.raw, grant=self.f.f.baseline.grant) | kw))

    def test_explicit_selection_preserves_raw_owner_spec_and_all_unrelated_facts(self):
        value = self.connect()
        self.assertIsNone(self.plan.baseline)
        self.assertIsNone(self.plan.producer.facts.previous_images)
        self.assertEqual(self.objects, value.baseline.objects)
        self.assertIs(value.baseline.grant, self.f.f.baseline.grant)
        self.assertEqual(policy.PREVIOUS_IMAGES, value.producer.facts.previous_images)
        self.assertEqual(self.plan.producer.facts, replace(value.producer.facts, previous_images=None))
        for field in ('approval', 'issuer', 'subject'):
            self.assertIs(getattr(self.plan, field), getattr(value, field))
        self.assertIs(self.plan.producer.grant, value.producer.grant)
        for raw in value.baseline.objects:
            obj = json.loads(raw)
            if obj['kind'] == 'Deployment': self.assertEqual(0, obj['spec']['replicas'])

    def test_same_previous_is_explicitly_accepted_but_different_previous_never_replaced(self):
        self.plan = replace(self.plan, producer=replace(self.plan.producer,
            facts=replace(self.plan.producer.facts, previous_images=policy.PREVIOUS_IMAGES)))
        self.assertEqual(policy.PREVIOUS_IMAGES, self.connect().producer.facts.previous_images)
        self.plan = replace(self.plan, producer=replace(self.plan.producer,
            facts=replace(self.plan.producer.facts, previous_images=self.f.f.previous)))
        with self.assertRaises(BaselineArchiveError): self.connect()
        self.assertEqual(self.f.f.previous, self.plan.producer.facts.previous_images)

    def test_foreign_snapshot_or_plain_grant_cannot_be_adopted(self):
        for args in ({'received_archive':self.raw+b'CHANGED'}, {'grant':{'approved':True}}):
            with self.assertRaisesRegex(BaselineArchiveError, 'FDS_OBSERVED_BASELINE_REJECTED'):
                self.connect(**args)
        with self.assertRaises(TypeError): self.connect(expected_sha256='0'*64)
        self.assertIsNone(self.plan.baseline)

    def test_foreign_profile_or_existing_baseline_remains_rejected(self):
        original = self.plan
        self.plan = replace(original, producer=replace(original.producer,
            facts=replace(original.producer.facts, source_commit='0'*40)))
        with self.assertRaises(BaselineArchiveError): self.connect()
        self.plan = replace(original, baseline=self.f.f.baseline)
        with self.assertRaises(BaselineArchiveError): self.connect()

    def test_status_selection_is_not_fresh_evidence_force_permission_or_runtime_gate(self):
        status = policy.selection_status()
        self.assertFalse(status['current_live_checked'])
        self.assertFalse(status['rollback_verified'])
        self.assertFalse(status['execution_authorized'])
        self.assertFalse(status['force_authorized'])
        self.assertEqual('SSA_REPLICAS_CONFLICT_PENDING', status['write_issue'])
        status['previous_images'].clear()
        self.assertEqual(2, len(policy.selection_status()['previous_images']))
        self.assertIs(operating.connect_baseline, policy.connect_selected_baseline)
        with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(2, job.main(['status']))
        self.assertIsNone(declaration.SITE)


if __name__ == '__main__': unittest.main()
