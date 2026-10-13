"""Optional offline MOCK compatibility test against an exact, clean #43 checkout.

Requires PyYAML 6.0.3 and the existing producer repository. No API/credentials.
This test imports reviewed producer fixtures only AFTER checking source identity.
The real bridge reads the pinned checker from Git objects. The positive path uses
a temporary, explicitly synthetic commit; the real source keeps its PENDING host.
"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import delivery_adapter as core
from producer_bridge import CHECKER_PATH, CONTRACT_PATH, CommittedProducerBridge, GitSnapshot
from test_delivery_adapter import Harness

PRODUCER_SHA = '6e7acf1053b1e44164d38851e477a2d2d64b9c3a'


def module_at(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--producer', type=Path, required=True)
    root = parser.parse_args().producer.resolve()
    # Exclude untracked import-shadowing files as well as tracked edits.
    for args, expected in ((['rev-parse', 'HEAD'], PRODUCER_SHA),
                           (['status', '--porcelain', '--untracked-files=all'], '')):
        result = subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                                text=True, timeout=30, check=False)
        if result.returncode != 0 or result.stdout.strip() != expected:
            print('PRODUCER_SOURCE_NOT_PINNED_AND_CLEAN')
            return 2
    import yaml
    if yaml.__version__ != '6.0.3':
        print('PYYAML_VERSION_MISMATCH')
        return 2
    module_at('check_delivery_render', root / 'tools/check_delivery_render.py')
    fixtures = module_at('producer_contract_test_fixtures', root / 'tools/tests/test_deploy_rosa_delivery.py')
    snapshot = GitSnapshot(root, PRODUCER_SHA)

    docs = fixtures.synthetic_app_render()
    images = []
    for obj in docs:
        if obj['kind'] == 'Deployment':
            for container in obj['spec']['template']['spec']['containers']:
                container['image'] = 'registry.example.com/fds/' + container['name'] + '@sha256:' + 'a' * 64
                images.append((container['name'], container['image']))
    raw = yaml.safe_dump_all(docs, sort_keys=False).encode()
    contract_raw = yaml.safe_dump(fixtures.DELIVERY, sort_keys=False).encode()
    harness = Harness()
    harness.render, harness.contract = raw, contract_raw
    harness.rebind(images=tuple(sorted(images, key=lambda p: core.SERVICES.index(p[0]))),
                   route_host=fixtures.APPROVED_HOST, rendered_sha256=core.digest(raw),
                   contract_sha256=core.digest(contract_raw))
    # A synthetic approved contract is NOT committed into the real repository.
    # The checker blob is original and its production pin is never patched.
    with tempfile.TemporaryDirectory(prefix='p2-compat-synthetic-') as directory:
        synthetic = Path(directory).resolve()
        for name, content in ((CHECKER_PATH, snapshot.blob(CHECKER_PATH)),
                              (CONTRACT_PATH, contract_raw)):
            path = synthetic / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_TERMINAL_PROMPT='0')
        def git(*args):
            return subprocess.run(
                ['git', '-C', str(synthetic), '-c', 'core.autocrlf=false',
                 '-c', 'commit.gpgsign=false', '-c', 'user.name=P2 Synthetic',
                 '-c', 'user.email=p2-synthetic@example.invalid', *args],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                timeout=15, check=True).stdout.decode().strip()
        git('init', '-q')
        git('add', '--', CHECKER_PATH, CONTRACT_PATH)
        git('commit', '-qm', 'Synthetic offline compatibility input only')
        synthetic_commit = git('rev-parse', 'HEAD')
        harness.rebind(source_commit=synthetic_commit)
        result = harness.run(CommittedProducerBridge(synthetic, harness.binding))
    if result['status'] != 'ROLLOUT_OBSERVED' or len(harness.requests) != 8:
        print('SYNTHETIC_PRODUCER_COMPATIBILITY_FAILED')
        return 1

    # Real checked-in contract has PENDING host: no sender call is permitted.
    pending = snapshot.blob(CONTRACT_PATH)
    blocked_harness = Harness()
    blocked_harness.render, blocked_harness.contract = raw, pending
    blocked_harness.rebind(source_commit=PRODUCER_SHA,
                           images=harness.binding.images, route_host=fixtures.APPROVED_HOST,
                           rendered_sha256=core.digest(raw), contract_sha256=core.digest(pending))
    blocked = blocked_harness.run(CommittedProducerBridge(root, blocked_harness.binding))
    if (blocked['error_code'] != 'RENDER_CHECK_FAILED' or blocked_harness.requests):
        print('PENDING_PRODUCER_CONTRACT_NOT_BLOCKED')
        return 1
    print(json.dumps({'scope': 'MOCK_ONLY', 'producer': PRODUCER_SHA,
                      'positive_fixture_commit': synthetic_commit,
                      'positive_fixture_is_synthetic': True,
                      'bridge': 'CommittedProducerBridge',
                      'synthetic_approved_render': result['status'],
                      'actual_pending_contract': blocked['error_code'],
                      'actual_pending_sender_calls': 0,
                      'render_generation': 'NOT_VERIFIED', 'source_approval': 'NOT_VERIFIED',
                      'runtime': 'NOT_RUN'}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
