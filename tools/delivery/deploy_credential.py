"""Injected native ServiceAccount JWT lifetime and local cleanup contract.

A reviewed caller must supply a fresh TokenReview backend over authenticated,
pinned Kubernetes TLS. This module neither implements that backend nor issues,
stores, refreshes or revokes a token. Decoding alone never creates a Credential.
"""
import base64
import copy
import datetime as dt
import math
import re
import time

import deploy_binding as b

SUBJECT = 'system:serviceaccount:ci-cd:cicd-runner-sa'
GROUPS = {'system:serviceaccounts', 'system:serviceaccounts:ci-cd', 'system:authenticated'}
UID = re.compile(r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}')


class CredentialError(RuntimeError):
    """Static codes only; never propagate token or backend response text."""


def need(ok, code):
    if not ok:
        raise CredentialError(code)


def segment(raw):
    need(type(raw) is str and 0 < len(raw) <= 16384 and
         re.fullmatch(r'[A-Za-z0-9_-]+', raw), 'JWT_ENCODING_INVALID')
    try:
        decoded = base64.b64decode(raw + '=' * (-len(raw) % 4), altchars=b'-_', validate=True)
        need(base64.urlsafe_b64encode(decoded).decode().rstrip('=') == raw,
             'JWT_ENCODING_INVALID')
        return decoded
    except CredentialError:
        raise
    except Exception:
        raise CredentialError('JWT_ENCODING_INVALID') from None


def claims_checked(token, expected_uid):
    need(type(token) is str and 0 < len(token) <= 16384, 'CREDENTIAL_INVALID')
    need(type(expected_uid) is str and UID.fullmatch(expected_uid), 'SA_UID_INVALID')
    parts = token.split('.')
    need(len(parts) == 3, 'JWT_ENCODING_INVALID')
    try:
        header, claims = b.read(segment(parts[0])), b.read(segment(parts[1]))
        need(header.get('alg') in ('RS256', 'ES256') and bool(segment(parts[2])),
             'JWT_ALGORITHM_UNSUPPORTED')
        need(claims.get('iss') == b.AUDIENCE and claims.get('sub') == SUBJECT,
             'JWT_IDENTITY_MISMATCH')
        need(claims.get('aud') == [b.AUDIENCE], 'JWT_AUDIENCE_MISMATCH')
        for field in ('exp', 'iat', 'nbf'):
            need(type(claims.get(field)) is int and 0 < claims[field] <= 253402300799,
                 'JWT_TIME_INVALID')
        need(claims['nbf'] <= claims['iat'] < claims['exp']
             and claims['exp'] - claims['iat'] <= b.gate.TOKEN_MAX_SECONDS,
             'JWT_LIFETIME_INVALID')
        kubernetes = claims['kubernetes.io']
        account = kubernetes['serviceaccount']
        need(kubernetes['namespace'] == 'ci-cd' and account['name'] == 'cicd-runner-sa'
             and account['uid'] == expected_uid, 'JWT_SA_MISMATCH')
        return claims
    except CredentialError:
        raise
    except Exception:
        raise CredentialError('JWT_CLAIMS_INVALID') from None


def review_checked(result, token, uid):
    # The backend, not this dict validator, must authenticate the API response.
    try:
        need(type(result) is dict and result.get('apiVersion') == 'authentication.k8s.io/v1'
             and result.get('kind') == 'TokenReview', 'TOKEN_REVIEW_INVALID')
        spec, status = result['spec'], result['status']
        need(spec.get('token') == token and spec.get('audiences') == [b.AUDIENCE],
             'TOKEN_REVIEW_BINDING_MISMATCH')
        need(status.get('authenticated') is True and not status.get('error'),
             'TOKEN_REVIEW_DENIED')
        need(status.get('audiences') == [b.AUDIENCE], 'TOKEN_REVIEW_AUDIENCE_MISMATCH')
        user = status['user']
        need(user.get('username') == SUBJECT and user.get('uid') == uid,
             'TOKEN_REVIEW_IDENTITY_MISMATCH')
        groups = user.get('groups')
        need(type(groups) is list and all(type(x) is str for x in groups)
             and len(groups) == len(GROUPS) and set(groups) == GROUPS,
             'TOKEN_REVIEW_GROUPS_INVALID')
    except CredentialError:
        raise
    except Exception:
        raise CredentialError('TOKEN_REVIEW_INVALID') from None


class Credential:
    """Single injected credential. close() drops only our local reference."""
    __slots__ = ('_token', '_expiry', '_mono_expiry', '_last_clock', '_now',
                 '_clock', '_metadata', 'closed')

    def __init__(self, token, expected_sa_uid, reviewer, *,
                 now=lambda: dt.datetime.now(dt.timezone.utc), clock=time.monotonic,
                 review_timeout=10):
        self._token, self.closed = None, True
        self._now, self._clock = now, clock
        need(callable(now) and callable(clock) and
             callable(getattr(reviewer, 'review', None)), 'CREDENTIAL_PROVIDER_REQUIRED')
        need(type(review_timeout) in (int, float) and math.isfinite(review_timeout)
             and 0 < review_timeout <= 10, 'TOKEN_REVIEW_BUDGET_INVALID')
        claims = claims_checked(token, expected_sa_uid)
        current, initial = self.point()
        need(claims['nbf'] <= current.timestamp() and claims['iat'] <= current.timestamp(),
             'CREDENTIAL_NOT_YET_VALID')
        self._expiry = dt.datetime.fromtimestamp(claims['exp'], dt.timezone.utc)
        lifetime = (self._expiry - current).total_seconds()
        need(lifetime > 0, 'CREDENTIAL_EXPIRED')
        budget = min(review_timeout, lifetime)
        # This initial monotonic bound is never reset after a delayed review.
        self._mono_expiry, self._last_clock = initial + lifetime, initial
        try:
            result = reviewer.review(token, b.AUDIENCE, budget)
        except Exception:
            raise CredentialError('TOKEN_REVIEW_FAILED') from None
        review_checked(result, token, expected_sa_uid)
        checked_at, finished = self.point()
        need(initial <= finished <= initial + budget, 'TOKEN_REVIEW_DEADLINE_EXCEEDED')
        need(min((self._expiry - checked_at).total_seconds(), self._mono_expiry - finished) > 0,
             'CREDENTIAL_EXPIRED')
        self._metadata = {
            'schema': 'P1-CICD-credential-observation-1',
            'subject': SUBJECT, 'service_account_uid': expected_sa_uid,
            'issuer': b.AUDIENCE, 'audience': b.AUDIENCE,
            'issued_at': dt.datetime.fromtimestamp(claims['iat'], dt.timezone.utc).isoformat(),
            'expires_at': self._expiry.isoformat(), 'checked_at': checked_at.isoformat(),
            'authentication_basis': 'CALLER_AUTHENTICATED_FRESH_TOKEN_REVIEW',
            'jwt_signature_check': 'NOT_PERFORMED_LOCALLY',
            'runner_token_authentication': 'NOT_VERIFIED',
            'server_revocation': 'NOT_PERFORMED', 'DELIVERY_GATE': 'NOT_VERIFIED',
        }
        self._last_clock, self._token, self.closed = finished, token, False

    def point(self):
        try:
            wall, mono = self._now(), self._clock()
            need(isinstance(wall, dt.datetime) and wall.tzinfo is not None
                 and wall.utcoffset() is not None, 'CREDENTIAL_TIME_INVALID')
            need(type(mono) in (int, float) and math.isfinite(mono),
                 'CREDENTIAL_CLOCK_INVALID')
            return wall.astimezone(dt.timezone.utc), mono
        except CredentialError:
            raise
        except Exception:
            raise CredentialError('CREDENTIAL_CLOCK_UNAVAILABLE') from None

    def remaining(self):
        if self.closed or self._token is None:
            return 0
        try:
            wall, mono = self.point()
            need(mono >= self._last_clock, 'CREDENTIAL_CLOCK_BACKWARD')
            self._last_clock = mono
            remaining = max(0, min((self._expiry - wall).total_seconds(), self._mono_expiry - mono))
            if remaining == 0:
                self.close()
            return remaining
        except Exception:
            self.close()
            raise CredentialError('CREDENTIAL_WINDOW_UNAVAILABLE') from None

    def token(self):
        need(self._token is not None and self.remaining() > 0, 'CREDENTIAL_UNAVAILABLE')
        return self._token

    def observation(self):
        result = copy.deepcopy(self._metadata)
        result['local_credential_cleanup'] = (
            'LOCAL_REFERENCE_DROPPED' if self.closed else 'NOT_PERFORMED')
        return result

    def close(self):
        self._token, self.closed = None, True

    def __repr__(self):
        return '<Credential closed=' + str(self.closed) + '>'

    def __enter__(self):
        need(self.remaining() > 0, 'CREDENTIAL_UNAVAILABLE')
        return self

    def __exit__(self, *_):
        self.close()
        return False
