"""Explicit private GitHub reads, separate from the ROSA bearer credential.

GET-only transport and an exact repository allowlist do not prove the token's
server-side permissions, issuer or expiry. No issuer, default token, environment
discovery or new authorization is installed here. local_use_until is an absolute
monotonic local use cap, NOT an authenticated server expiry timestamp.
"""
from dataclasses import dataclass, field
import re
import time

import delivery_adapter as core
from delivery_credential_channel import PipeCredentialReceiver
from delivery_github_source import (GitHubOriginalAdapter, PublicGitHubTLS,
                                    SourceError, need, repository_checked)


@dataclass(frozen=True, repr=False)
class GitHubReadCredential:
    token: str = field(repr=False)
    repository: object = field(repr=False)
    local_use_until: float
    _closed: bool = field(default=False, init=False, repr=False)

    def __post_init__(self):
        repository_checked(self.repository)
        need(type(self.token) is str and 20 <= len(self.token) <= 4096
             and re.fullmatch(r'[A-Za-z0-9._~+/\-]+=*', self.token) is not None
             and core.finite(self.local_use_until), 'SOURCE_CREDENTIAL_INVALID')

    def __repr__(self):
        return 'GitHubReadCredential(<redacted>)'

    def header(self, path, now):
        need(not self._closed and core.finite(now) and now < self.local_use_until,
             'SOURCE_CREDENTIAL_UNAVAILABLE')
        prefix = f'/repos/{self.repository.owner}/{self.repository.name}'
        need(path == prefix or path.startswith(prefix + '/'), 'SOURCE_CREDENTIAL_SCOPE_MISMATCH')
        # Revalidate mutable credential state before every emission. This does
        # not attest actual GitHub permission scope or the token's issuer.
        self.__post_init__()
        return b'Authorization: Bearer ' + self.token.encode('ascii') + b'\r\n'

    def close(self):
        object.__setattr__(self, '_closed', True)
        object.__setattr__(self, 'token', '')  # No claim of Python memory zeroization.


class PrivateGitHubTLS(PublicGitHubTLS):
    def __init__(self, *, credential, ca_pem, endpoint_ip, monotonic=time.monotonic):
        need(type(credential) is GitHubReadCredential and not credential._closed,
             'SOURCE_CREDENTIAL_REQUIRED')
        credential.__post_init__()
        self._credential, self._closed = credential, False
        super().__init__(ca_pem=ca_pem, endpoint_ip=endpoint_ip, monotonic=monotonic)
        need(self.now() < credential.local_use_until, 'SOURCE_CREDENTIAL_UNAVAILABLE')

    def _request_headers(self, path, deadline):
        need(not self._closed and type(self._credential) is GitHubReadCredential,
             'SOURCE_CREDENTIAL_UNAVAILABLE')
        # No ambient/ROSA credentials or public fallback. Scope is checked before
        # socket creation, so another repository never receives this token.
        return self._credential.header(path, self.now())

    def get(self, path, deadline):
        need(not self._closed and type(self._credential) is GitHubReadCredential
             and not self._credential._closed and core.finite(deadline),
             'SOURCE_CREDENTIAL_UNAVAILABLE')
        # The use cap also bounds handshake/send/read, not just header creation.
        return super().get(path, min(deadline, self._credential.local_use_until))

    def close(self):
        self._closed = True
        self._credential = None


class GitHubPipeCredentialReceiver(PipeCredentialReceiver):
    """A separate owned Linux pipe, never the ROSA channel/credential object.

    Repository and local use cap are code configuration, not read from input.
    Reuses the reviewed anonymous-pipe limits/ownership/EOF/scratch cleanup.
    """
    def __init__(self, fd, *, repository, local_use_until, monotonic=time.monotonic):
        super().__init__(fd, monotonic=monotonic)
        try:
            repository_checked(repository)
            need(core.finite(local_use_until) and self.now() < local_use_until,
                 'SOURCE_CREDENTIAL_UNAVAILABLE')
            self.repository, self.local_use_until = repository, local_use_until
        except Exception:
            self.close()
            raise SourceError('SOURCE_CREDENTIAL_CONFIGURATION_INVALID') from None

    def _credential(self, token):
        return GitHubReadCredential(token, self.repository, self.local_use_until)

    def receive(self, deadline):
        try:
            need(core.finite(deadline), 'SOURCE_DEADLINE_EXCEEDED')
            return super().receive(min(deadline, self.local_use_until))
        finally:
            self.close()


class PrivateGitHubOriginalAdapter(GitHubOriginalAdapter):
    def __init__(self, transport):
        need(type(transport) is PrivateGitHubTLS, 'SOURCE_TRANSPORT_REQUIRED')
        self._transport = transport

    def repository(self, request, pin, receipts):
        repository_checked(pin)
        need(not self._transport._closed and self._transport._credential is not None
             and pin == self._transport._credential.repository, 'SOURCE_CREDENTIAL_SCOPE_MISMATCH')
        prefix = f'/repos/{pin.owner}/{pin.name}'
        obj = self.read(request, prefix, receipts)
        need(type(obj) is dict and type(obj.get('owner')) is dict
             and type(obj.get('id')) is int and obj['id'] == pin.repository_id
             and obj.get('full_name') == f'{pin.owner}/{pin.name}'
             and obj.get('private') is True
             and type(obj.get('owner', {}).get('id')) is int
             and obj['owner']['id'] == pin.owner_id, 'SOURCE_REPOSITORY_CHANGED')
        return prefix
