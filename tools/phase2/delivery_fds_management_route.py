"""Code-selected historical management SSH route, NOT an issuer or live proof.

No host/file/key/token inspection, discovery, process, clock or configured SITE.
Port, host-key alias and remote installation remain explicit reviewed inputs.
The chosen same-account helper UID is an expectation, never a live UID receipt.
"""
from delivery_job import RunnerIdentity
from delivery_ssh_bridge import SshBridgeConfig

SOURCE_HOST = 'ansible01'
SOURCE_ACCOUNT = 'server01_ansible'
EXPECTED_RUNNER = RunnerIdentity('cicd-runner01', 'server01_cicd-runner', 1000)
TARGET_IP = '10.250.10.20'
SSH_BINARY = '/usr/bin/ssh'
IDENTITY_FILE = '/home/server01_ansible/.ssh/p1_ansible_ed25519'
KNOWN_HOSTS = '/home/server01_ansible/.ssh/known_hosts'
# Deliberate same-account route: SSH directly logs in as server01_cicd-runner;
# reviewed helper uses no sudo/setns. Not a default for other helper routes.
EXPECTED_HELPER_UID = 1000
REFERENCE = 'https://github.com/321Team/infra-ansible/issues/82#issuecomment-6283088910'
KEY_REFERENCE = 'https://github.com/321Team/infra-ansible/blob/7f04e18371f5d10aec678460f5dcd6e28d98d7d3/tools/ntp-onepass.py#L39'


def build_configuration(*, port, host_key_alias, remote_python, remote_helper,
                        remote_timeout_seconds):
    """Bind ONLY confirmed historical route values to the existing bridge.

    A missing installation value cannot select a placeholder or create a key.
    This returns inert syntax-checked configuration, not a running bridge,
    authenticated source identity, approved issuance location or Runtime grant.
    Current identity/trust/UID/netns and supplier authority still need existing
    runtime checks; selecting the controller never installs ROSA credentials.
    """
    config = SshBridgeConfig(ssh_binary=SSH_BINARY, host_ip=TARGET_IP, port=port,
        user=EXPECTED_RUNNER.account, host_key_alias=host_key_alias,
        known_hosts=KNOWN_HOSTS, identity_file=IDENTITY_FILE,
        remote_python=remote_python, remote_helper=remote_helper,
        listener_uid=EXPECTED_RUNNER.uid, remote_timeout_seconds=remote_timeout_seconds)
    config.check()
    return config


def selection_status():
    """Fresh non-secret expectation report only; no authenticity/authority."""
    return {'mode': 'HISTORICAL_MANAGEMENT_ROUTE', 'reference': REFERENCE,
        'source_host': SOURCE_HOST, 'source_account': SOURCE_ACCOUNT,
        'target_ip': TARGET_IP, 'target_account': EXPECTED_RUNNER.account,
        'identity_file': IDENTITY_FILE, 'known_hosts': KNOWN_HOSTS,
        'key_reference': KEY_REFERENCE,
        'helper_uid_expectation': EXPECTED_HELPER_UID,
        'same_account_helper_selected': True,
        'current_transport_verified': False, 'source_identity_authenticated': False,
        'issuer_location_selected': False, 'helper_deployment_verified': False,
        'execution_authorized': False}
