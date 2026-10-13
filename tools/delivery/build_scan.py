#!/usr/bin/env python3
"""Reviewed input -> committed source snapshot -> tests -> rootless builds -> scan.

No operational Workflow, Push, Deploy, credential issuance or source approval API.
The main-branch input record must be reviewed against the application's approval.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools' / 'trivy'))
from oci_archive import inspect_archive, json_object  # noqa: E402
from prepare_db import (PreflightError, require, runtime_context,  # noqa: E402
                        scanner_environment, utcnow)
from scan_images import SERVICES, scan_pair  # noqa: E402
from verify_runner import sha256  # noqa: E402
import offline_supply as offline  # noqa: E402
import offline_supply_v3 as offline_v3  # noqa: E402
import image_smoke  # noqa: E402

CONFIG = 'config/app-build-scan.json'
PODMAN = '/usr/bin/podman'
SOURCE_REPOSITORY = 'supreme33-kr/fds-msa'
SOURCE_REPOSITORIES = (SOURCE_REPOSITORY, '321Team/fds-msa')
DIGEST = r'sha256:[0-9a-f]{64}'
BASE = r'[a-z0-9][a-z0-9.-]*(?::[0-9]+)?/[a-z0-9][a-z0-9._/-]*@' + DIGEST


def relative(value):
    require(isinstance(value, str) and value and '\\' not in value and
            not any(ord(c) < 32 for c in value), 'RELATIVE_PATH_INVALID')
    parts = value.split('/')
    require(not value.startswith('/') and all(p not in ('', '.', '..', '.git') for p in parts),
            'RELATIVE_PATH_INVALID')
    return value


def test_step(step):
    require(isinstance(step, dict) and set(step) == {'cwd', 'argv', 'timeout_seconds'},
            'TEST_STEP_REQUIRED')
    relative(step['cwd'])
    argv = step['argv']
    require(isinstance(argv, list) and 0 < len(argv) <= 128 and
            all(isinstance(a, str) and a and len(a) <= 4096 and
                not any(ord(c) < 32 for c in a) for a in argv), 'TEST_ARGV_REQUIRED')
    require(type(step['timeout_seconds']) is int and 1 <= step['timeout_seconds'] <= 1800,
            'TEST_TIMEOUT_INVALID')


def unit_not_applicable(value):
    return isinstance(value, dict) and value.get('status') == 'NOT_APPLICABLE'


def validate_unit(value):
    if not unit_not_applicable(value):
        test_step(value)  # null is unresolved, never an implicit exemption.
        return
    require(set(value) == {'status', 'reason', 'approved_by', 'approval_reference'},
            'UNIT_SCOPE_FIELDS_INVALID')
    for key in ('reason', 'approved_by'):
        text = value[key]
        require(isinstance(text, str) and text.strip() and len(text) <= 2000 and
                not any(ord(c) < 32 for c in text), 'UNIT_SCOPE_JUSTIFICATION_REQUIRED')
    reference = value['approval_reference']
    require(isinstance(reference, str) and
            re.fullmatch(r'https://github\.com/[^\s]+', reference),
            'UNIT_SCOPE_APPROVAL_REFERENCE_REQUIRED')


def validate_config(data):
    require(isinstance(data, dict) and data.get('schema') in
            ('P1-CICD-app-build-scan-1', 'P1-CICD-app-build-scan-2',
             'P1-CICD-app-build-scan-3'), 'INPUT_SCHEMA_INVALID')
    schema = data['schema']
    supplied = schema in ('P1-CICD-app-build-scan-2', 'P1-CICD-app-build-scan-3')
    fields = {'schema', 'status', 'source', 'services'} | ({'offline_supply'} if supplied else set())
    require(set(data) == fields, 'INPUT_FIELDS_INVALID')
    require(data.get('status') == 'APPROVED_FOR_TEST_BUILD_SCAN', 'APP_HANDOFF_PENDING')
    source = data.get('source')
    require(isinstance(source, dict) and source.get('repository') in SOURCE_REPOSITORIES,
            'SOURCE_REPOSITORY_INVALID')
    require(set(source) == {'repository', 'commit', 'approved_by', 'approval_reference'},
            'SOURCE_FIELDS_INVALID')
    require(isinstance(source.get('commit'), str) and re.fullmatch('[0-9a-f]{40}', source['commit']),
            'APPROVED_SOURCE_SHA_REQUIRED')
    require(isinstance(source.get('approved_by'), str) and source['approved_by'].strip(),
            'SOURCE_APPROVER_REQUIRED')
    reference = source.get('approval_reference')
    require(isinstance(reference, str) and re.fullmatch(r'https://github\.com/[^\s]+', reference),
            'SOURCE_APPROVAL_REFERENCE_REQUIRED')
    services = data.get('services')
    require(isinstance(services, dict) and set(services) == set(SERVICES),
            'EXACT_TWO_SERVICES_REQUIRED')
    for name in SERVICES:
        service = services[name]
        service_fields = ({'context', 'dockerfile', 'setup', 'unit', 'contract'}
                          if schema == 'P1-CICD-app-build-scan-3' else
                          {'context', 'dockerfile', 'base_image', 'setup', 'unit', 'contract'})
        require(isinstance(service, dict) and set(service) == service_fields,
                'SERVICE_INPUT_INVALID')
        relative(service['context'])
        relative(service['dockerfile'])
        if schema != 'P1-CICD-app-build-scan-3':
            require(isinstance(service['base_image'], str) and re.fullmatch(BASE, service['base_image']),
                    'BASE_IMAGE_DIGEST_REQUIRED')
        require(isinstance(service['setup'], list) and len(service['setup']) <= 20,
                'SETUP_STEPS_INVALID')
        validate_unit(service['unit'])
        for step in [*service['setup'], service['contract']]:
            test_step(step)
    require(any(not unit_not_applicable(services[name]['unit']) for name in SERVICES),
            'UNIT_PHASE_REQUIRED')
    if schema == 'P1-CICD-app-build-scan-2':
        offline.validate_record(data['offline_supply'])
        bases = {service['base_image'] for service in services.values()}
        require(len(bases) == 1
                and next(iter(bases)).endswith('@' + data['offline_supply']['manifest_digest']),
                'OFFLINE_BASE_BINDING')
        require(all(service['setup'] == [] for service in services.values()),
                'OFFLINE_SETUP_MUST_BE_GENERATED')
    elif schema == 'P1-CICD-app-build-scan-3':
        offline_v3.validate_record(data['offline_supply'])
        require(all(service['setup'] == [] for service in services.values()),
                'OFFLINE_SETUP_MUST_BE_GENERATED')
    return data


def offline_module(config):
    return offline_v3 if config['schema'] == 'P1-CICD-app-build-scan-3' else offline


def offline_dockerfile(config, name):
    if config['schema'] == 'P1-CICD-app-build-scan-3':
        return offline_v3.dockerfile(name)
    return offline.dockerfile(name, config['services'][name]['base_image'])

def run_command(argv, cwd, env, output, timeout):
    """No shell interpolation; raw output stays in a private file.

    Stop the command process group on timeout or parent interruption. This is
    not a sandbox for malicious code running with the same UID.
    """
    # Authenticated transport discards untrusted server output at the OS boundary.
    with (open(os.devnull, 'wb') if output is None else output.open('xb')) as stream:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=stream,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            return process.wait(timeout=timeout)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=10)
            raise


def read_output(path):
    require(path.stat().st_size <= 1024 * 1024, 'COMMAND_OUTPUT_TOO_LARGE')
    return path.read_text().strip()


def unpack_source(archive_path, target, *, for_image=False):
    """Narrow source profile: regular files/directories, no symlinks/gitlinks."""
    target.mkdir(mode=0o700)

    def private_directory(path):
        # Create each ancestor explicitly; mkdir(parents=True) ignores mode
        # for implicit parents. The new target contains no accepted symlinks.
        current = target
        for part in path.relative_to(target).parts:
            current = current / part
            current.mkdir(mode=0o700, exist_ok=True)
            current.chmod(0o700)

    seen, total = set(), 0
    with tarfile.open(archive_path, 'r:') as archive:
        for member in archive:
            name = member.name.rstrip('/') if member.isdir() else member.name
            relative(name)
            require(name not in seen and len(seen) < 10000, 'SOURCE_DUPLICATE_OR_TOO_MANY_FILES')
            seen.add(name)
            require(member.isdir() or member.isfile(), 'SOURCE_LINK_OR_SPECIAL_FILE')
            total += member.size
            require(total <= 512 * 1024 * 1024, 'SOURCE_ARCHIVE_TOO_LARGE')
            path = target / name
            if member.isdir():
                private_directory(path)
            else:
                private_directory(path.parent)
                with archive.extractfile(member) as src, path.open('xb') as dst:
                    shutil.copyfileobj(src, dst)
                path.chmod(0o700 if member.mode & 0o111 else 0o600)

    if for_image:
        # COPY preserves these source modes in the non-root runtime image.
        # Keep the context root private; only validated contents enter the image.
        for path in target.rglob('*'):
            path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644)


def validate_source_files(root, config):
    for name, service in config['services'].items():
        require((root / service['context']).is_dir(), 'BUILD_CONTEXT_MISSING')
        dockerfile = root / service['dockerfile']
        require(dockerfile.is_file() and dockerfile.stat().st_size <= 1024 * 1024,
                'DOCKERFILE_MISSING_OR_TOO_LARGE')
        if config['schema'] == 'P1-CICD-app-build-scan-3':
            require(dockerfile.read_bytes() == offline_v3.dockerfile(name).encode(),
                    'DOCKERFILE_TWO_STAGE_PROFILE_REQUIRED')
        else:
            # Supported legacy profile: one literal, pinned FROM. No silent Dockerfile rewrite.
            lines = [line.strip() for line in dockerfile.read_text().splitlines()]
            from_lines = [line for line in lines if re.match(r'(?i)^FROM(?:\s|$)', line)]
            require(len(from_lines) == 1 and
                    re.fullmatch(r'(?i:FROM)\s+' + re.escape(service['base_image']), from_lines[0]),
                    'DOCKERFILE_BASE_NOT_PINNED_OR_UNSUPPORTED')
        steps = [*service['setup'], service['contract']]
        if not unit_not_applicable(service['unit']):
            steps.append(service['unit'])
        for step in steps:
            require((root / step['cwd']).is_dir(), 'TEST_CWD_MISSING')

def validate_scan_receipt(scan_rc, scan, context, source_sha, built, archives):
    """Bind PASS and fail-fast BLOCK receipts to the actual build and run."""
    require(type(scan_rc) is int and scan_rc in (0, 10), 'IMAGE_SCAN_FAILED')
    status = 'PASS' if scan_rc == 0 else 'BLOCK'
    require(isinstance(scan, dict) and scan.get('IMAGE_SCAN') == status and
            scan.get('source_sha') == source_sha and scan.get('context') == context,
            'SCAN_RECEIPT_MISMATCH')
    images = scan.get('images')
    require(isinstance(images, dict) and 0 < len(images) <= len(SERVICES),
            'SCAN_RECEIPT_MISMATCH')
    # scan_pair stops on the first vulnerability, so BLOCK can have one image.
    # Only a contiguous prefix can have been scanned; later images are unknown.
    scanned = SERVICES[:len(images)]
    require(set(images) == set(scanned), 'SCAN_RECEIPT_MISMATCH')
    if scan_rc == 0:
        require(len(images) == len(SERVICES), 'SCAN_RECEIPT_MISMATCH')
    else:
        require(scan.get('error_code') == 'HIGH_CRITICAL_FINDINGS', 'SCAN_RECEIPT_MISMATCH')
    statuses = {name: 'NOT_SCANNED' for name in SERVICES}
    for name in scanned:
        image = images[name]
        expected = 'BLOCK' if scan_rc == 10 and name == scanned[-1] else 'PASS'
        require(isinstance(image, dict) and image.get('scan') == expected and
                type(image.get('exit_code')) is int and
                image['exit_code'] == (10 if expected == 'BLOCK' else 0),
                'SCAN_RECEIPT_MISMATCH')
        for key in ('archive_sha256', 'manifest_digest', 'config_digest'):
            require(image.get(key) == built[name][key], 'SCAN_BUILD_IDENTITY_MISMATCH')
        statuses[name] = expected
    for name in SERVICES:
        # Preserve both built artifacts, including the one not reached by Scan.
        require(sha256(archives[name]) == built[name]['archive_sha256'], 'BUILT_ARCHIVE_CHANGED')
    return statuses


def execute(config, source_dir, evidence, context, incoming_env):
    """Caller provides a new private directory and reviewed main input record."""
    result = {'BUILD_SCAN': 'FAIL', 'DELIVERY_GATE': 'NOT_VERIFIED',
              'PUSH': 'NOT_EXECUTED', 'DEPLOY': 'NOT_EXECUTED', 'context': context,
              'started_at': utcnow().isoformat(), 'steps': [], 'images': {},
              'not_applicable_tests': []}
    rc = 1
    try:
        validate_config(config)  # No source command/network before input completion.
        schema = config['schema']
        supplied = schema in ('P1-CICD-app-build-scan-2', 'P1-CICD-app-build-scan-3')
        supply_v3 = schema == 'P1-CICD-app-build-scan-3'
        v3_images = None
        source_sha = config['source']['commit']
        result['source'] = config['source']
        home, runtime = evidence / 'home', evidence / 'runtime'
        home.mkdir(mode=0o700)
        runtime.mkdir(mode=0o700)
        env = scanner_environment(incoming_env)
        env.update(HOME=str(home), XDG_RUNTIME_DIR=str(runtime),
                   GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null',
                   GIT_TERMINAL_PROMPT='0', GIT_NO_REPLACE_OBJECTS='1', PYTHONNOUSERSITE='1')
        if supplied:
            env = {key: value for key, value in env.items() if 'proxy' not in key.lower()}
            env['PIP_CONFIG_FILE'] = os.devnull
            env['XDG_CACHE_HOME'] = str(home / 'cache')
            env['XDG_RUNTIME_DIR'] = '/run/user/' + str(os.getuid())

        def command(label, argv, cwd=evidence, timeout=300):
            output = evidence / (label + '.log')
            code = run_command(argv, cwd, env, output, timeout)
            result['steps'].append({'name': label, 'exit_code': code, 'log_sha256': sha256(output)})
            require(code == 0, 'COMMAND_FAILED:' + label)
            return output

        head = command('source-head', ['/usr/bin/git', '-C', str(source_dir), 'rev-parse', 'HEAD'])
        require(read_output(head) == source_sha, 'SOURCE_HEAD_MISMATCH')
        origin = command('source-origin', ['/usr/bin/git', '-C', str(source_dir),
                                            'remote', 'get-url', 'origin'])
        source_origin = 'https://github.com/' + config['source']['repository']
        require(read_output(origin) in (source_origin, source_origin + '.git'),
                'SOURCE_ORIGIN_MISMATCH')
        tree = command('source-tree', ['/usr/bin/git', '-C', str(source_dir), 'ls-tree',
                                       '-r', '-z', source_sha])
        require(tree.stat().st_size <= 1024 * 1024, 'SOURCE_TREE_TOO_LARGE')
        require(all(not row.startswith(b'160000 ') for row in tree.read_bytes().split(b'\0')),
                'SOURCE_SUBMODULE_UNSUPPORTED')
        snapshot = evidence / 'source.tar'
        command('source-archive', ['/usr/bin/git', '-C', str(source_dir), 'archive',
                                   '--format=tar', '--output=' + str(snapshot), source_sha])
        result['source_archive_sha256'] = sha256(snapshot)
        tests_root, build_root = evidence / 'tests-source', evidence / 'build-source'
        unpack_source(snapshot, tests_root)
        validate_source_files(tests_root, config)
        staged = evidence / 'offline-supply'
        if supplied:
            if supply_v3:
                result['offline_supply'] = {'SUPPLY_COPY': 'FAIL', 'IMAGE_IMPORT': 'NOT_EXECUTED'}
                identities = offline_v3.stage(config['offline_supply'], staged)
                result['offline_supply'].update(SUPPLY_COPY='PASS', images=identities)
                offline_v3.validate_source(tests_root, config, staged)
            else:
                result['offline_supply'] = {'SUPPLY_COPY': 'FAIL',
                                            'REPOSITORY_DIGEST_RESOLUTION': 'NOT_EXECUTED'}
                result['offline_supply'].update(
                    offline.stage(config['offline_supply'], staged), SUPPLY_COPY='PASS')
                offline.validate_source(tests_root, config, staged)
        # Tests use a separate copy; generated/ignored files do not enter Build.
        podman = [PODMAN, '--remote=false', '--root', str(evidence / 'storage'),
                  '--runroot', str(evidence / 'runroot')]
        if supplied:
            podman += ['--storage-driver', 'vfs', '--tmpdir', str(evidence / 'podman-tmp'),
                       '--events-backend', 'none']
        version = command('podman-version', podman + ['--version'])
        require(read_output(version) == 'podman version 6.1.0', 'PODMAN_VERSION_MISMATCH')
        rootless = command('podman-rootless', podman + ['info', '--format', '{{.Host.Security.Rootless}}'])
        require(read_output(rootless) == 'true', 'ROOTLESS_REQUIRED')
        if supplied:
            if supply_v3:
                result['offline_supply']['IMAGE_IMPORT'] = 'FAIL'
                v3_images = offline_v3.import_images(
                    command, podman, config['offline_supply'], staged)
                result['offline_supply'].update(IMAGE_IMPORT='PASS', imports=v3_images)
                offline_v3.install_tests(
                    command, podman, tests_root, staged, v3_images['builder']['reference'])
            else:
                base = config['services'][SERVICES[0]]['base_image']
                result['offline_supply']['REPOSITORY_DIGEST_RESOLUTION'] = 'FAIL'
                result['offline_supply'].update(
                    offline.import_base(command, podman, config['offline_supply'], staged, base))
                offline.install_tests(command, tests_root, staged)
        for phase in ('setup', 'unit', 'contract'):
            for name in SERVICES:
                value = config['services'][name][phase]
                if phase == 'unit' and unit_not_applicable(value):
                    result['not_applicable_tests'].append({'service': name, 'phase': phase, **value})
                    continue  # No command, log, exit code or fabricated PASS.
                steps = value if phase == 'setup' else [value]
                for index, step in enumerate(steps):
                    if supply_v3:
                        command(f'{name}-{phase}-{index}',
                                offline_v3.test_argv(
                                    podman, tests_root, staged,
                                    v3_images['builder']['reference'], step),
                                timeout=step['timeout_seconds'])
                    else:
                        command(f'{name}-{phase}-{index}', step['argv'],
                                tests_root / step['cwd'], step['timeout_seconds'])
        require(sha256(snapshot) == result['source_archive_sha256'], 'SOURCE_ARCHIVE_CHANGED')
        unpack_source(snapshot, build_root, for_image=True)
        validate_source_files(build_root, config)
        if supplied:
            supply = offline_v3 if supply_v3 else offline
            supply.verify_staged(staged, config['offline_supply'])
            supply.validate_source(build_root, config, staged)
        auth = evidence / 'empty-auth.json'
        auth.write_text('{"auths":{}}\n')
        archives = {}
        for name in SERVICES:
            service = config['services'][name]
            if supplied:
                # Recheck immediately before each consumer; tests never supply build artifacts.
                supply = offline_v3 if supply_v3 else offline
                supply.verify_staged(staged, config['offline_supply'])
                build_options = ['--network=none', '--http-proxy=false', '--no-cache',
                    '--security-opt=label=disable', '--build-context',
                    'python-wheels=' + str(staged / (name + '-runtime') / 'wheels')]
                if supply_v3:
                    build_options += [
                        '--build-arg', 'BUILDER_IMAGE=' + v3_images['builder']['reference'],
                        '--build-arg', 'RUNTIME_IMAGE=' + v3_images['runtime']['reference']]
            else:
                command(name + '-base-pull', podman + ['pull', '--platform', 'linux/amd64',
                        '--tls-verify=true', '--authfile', str(auth), service['base_image']], timeout=600)
                build_options = []
            iid = evidence / (name + '.iid')
            command(name + '-build', podman + ['build', '--format', 'oci', '--platform', 'linux/amd64',
                    '--pull=never', '--tls-verify=true', '--authfile', str(auth), '--iidfile', str(iid),
                    *build_options,
                    '--file', str(build_root / service['dockerfile']),
                    str(build_root / service['context'])], timeout=1800)
            image_id = read_output(iid)
            require(re.fullmatch(DIGEST, image_id), 'BUILD_IMAGE_ID_INVALID')
            archive = evidence / (name + '.oci.tar')
            command(name + '-save', podman + ['save', '--format', 'oci-archive',
                                             '--output', str(archive), image_id], timeout=600)
            identity = inspect_archive(archive)
            require(identity['config_digest'] == image_id, 'BUILD_ARCHIVE_ID_MISMATCH')
            result['images'][name] = {'image_id': image_id, **identity}
            archives[name] = archive
            if supply_v3:
                receipt = result.setdefault('image_startup', {}).setdefault(name, {})
                try:
                    image_smoke.check_image(command, podman, name, identity, evidence, receipt)
                except image_smoke.SmokeError as exc:
                    raise PreflightError(str(exc)) from None
                require(receipt.get('status') == 'PASS', 'FINAL_IMAGE_STARTUP_NOT_PASS')
                require(sha256(archive) == identity['archive_sha256'], 'BUILT_ARCHIVE_CHANGED')
        scan_dir = evidence / 'scan'
        scan_dir.mkdir(mode=0o700)
        scan_rc, scan = scan_pair(scan_dir, context, source_sha, archives, incoming_env)
        result['scan_result_sha256'] = sha256(scan_dir / 'result.json')
        result['scan_status'] = validate_scan_receipt(scan_rc, scan, context, source_sha,
                                                     result['images'], archives)
        result['BUILD_SCAN'], rc = ('BLOCK' if scan_rc == 10 else 'PASS'), scan_rc
    except PreflightError as exc:
        result['error_code'] = str(exc)
    except subprocess.TimeoutExpired:
        result['error_code'] = 'COMMAND_TIMEOUT'
    except (OSError, ValueError, TypeError, AttributeError, KeyError, RecursionError,
            tarfile.TarError, offline.zipfile.BadZipFile, EOFError, subprocess.SubprocessError):
        result['error_code'] = 'SOURCE_BUILD_OR_EVIDENCE_ERROR'
    result['finished_at'] = utcnow().isoformat()
    (evidence / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    (evidence / 'SHA256SUMS').write_text(sha256(evidence / 'result.json') + '  result.json\n')
    return rc, result


def interrupted(_signum, _frame):
    raise PreflightError('BUILD_SCAN_INTERRUPTED')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path)
    parser.add_argument('--execute', action='store_true', help='Requires reviewed main inputs and runtime context')
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        require(sys.version_info >= (3, 11), 'PYTHON_3_11_REQUIRED')
        require((ROOT / CONFIG).stat().st_size <= 64 * 1024, 'INPUT_TOO_LARGE')
        local_bytes = (ROOT / CONFIG).read_bytes()
        config = validate_config(json_object(local_bytes))
        if not args.execute:
            print('INPUT_SHAPE=VALID\nSOURCE_APPROVAL=DECLARED_NOT_INDEPENDENTLY_VERIFIED\nDELIVERY_GATE=NOT_VERIFIED')
            return 0
        require(args.source_dir is not None, 'SOURCE_CHECKOUT_REQUIRED')
        source_dir = args.source_dir.resolve()
        os.chdir(ROOT)
        signal.signal(signal.SIGINT, interrupted)
        signal.signal(signal.SIGTERM, interrupted)
        context = runtime_context(os.environ)
        committed = subprocess.check_output(['/usr/bin/git', '--no-replace-objects', 'show', context['workflow_sha'] + ':' + CONFIG],
                                            timeout=15, stderr=subprocess.DEVNULL)
        require(committed == local_bytes, 'INPUT_NOT_FROM_WORKFLOW_COMMIT')
        context['input_sha256'] = hashlib.sha256(committed).hexdigest()
        evidence = Path(tempfile.mkdtemp(prefix=f'build-scan-{context["run_id"]}-{context["attempt"]}-',
                                        dir=Path.home()))
        print(f'EVIDENCE_DIR={evidence}', flush=True)
        rc, result = execute(config, source_dir, evidence, context, os.environ)
        print(f'BUILD_SCAN={result["BUILD_SCAN"]}\nERROR_CODE={result.get("error_code", "NONE")}')
        print('PUSH=NOT_EXECUTED\nDEPLOY=NOT_EXECUTED\nDELIVERY_GATE=NOT_VERIFIED')
        return rc
    except PreflightError as exc:
        print(f'BUILD_SCAN=FAIL\nERROR_CODE={exc}\nDELIVERY_GATE=NOT_VERIFIED')
        return 1
    except (OSError, ValueError, TypeError, AttributeError, KeyError, RecursionError, subprocess.SubprocessError):
        print('BUILD_SCAN=FAIL\nERROR_CODE=INPUT_OR_CONTEXT_ERROR\nDELIVERY_GATE=NOT_VERIFIED')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
