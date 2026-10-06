"""Synthetic JWT/TokenReview tests; no signatures, issuer, token or server used."""
import base64
import copy
import datetime as dt
import json
import unittest

import deploy_adapter as core
import deploy_binding as b
import deploy_credential as c
from test_deploy_adapter import Fake, fixtures
from test_deploy_binding import NOW, encode

SA_UID = '11111111-2222-3333-4444-555555555555'


def jwt(claims, header=None):
    parts = [encode(header or {'alg': 'RS256', 'typ': 'JWT'}), encode(claims), b'MOCK_SIGNATURE']
    return '.'.join(base64.urlsafe_b64encode(x).decode().rstrip('=') for x in parts)


def fixture():
    issued = int(NOW.timestamp()) - 10
    claims = {
        'iss': b.AUDIENCE, 'sub': c.SUBJECT, 'aud': [b.AUDIENCE],
        'iat': issued, 'nbf': issued, 'exp': issued + 3600,
        'kubernetes.io': {'namespace': 'ci-cd',
                          'serviceaccount': {'name': 'cicd-runner-sa', 'uid': SA_UID}},
    }
    token = jwt(claims)
    response = {
        'apiVersion': 'authentication.k8s.io/v1', 'kind': 'TokenReview',
        'spec': {'token': token, 'audiences': [b.AUDIENCE]},
        'status': {'authenticated': True, 'audiences': [b.AUDIENCE],
                   'user': {'username': c.SUBJECT, 'uid': SA_UID, 'groups': sorted(c.GROUPS)}},
    }
    return claims, token, response


class Reviewer:
    def __init__(self, obj):
        self.obj, self.calls, self.hook = copy.deepcopy(obj), [], None

    def review(self, token, audience, timeout):
        self.calls.append((token, audience, timeout))
        if self.hook:
            self.hook()
        return copy.deepcopy(self.obj)


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.claims, self.raw, response = fixture()
        self.reviewer = Reviewer(response)
        self.wall, self.mono = NOW, 0

    def credential(self, **kwargs):
        return c.Credential(
            kwargs.pop('token', self.raw), kwargs.pop('uid', SA_UID),
            kwargs.pop('reviewer', self.reviewer),
            now=kwargs.pop('now', lambda: self.wall),
            clock=kwargs.pop('clock', lambda: self.mono), **kwargs)

    def change_claims(self, **values):
        self.claims.update(values)
        self.raw = jwt(self.claims)
        self.reviewer.obj['spec']['token'] = self.raw

    def test_fresh_token_review_binds_exact_credential_subject_uid_and_audience(self):
        credential = self.credential()
        self.assertEqual(credential.token(), self.raw)
        self.assertEqual(credential.remaining(), 3590)
        self.assertEqual(self.reviewer.calls, [(self.raw, b.AUDIENCE, 10)])
        observed = credential.observation()
        self.assertEqual(observed['service_account_uid'], SA_UID)
        self.assertEqual(observed['authentication_basis'], 'CALLER_AUTHENTICATED_FRESH_TOKEN_REVIEW')
        self.assertEqual(observed['jwt_signature_check'], 'NOT_PERFORMED_LOCALLY')
        self.assertEqual(observed['runner_token_authentication'], 'NOT_VERIFIED')
        self.assertEqual(observed['DELIVERY_GATE'], 'NOT_VERIFIED')

    def test_decode_alone_does_not_create_credential(self):
        with self.assertRaisesRegex(c.CredentialError, 'CREDENTIAL_PROVIDER_REQUIRED'):
            self.credential(reviewer=None)

    def test_different_token_or_audience_in_review_does_not_bind(self):
        for field, value in (('token', 'other'), ('audiences', ['other'])):
            obj = copy.deepcopy(self.reviewer.obj)
            obj['spec'][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(
                    c.CredentialError, 'TOKEN_REVIEW_BINDING_MISMATCH'):
                self.credential(reviewer=Reviewer(obj))

    def test_denied_error_or_non_boolean_authentication_is_rejected(self):
        for values in ({'authenticated': False}, {'authenticated': 1},
                       {'authenticated': True, 'error': 'PRIVATE_TOKEN'}):
            obj = copy.deepcopy(self.reviewer.obj)
            obj['status'].update(values)
            with self.subTest(values=values), self.assertRaisesRegex(
                    c.CredentialError, '^TOKEN_REVIEW_DENIED$'):
                self.credential(reviewer=Reviewer(obj))

    def test_explicit_review_audience_is_required_even_when_authenticated(self):
        for values in ([], ['other'], [b.AUDIENCE, 'other'], None):
            obj = copy.deepcopy(self.reviewer.obj)
            obj['status']['audiences'] = values
            with self.subTest(values=values), self.assertRaises(c.CredentialError):
                self.credential(reviewer=Reviewer(obj))

    def test_reviewed_wrong_service_account_or_uid_is_rejected(self):
        for field, value in (('username', 'system:serviceaccount:default:other'), ('uid', 'other')):
            obj = copy.deepcopy(self.reviewer.obj)
            obj['status']['user'][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(
                    c.CredentialError, '^TOKEN_REVIEW_IDENTITY_MISMATCH$'):
                self.credential(reviewer=Reviewer(obj))

    def test_elevated_duplicate_or_missing_groups_are_not_accepted(self):
        for groups in (sorted(c.GROUPS) + ['system:masters'], ['system:authenticated'],
                       ['system:authenticated'] * 3, 'system:authenticated'):
            obj = copy.deepcopy(self.reviewer.obj)
            obj['status']['user']['groups'] = groups
            with self.subTest(groups=groups), self.assertRaisesRegex(
                    c.CredentialError, '^TOKEN_REVIEW_GROUPS_INVALID$'):
                self.credential(reviewer=Reviewer(obj))

    def test_wrong_kind_and_missing_review_fields_fail_closed(self):
        for obj in ({}, {'kind': 'TokenReview'}, [], None):
            with self.subTest(obj=obj), self.assertRaisesRegex(
                    c.CredentialError, '^TOKEN_REVIEW_INVALID$'):
                self.credential(reviewer=Reviewer(obj))

    def test_backend_exception_is_sanitized_and_never_becomes_valid(self):
        self.reviewer.hook = lambda: (_ for _ in ()).throw(ValueError(self.raw))
        with self.assertRaisesRegex(c.CredentialError, '^TOKEN_REVIEW_FAILED$'):
            self.credential()

    def test_claim_issuer_subject_or_audience_mismatch_blocks_before_review(self):
        for field, value in (('iss', 'https://other'), ('sub', 'other'),
                             ('aud', 'other'), ('aud', [b.AUDIENCE, 'other'])):
            claims = copy.deepcopy(self.claims)
            claims[field] = value
            with self.subTest(field=field), self.assertRaises(c.CredentialError):
                self.credential(token=jwt(claims))
        self.assertEqual(self.reviewer.calls, [])

    def test_expected_uid_namespace_and_account_claim_are_required(self):
        for field, value in (('namespace', 'default'), ('serviceaccount', {
                'name': 'cicd-runner-sa', 'uid': 'other'})):
            claims = copy.deepcopy(self.claims)
            claims['kubernetes.io'][field] = value
            with self.subTest(field=field), self.assertRaises(c.CredentialError):
                self.credential(token=jwt(claims))
        for uid in ('old', None, '00000000-0000-0000-0000-000000000000'):
            with self.subTest(uid=uid), self.assertRaises(c.CredentialError):
                self.credential(uid=uid)
        self.assertEqual(self.reviewer.calls, [])

    def test_missing_boolean_nonfinite_and_out_of_range_time_claims_are_rejected(self):
        for field in ('exp', 'iat', 'nbf'):
            for value in (None, True, '3600', float('inf'), 10 ** 50):
                claims = copy.deepcopy(self.claims)
                claims[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(c.CredentialError):
                    self.credential(token=jwt(claims))
        self.assertEqual(self.reviewer.calls, [])

    def test_requested_ttl_does_not_replace_expiry_and_overlong_actual_token_is_rejected(self):
        self.change_claims(exp=self.claims['iat'] + 3601)
        with self.assertRaisesRegex(c.CredentialError, '^JWT_LIFETIME_INVALID$'):
            self.credential()
        self.change_claims(exp=int(NOW.timestamp()) - 1)
        with self.assertRaisesRegex(c.CredentialError, '^CREDENTIAL_EXPIRED$'):
            self.credential()
        self.assertEqual(self.reviewer.calls, [])

    def test_not_yet_valid_claims_block_without_remote_review(self):
        future = int(NOW.timestamp()) + 1
        self.change_claims(iat=future, nbf=future, exp=future + 3600)
        with self.assertRaisesRegex(c.CredentialError, '^CREDENTIAL_NOT_YET_VALID$'):
            self.credential()
        self.assertEqual(self.reviewer.calls, [])

    def test_unsigned_malformed_noncanonical_and_duplicate_jwt_are_rejected(self):
        for token in ('a.b', 'a.b.c', self.raw + '\n', None,
                      jwt(self.claims, {'alg': 'none'})):
            with self.subTest(token=repr(token)[:10]), self.assertRaises(c.CredentialError):
                self.credential(token=token)
        duplicate = b'{"exp":1,"exp":2}'
        encoded = base64.urlsafe_b64encode(duplicate).decode().rstrip('=')
        token = self.raw.split('.')[0] + '.' + encoded + '.' + self.raw.split('.')[2]
        with self.assertRaisesRegex(c.CredentialError, '^JWT_CLAIMS_INVALID$'):
            self.credential(token=token)
        self.assertEqual(self.reviewer.calls, [])

    def test_time_spent_in_review_is_not_added_back_to_lifetime(self):
        self.reviewer.hook = lambda: setattr(self, 'mono', 9)
        credential = self.credential()
        self.assertEqual(credential.remaining(), 3581)

    def test_review_overruns_budget_or_expires_before_completion(self):
        self.reviewer.hook = lambda: setattr(self, 'mono', 11)
        with self.assertRaisesRegex(c.CredentialError, '^TOKEN_REVIEW_DEADLINE_EXCEEDED$'):
            self.credential()
        self.mono = 0
        self.reviewer.hook = lambda: setattr(self, 'wall', NOW + dt.timedelta(hours=1))
        with self.assertRaisesRegex(c.CredentialError, '^CREDENTIAL_EXPIRED$'):
            self.credential()

    def test_remaining_decreases_monotonically_despite_backward_wall_clock(self):
        credential = self.credential()
        self.mono = 20
        self.wall = NOW - dt.timedelta(hours=2)
        self.assertEqual(credential.remaining(), 3570)
        self.mono = 3590
        self.assertEqual(credential.remaining(), 0)
        with self.assertRaisesRegex(c.CredentialError, '^CREDENTIAL_UNAVAILABLE$'):
            credential.token()

    def test_clock_failure_or_backward_monotonic_drops_reference(self):
        credential = self.credential()
        self.mono = 5
        credential.remaining()
        self.mono = 4
        with self.assertRaisesRegex(c.CredentialError, '^CREDENTIAL_WINDOW_UNAVAILABLE$'):
            credential.remaining()
        self.assertTrue(credential.closed)
        self.assertEqual(credential.remaining(), 0)

    def test_closed_credential_is_not_refreshed_or_reusable(self):
        credential = self.credential()
        credential.close()
        credential.close()
        self.assertEqual(credential.remaining(), 0)
        with self.assertRaises(c.CredentialError):
            credential.token()
        with self.assertRaises(c.CredentialError):
            credential.__enter__()
        self.assertEqual(len(self.reviewer.calls), 1)

    def test_public_closed_flag_cannot_restore_dropped_token(self):
        credential = self.credential()
        credential.close()
        credential.closed = False  # Defensive misuse case from PR58 review.
        with self.assertRaisesRegex(c.CredentialError, '^CREDENTIAL_UNAVAILABLE$'):
            credential.token()

    def test_exception_exit_drops_local_reference_but_does_not_claim_server_revocation(self):
        credential = self.credential()
        with self.assertRaises(ValueError):
            with credential:
                raise ValueError('stop')
        observed = credential.observation()
        self.assertEqual(observed['local_credential_cleanup'], 'LOCAL_REFERENCE_DROPPED')
        self.assertEqual(observed['server_revocation'], 'NOT_PERFORMED')
        self.assertEqual(credential.remaining(), 0)

    def test_repr_observation_and_errors_do_not_serialize_token(self):
        credential = self.credential()
        output = repr(credential) + str(credential) + json.dumps(credential.observation())
        self.assertNotIn(self.raw, output)
        self.assertNotIn('MOCK_SIGNATURE', output)
        observed = credential.observation()
        observed['subject'] = 'changed'
        self.assertEqual(credential.observation()['subject'], c.SUBJECT)


class AdapterCredentialTests(unittest.TestCase):
    def setUp(self):
        _, token, result = fixture()
        self.mono = 0
        self.credential = c.Credential(token, SA_UID, Reviewer(result),
                                      now=lambda: NOW, clock=lambda: self.mono)
        self.targets, objects = fixtures()
        self.client = Fake(objects)

    def adapter(self, checkpoint):
        return core.Adapter(
            self.client, lambda _: True, checkpoint,
            remaining_seconds=lambda: 3600,
            credential_remaining_seconds=self.credential.remaining,
            timeout=3, interval=1, clock=lambda: self.mono)

    def test_close_after_pending_checkpoint_blocks_patch_without_recovery_write(self):
        def checkpoint(receipt):
            if any(item['deploy'] == 'PATCH_PENDING' for item in receipt['images'].values()):
                self.credential.close()
        result = self.adapter(checkpoint).run(self.targets, rollback_on_failure=True)
        self.assertEqual(self.client.patches, [])
        self.assertEqual(result['error_code'], 'AUTHORIZATION_DENIED')
        self.assertEqual(result['ROLLBACK'], 'MANUAL_RECONCILIATION_REQUIRED')

    def test_actual_expiry_after_first_patch_stops_second_patch_and_rollback(self):
        self.client.patch_hook = lambda *_: setattr(self, 'mono', 3590)
        result = self.adapter(lambda _: None).run(self.targets, rollback_on_failure=True)
        self.assertEqual(len(self.client.patches), 1)
        self.assertEqual(result['error_code'], 'AUTHORIZATION_DENIED')
        self.assertEqual(result['ROLLBACK'], 'MANUAL_RECONCILIATION_REQUIRED')
        self.assertEqual(result['DELIVERY_GATE'], 'NOT_VERIFIED')
        self.assertTrue(self.credential.closed)
