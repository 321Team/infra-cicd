"""Fetch one approved private source SHA; never persist transport credentials/output."""
import base64
import re

import build_scan as flow

PRIVATE_REPOSITORY = '321Team/fds-msa'
PRIVATE_ORIGIN = 'https://github.com/' + PRIVATE_REPOSITORY + '.git'
TOKEN_ENV = 'CICD_APP_SOURCE_TOKEN'
HEADER_ENV = 'CICD_SOURCE_AUTH_HEADER'
GIT = ['/usr/bin/git', '--no-replace-objects',
       '-c', 'core.hooksPath=/dev/null', '-c', 'credential.helper=',
       '-c', 'http.sslVerify=true', '-c', 'http.followRedirects=false',
       '-c', 'http.extraHeader=', '-c', 'gc.auto=0', '-c', 'maintenance.auto=false',
       '-c', 'protocol.allow=never', '-c', 'protocol.https.allow=always',
       '-c', 'fetch.recurseSubmodules=false', '-c', 'fetch.fsckObjects=true']


def origin(repository):
    flow.require(repository in flow.SOURCE_REPOSITORIES, 'SOURCE_REPOSITORY_INVALID')
    return 'https://github.com/' + repository + '.git'


def validate_token(token):
    # No token contents, prefix, hash, or length are included in diagnostics.
    flow.require(isinstance(token, str) and bool(token), 'SOURCE_READ_TOKEN_REQUIRED')
    flow.require(re.fullmatch(r'[A-Za-z0-9_]{20,512}', token) is not None,
                 'SOURCE_READ_TOKEN_INVALID')


def fetch(target, sha, token, env, folder, steps):
    """env must be the isolated Git environment, never the inherited environment.

    Only the fixed GitHub repository URL receives a transient Basic auth header.
    Same-UID hostile processes / host administrators are outside this boundary.
    """
    validate_token(token)
    flow.require(isinstance(sha, str) and re.fullmatch('[0-9a-f]{40}', sha),
                 'APPROVED_SOURCE_SHA_REQUIRED')
    scoped = dict(env)
    scoped[HEADER_ENV] = 'AUTHORIZATION: basic ' + base64.b64encode(
        ('x-access-token:' + token).encode('ascii')).decode('ascii')
    argv = GIT + ['--config-env=http.' + PRIVATE_ORIGIN + '.extraheader=' + HEADER_ENV,
                  '-C', str(target), 'fetch', '--no-tags', '--depth=1',
                  '--no-auto-maintenance', '--no-recurse-submodules', PRIVATE_ORIGIN, sha]
    code = -1
    try:
        # Raw stdout/stderr may echo a credential, including encoded variants.
        # Discard them instead of attempting incomplete post-hoc redaction.
        code = flow.run_command(argv, folder, scoped, None, 300)
    finally:
        scoped.clear()
        output = folder / 'source-fetch.log'
        with output.open('x') as stream:
            stream.write('PRIVATE_SOURCE_FETCH_EXIT=' + str(code) + '\nRAW_OUTPUT=DISCARDED\n')
        steps.append({'name': 'source-fetch', 'exit_code': code,
                      'log_sha256': flow.sha256(output)})
    flow.require(code == 0, 'SOURCE_PRIVATE_FETCH_FAILED')
