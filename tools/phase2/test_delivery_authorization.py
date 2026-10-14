"""Synthetic authority-provider outputs only; no actual approval/token/API."""
import unittest
from dataclasses import asdict, replace
from unittest.mock import Mock

import delivery_adapter as core
from delivery_authorization import BoundAuthorizer, VerifiedInputs
from test_delivery_adapter import Harness, SENSITIVE


class AuthorizationTests(unittest.TestCase):
    def connect(self):
        h = Harness()
        claim = Mock(binding=h.binding)
        journal = Mock(claim=claim)
        verify = Mock(return_value=VerifiedInputs(h.binding, h.binding, h.lease))
        h.authorizer = BoundAuthorizer(verify=verify, claim=claim, journal=journal, wall=lambda: h.now)
        return h, claim, journal, verify

    def test_fresh_verification_and_same_claim_checked_for_each_request(self):
        h, claim, journal, verify = self.connect()
        self.assertEqual(h.run()['status'], 'ROLLOUT_OBSERVED')
        self.assertEqual(len(h.requests), 8)
        self.assertGreater(verify.call_count, len(h.requests))
        self.assertEqual(claim.check.call_count, 2 * verify.call_count)
        self.assertEqual(journal.check.call_count, 2 * verify.call_count)

    def test_declared_json_or_bare_lease_cannot_authorize(self):
        for value in ({'approved': True}, asdict(self.connect()[3].return_value), None):
            h, _, _, verify = self.connect()
            verify.return_value = value
            self.assertEqual(h.run()['error_code'], 'AUTHORIZATION_DENIED')
            self.assertEqual(h.requests, [])
        h, _, _, verify = self.connect()
        verify.return_value = h.lease
        self.assertEqual(h.run()['error_code'], 'AUTHORIZATION_DENIED')

    def test_every_approval_and_observation_binding_field_must_match(self):
        for field in core.Binding.__dataclass_fields__:
            h, _, _, _ = self.connect()
            value = getattr(h.binding, field)
            if field == 'images':
                changed = tuple((name, image[:-64] + '9' * 64) for name, image in value)
            elif field == 'action':
                changed = 'restore'
            elif field.endswith('sha256'):
                changed = '9' * 64
            elif field == 'source_commit':
                changed = '9' * 40
            elif field == 'api_origin':
                changed = 'https://other.rosa.example.com:6443'
            elif field == 'route_host':
                changed = 'other.rosa.example.com'
            else:
                changed = 'other-identity'
            for side in ('approved', 'observed'):
                with self.subTest(field=field, side=side):
                    h, _, _, verify = self.connect()
                    verify.return_value = replace(verify.return_value, **{
                        side: replace(h.binding, **{field: changed})})
                    self.assertEqual(h.run()['error_code'], 'AUTHORIZATION_DENIED')
                    self.assertEqual(h.requests, [])

    def test_other_claim_or_other_journal_cannot_be_used(self):
        for other in ('claim', 'journal'):
            h, claim, journal, verify = self.connect()
            if other == 'claim':
                claim.binding = replace(h.binding, run_id='different-run')
            else:
                journal.claim = Mock(binding=h.binding)
            self.assertEqual(h.run()['error_code'], 'AUTHORIZATION_DENIED')
            verify.assert_not_called()
            self.assertEqual(h.requests, [])

    def test_storage_lost_during_verification_blocks_before_send(self):
        for component in ('claim', 'journal'):
            h, claim, journal, verify = self.connect()
            target = claim if component == 'claim' else journal
            target.check.side_effect = [None, RuntimeError(SENSITIVE)]
            result = h.run()
            self.assertEqual(result['error_code'], 'AUTHORIZATION_DENIED')
            self.assertNotIn(SENSITIVE, str(result))
            self.assertEqual(h.requests, [])

    def test_provider_failure_and_invalid_lease_fail_closed(self):
        changes = ({'binding_key': '9' * 64}, {'credential_fingerprint': SENSITIVE},
                   {'requested_seconds': True}, {'requested_seconds': 1800},
                   {'token_expires_at': float('nan')}, {'approval_expires_at': True},
                   {'token_expires_at': 1000}, {'approval_expires_at': 1000})
        for change in changes:
            h, _, _, verify = self.connect()
            verify.return_value = replace(verify.return_value, lease=replace(h.lease, **change))
            self.assertEqual(h.run()['error_code'], 'AUTHORIZATION_DENIED')
            self.assertEqual(h.requests, [])
        h, _, _, verify = self.connect()
        verify.side_effect = RuntimeError(SENSITIVE)
        result = h.run()
        self.assertEqual(result['error_code'], 'AUTHORIZATION_DENIED')
        self.assertNotIn(SENSITIVE, str(result))

    def test_expiry_or_silent_renewal_after_first_response_stops_next_write(self):
        for renew in (False, True):
            h, _, _, verify = self.connect()
            def after(request, response):
                if renew:
                    verify.return_value = replace(verify.return_value,
                        lease=replace(h.lease, token_expires_at=2000))
                else:
                    h.now = 1900
            h.after_send = after
            result = h.run()
            self.assertEqual(result['error_code'], 'AUTHORIZATION_DENIED')
            self.assertEqual(len(h.requests), 1)
            self.assertEqual(result['acknowledged'], ['v1/Service/transaction-api'])


if __name__ == '__main__':
    unittest.main()
