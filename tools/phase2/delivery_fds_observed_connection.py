"""Explicit 2026-10-24 read-only connection selection, never live discovery.

Observed DNS/TLS/SSH facts are time-bound inputs, not login/issuance authority.
No import-time I/O, ambient credentials, helper installation or SITE activation.
"""
import delivery_fds_site_inputs as profile

OBSERVED_AT = '2026-10-24T10:35:50.741088+00:00'
OAUTH_ORIGIN = 'https://oauth.fds-rosa-hcp.khht.p3.openshiftapps.com:443'
OAUTH_ENDPOINT_IP = '52.78.218.60'
API_ENDPOINT_IP = '13.125.150.237'
CA_PEM_SHA256 = '22b557a27055b33606b6559f37703928d3e4ad79f110b407d04986e1843543d1'
METADATA_SHA256 = 'c855230309dea740f01ddca4ad858145f300503858ff6090e7958ca690036fd8'
SERVER_CERT_DER_SHA256 = '33894df7e81331dc926002acf81a8fee816793dde3f0c2eba6c9885821c408b0'
SSH_PORT = 22
SSH_HOST_KEY_ALIAS = '10.250.10.20'
SSH_ED25519_FINGERPRINT = 'SHA256:mrhPqYIIujKpICtlTbxKg3LjkuGFur5na0Kzch2N5Ws'
REMOTE_PYTHON = '/usr/bin/python3'
REMOTE_PYTHON_VERSION = '3.12.15'
OBSERVED_WORKSPACE = '/home/server01_cicd-runner/actions-runner/_work/infra-cicd/infra-cicd'
OBSERVED_RUNNER_CHECKOUT = 'b5effa840f6b8166cd6f6f52edafe98c7d7a94f4'


def login_inputs():
    """Fresh non-secret keyword inputs for the EXISTING explicit provider.

    The caller still supplies username, password/input, clock and deadline.
    The same public root was independently TLS-verified for the OAuth hostname;
    its hash is explicit, not inferred merely from the API's trust selection.
    """
    return dict(origin=OAUTH_ORIGIN, ca_pem=profile.CA_PEM,
                ca_sha256=CA_PEM_SHA256, endpoint_ip=OAUTH_ENDPOINT_IP)


def supplier_inputs():
    """Only observed transport values; job workspace/context remain explicit."""
    return dict(endpoint_ip=API_ENDPOINT_IP, port=SSH_PORT,
                host_key_alias=SSH_HOST_KEY_ALIAS, remote_python=REMOTE_PYTHON)


def selection_status():
    """Return the dated observation, never a current reachability/GO claim."""
    return {
        'observed_at': OBSERVED_AT,
        'oauth_origin': OAUTH_ORIGIN,
        'oauth_endpoint_ip': OAUTH_ENDPOINT_IP,
        'api_endpoint_ip': API_ENDPOINT_IP,
        'metadata_sha256': METADATA_SHA256,
        'ca_pem_sha256': CA_PEM_SHA256,
        'read_only_tls_verified_at_observation': True,
        'read_only_management_ssh_verified_at_observation': True,
        'ssh_port': SSH_PORT, 'ssh_host_key_alias': SSH_HOST_KEY_ALIAS,
        'ssh_ed25519_fingerprint': SSH_ED25519_FINGERPRINT,
        'observed_python': REMOTE_PYTHON, 'observed_python_version': REMOTE_PYTHON_VERSION,
        'observed_workspace': OBSERVED_WORKSPACE,
        'observed_runner_checkout': OBSERVED_RUNNER_CHECKOUT,
        'helpers_present_in_observed_base_checkout': False,
        'job_checkout_helper_deployment_verified': False,
        'basic_login_verified': False, 'token_request_verified': False,
        'runtime_authorized': False,
    }
