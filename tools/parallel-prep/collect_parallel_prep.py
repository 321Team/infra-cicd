#!/usr/bin/env python3
"""P1-CICD-01 TLS/Runner preparation inventory; no service/configuration changes."""
import argparse
import datetime as dt
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import pwd
import re
import shutil
import socket
import ssl
import stat
import subprocess
import tarfile
import tempfile

VERSION = '1.0'
HOSTS = {'runner': ('cicd-runner01', '10.250.10.20'), 'harbor': ('harbor01', '10.1.93.54')}
RUNNER_USER = 'server01_cicd-runner'
SAFE_PATH = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
NAMES = ('harbor-log', 'harbor-db', 'redis', 'registry', 'registryctl',
         'harbor-core', 'harbor-portal', 'harbor-jobservice', 'nginx')
FIELDS = {
    'id': '.Id', 'image': '.Config.Image', 'status': '.State.Status',
    'compose_working_dir': '(index .Config.Labels "com.docker.compose.project.working_dir")',
    'compose_config_files': '(index .Config.Labels "com.docker.compose.project.config_files")',
}
FORMAT = '{' + ','.join(json.dumps(k) + ':{{json ' + v + '}}' for k, v in FIELDS.items())
FORMAT += ',"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}}'
FORMAT += ',"mounts":[{{range $i,$m := .Mounts}}{{if $i}},{{end}}{"source":{{json $m.Source}},"destination":{{json $m.Destination}},"rw":{{json $m.RW}}}{{end}}]}'


def scrub(value):
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if not isinstance(value, str):
        return value
    if re.search(r'-----BEGIN [^-]*PRIVATE KEY-----', value):
        return '[REDACTED_PRIVATE_KEY]'
    value = re.sub(r'(?i)([a-z][a-z0-9+.-]*://)[^\s/@]+@', r'\1[REDACTED]@', value)
    value = re.sub(r'(?i)\b(Bearer|Basic)\s+[A-Za-z0-9+/=_~.-]+', r'\1 [REDACTED]', value)
    value = re.sub(r'\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)\b', '[REDACTED_GITHUB_TOKEN]', value)
    return re.sub(r'(?i)(\b(?:password|passwd|token|secret|api[_-]?key)\b[\s:=]+)(?:"[^"\n]*"|\x27[^\x27\n]*\x27|[^\s,;]+)', r'\1[REDACTED]', value)


def run(argv):
    env = {'PATH': SAFE_PATH, 'LC_ALL': 'C', 'HOME': pwd.getpwuid(os.geteuid()).pw_dir}
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=12, check=False, env=env)
        if len(result.stdout) > 131072:
            return {'rc': result.returncode, 'error': 'OUTPUT_TOO_LARGE'}
        # stderr is deliberately not included in evidence.
        return {'rc': result.returncode, 'text': result.stdout}
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        return {'rc': None, 'error': type(exc).__name__}


def query(argv):
    result = run(argv)
    if result.get('rc') == 0 and 'text' in result:
        try:
            return {'status': 'COLLECTED', 'data': json.loads(result['text'])}
        except (ValueError, TypeError):
            pass
    return {'status': 'UNAVAILABLE', 'rc': result.get('rc'), 'error': result.get('error', 'QUERY_OR_PARSE_FAILED')}


def text_query(argv):
    result = run(argv)
    return {'status': 'COLLECTED' if result.get('rc') == 0 and 'text' in result else 'UNAVAILABLE',
            'rc': result.get('rc'), 'value': scrub(result.get('text', '').strip()),
            'error': result.get('error')}


def identity(role):
    host, expected_ip = HOSTS[role]
    rows = query(['ip', '-j', '-4', 'address', 'show'])
    addresses = [a.get('local') for row in rows.get('data', []) for a in row.get('addr_info', [])
                 if a.get('family') == 'inet'] if isinstance(rows.get('data'), list) else []
    user = pwd.getpwuid(os.geteuid()).pw_name
    ok = socket.gethostname().split('.')[0] == host and expected_ip in addresses
    ok = ok and ((role == 'harbor' and os.geteuid() == 0) or (role == 'runner' and user == RUNNER_USER))
    return {'status': 'MATCHED' if ok else 'MISMATCH', 'expected_host': host,
            'host': socket.gethostname().split('.')[0], 'user': user, 'ipv4': addresses}


def metadata(path):
    try:
        info = Path(path).stat()
        return {'path': str(path), 'status': 'PRESENT', 'mode': oct(info.st_mode & 0o777),
                'uid': info.st_uid, 'gid': info.st_gid, 'bytes': info.st_size}
    except OSError as exc:
        return {'path': str(path), 'status': 'NOT_FOUND' if isinstance(exc, FileNotFoundError) else 'UNAVAILABLE',
                'error': type(exc).__name__}


def read_config(path):
    # Explicit config files only. Never recurse, source shell text, or read private keys.
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('CONFIG_NOT_REGULAR')
        data = stream.read(1048577)
    if len(data) > 1048576:
        raise ValueError('CONFIG_TOO_LARGE')
    return data.decode('utf-8')


def project_harbor_yaml(text):
    """Conservative scalar projection, NOT a general YAML validator."""
    result, seen, section, ambiguous = {}, set(), None, False
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if '\t' in line:
            ambiguous = True
            continue
        match = re.match(r'^( *)([A-Za-z_][A-Za-z_0-9]*):\s*(.*?)\s*$', line)
        if not match:
            ambiguous = True
            continue
        space, key, raw = match.groups()
        if not space:
            section = key
        selected = (not space and key == 'hostname') or (section == 'https' and space and key in ('port', 'certificate', 'private_key'))
        if not selected:
            continue
        name = key if not space else 'https.' + key
        if name in seen:
            ambiguous = True
        seen.add(name)
        # Paths and hostname only; reject substitutions, aliases, inline objects, spaces.
        raw = re.split(r'\s+#', raw, maxsplit=1)[0].strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'"):
            raw = raw[1:-1]
        if not re.fullmatch(r'[A-Za-z0-9_./:-]+', raw):
            ambiguous = True
            continue
        if key in ('certificate', 'private_key') and not raw.startswith('/'):
            ambiguous = True
            continue
        result[name] = raw
    return {'fields': result, 'projection_status': 'REVIEW_REQUIRED' if ambiguous else 'SCALARS_COLLECTED',
            'configuration_validated': False}


def project_nginx(text):
    result = []
    for line in text.splitlines():
        match = re.fullmatch(r'\s*(ssl_certificate|ssl_certificate_key)\s+["\x27]?(/[A-Za-z0-9_./-]+)["\x27]?;\s*(?:#.*)?', line)
        if match:
            result.append({'directive': match.group(1), 'path': match.group(2)})
    return {'directives': result, 'configuration_validated': False,
            'note': 'Only this file is projected; includes and running process configuration are not verified.'}


def config_record(path, projector):
    entry = metadata(path)
    if entry['status'] == 'PRESENT':
        try:
            entry.update(projector(read_config(path)))
        except (OSError, ValueError, UnicodeError) as exc:
            entry.update(status='UNAVAILABLE', error=type(exc).__name__)
    return entry


def harbor_inventory(extra_configs):
    containers = {name: query(['docker', '--host', 'unix:///var/run/docker.sock', 'inspect',
                              '--type', 'container', '--format', FORMAT, name]) for name in NAMES}
    roots = {'/home/server08/harbor', '/home/fdsadmin/harbor'}
    compose_files = set()
    for record in containers.values():
        data = record.get('data', {})
        root = data.get('compose_working_dir') if isinstance(data, dict) else None
        if root and re.fullmatch(r'/[A-Za-z0-9_./-]+', root):
            roots.add(root)
        for candidate in (data.get('compose_config_files') or '').split(','):
            if re.fullmatch(r'/[A-Za-z0-9_./-]+', candidate):
                compose_files.add(candidate)
    configs = {str(Path(root) / 'harbor.yml') for root in roots} | set(extra_configs)
    nginx_paths = {str(Path(root) / 'common/config/nginx/nginx.conf') for root in roots}
    nginx = containers['nginx'].get('data', {})
    for mount in nginx.get('mounts', []) if isinstance(nginx, dict) else []:
        source = mount.get('source', '')
        if not re.fullmatch(r'/[A-Za-z0-9_./-]+', source):
            continue
        if mount.get('destination') == '/etc/nginx/nginx.conf':
            nginx_paths.add(source)
        elif mount.get('destination') == '/etc/nginx':
            nginx_paths.add(str(Path(source) / 'nginx.conf'))
    return {'containers': containers,
            'harbor_config_candidates': [config_record(p, project_harbor_yaml) for p in sorted(configs)],
            'nginx_config_candidates': [config_record(p, project_nginx) for p in sorted(nginx_paths)],
            'compose_file_metadata': [metadata(p) for p in sorted(compose_files)],
            'canonical_installation': 'OWNER_CONFIRMATION_REQUIRED',
            'approved_project_ca_exists': 'UNKNOWN',
            'note': 'Compose labels and candidate file presence do not prove an authoritative installation. Certificate/key paths are references only; their files are not opened.'}


def resolver_servers():
    try:
        text = read_config('/etc/resolv.conf')
    except (OSError, ValueError, UnicodeError):
        return None
    servers = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0] == 'nameserver':
            try:
                servers.append(str(ipaddress.ip_address(fields[1])))
            except ValueError:
                continue
    return servers


def github_probe(host):
    resolved = run(['getent', 'ahostsv4', host])
    if resolved.get('rc') != 0:
        return {'host': host, 'status': 'DNS_FAILED'}
    try:
        candidates = list(dict.fromkeys(line.split()[0] for line in resolved.get('text', '').splitlines() if line.split()))
        address = next(ip for ip in candidates if ipaddress.ip_address(ip).is_global)
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        with socket.create_connection((address, 443), timeout=5, source_address=('10.250.10.20', 0)) as connection:
            with context.wrap_socket(connection, server_hostname=host) as secure:
                return {'host': host, 'status': 'TLS_VERIFIED', 'resolved_ip': address,
                        'local_source': secure.getsockname()[0], 'tls_version': secure.version()}
    except (OSError, ValueError, StopIteration) as exc:
        return {'host': host, 'status': 'FAILED', 'error': type(exc).__name__}


def runner_inventory(runner_dir, network):
    commands = {'git': ['git', '--version'], 'docker_client': ['docker', '--version'],
                'podman': ['podman', '--version'], 'trivy': ['trivy', '--version'],
                'kubectl_client': ['kubectl', 'version', '--client=true', '-o', 'json'],
                'openssl': ['openssl', 'version'], 'curl': ['curl', '--version'],
                'tar': ['tar', '--version']}
    tools = {name: text_query(argv) for name, argv in commands.items()}
    services = text_query(['systemctl', 'list-units', '--all', '--no-pager', '--no-legend', 'actions.runner.*.service'])
    unit_files = text_query(['systemctl', 'list-unit-files', '--no-pager', '--no-legend', 'actions.runner.*.service'])
    roots = [Path(runner_dir)] if runner_dir else [Path(pwd.getpwuid(os.geteuid()).pw_dir) / 'actions-runner', Path('/opt/actions-runner')]
    installations = []
    for root in roots:
        installations.append({'directory': metadata(root), 'files': {name: metadata(root / name) for name in ('config.sh', 'run.sh', 'svc.sh', 'bin/Runner.Listener')},
                              'note': 'File metadata only; neither configuration nor credential contents are read. Other install paths are not searched.'})
    servers = resolver_servers()
    probes = [{'status': 'NOT_REQUESTED'}]
    if network:
        if not servers or set(servers) & {'10.1.93.52', '10.1.93.53'}:
            probes = [{'status': 'SKIPPED_DNS_SCOPE_REVIEW', 'note': 'Resolver scope needs confirmation under the existing IF-13M baseline.'}]
        else:
            probes = [github_probe(host) for host in ('github.com', 'api.github.com')]
    return {'tools': tools, 'services': services, 'service_unit_files': unit_files,
            'installation_candidates': installations, 'resolver_servers': servers,
            'github_probes': probes, 'github_registration': 'NOT_VERIFIED', 'job_reception': 'NOT_VERIFIED',
            'note': 'Empty services or candidate paths do not prove no runner exists. Two direct TLS probes do not cover Actions wildcard/CDN/proxy connectivity or job execution.'}


def save_report(report, output):
    base = Path(output).resolve()
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    folder = Path(tempfile.mkdtemp(prefix='P1_CICD_Parallel_', dir=base))
    doc = folder / 'preparation.json'
    doc.write_text(json.dumps(scrub(report), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    sums = folder / 'SHA256SUMS'
    sums.write_text(hashlib.sha256(doc.read_bytes()).hexdigest() + '  preparation.json\n')
    archive = folder / ('P1-CICD-01_Parallel_' + report['role'] + '_Result.tar.gz')
    with tarfile.open(archive, 'w:gz') as target:
        for path in (doc, sums):
            info = target.gettarinfo(str(path), arcname=path.name)
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            info.mode = 0o600
            with path.open('rb') as stream:
                target.addfile(info, stream)
    side = Path(str(archive) + '.sha256')
    side.write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + '  ' + archive.name + '\n')
    # Hand back only newly created output files to the real sudo invoker.
    owner = None
    if os.geteuid() == 0 and os.environ.get('SUDO_UID', '').isdigit():
        account = pwd.getpwuid(int(os.environ['SUDO_UID']))
        if account.pw_uid != 0:
            owner = (account.pw_uid, account.pw_gid)
    for path in (folder, doc, sums, archive, side):
        os.chmod(path, 0o700 if path == folder else 0o600)
        if owner:
            os.chown(path, *owner)
    return doc, archive, side


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role', required=True, choices=HOSTS)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--runner-dir', help='Known runner install directory; metadata only.')
    parser.add_argument('--github-network', action='store_true', help='Runner only; verify TLS to github.com and api.github.com.')
    parser.add_argument('--harbor-config', action='append', default=[], help='Explicit harbor.yml path; selected scalar fields only.')
    args = parser.parse_args(argv)
    if (args.role == 'runner' and args.harbor_config) or (args.role == 'harbor' and (args.github_network or args.runner_dir)):
        parser.error('Options do not match role.')
    if any(Path(p).name != 'harbor.yml' or not Path(p).is_absolute() for p in args.harbor_config):
        parser.error('--harbor-config must be an absolute path ending in harbor.yml.')
    os.umask(0o077)
    ident = identity(args.role)
    if ident['status'] != 'MATCHED':
        print('COLLECTION_COMPLETE=false\nREASON=HOST_IP_OR_USER_MISMATCH\nDELIVERY_GATE=NOT_VERIFIED')
        return 3
    report = {'schema': 'P1-CICD-01-parallel-preparation-' + VERSION, 'role': args.role,
              'started_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'identity': ident,
              'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'platform': platform.platform(), 'architecture': platform.machine(),
              'python_version': platform.python_version(), 'cpu_count': os.cpu_count(),
              'time': text_query(['timedatectl', 'show', '-p', 'Timezone', '-p', 'NTPSynchronized']),
              'selinux': text_query(['getenforce']),
              'changes_applied': False, 'delivery_gate': 'NOT_VERIFIED'}
    report['inventory'] = (runner_inventory(args.runner_dir, args.github_network) if args.role == 'runner'
                           else harbor_inventory(args.harbor_config))
    report['finished_at_utc'] = dt.datetime.now(dt.timezone.utc).isoformat()
    report['collection_complete'] = True
    report['preparation_status'] = 'REVIEW_REQUIRED'
    report['limitations'] = ['Collection complete means the collector finished, not that all commands succeeded.',
                             'Per-item UNAVAILABLE/REVIEW_REQUIRED states must be reviewed; no automated deployment readiness verdict.',
                             'No private key/certificate files, credentials, process arguments, application logs, full Docker inspect or full config files are included in the archive.',
                             'No certificate generation, trust installation, package/service changes, runner registration, push or deployment.',
                             'CA existence and installation ownership require human asset records; unusual sensitive path names require owner review before sharing.']
    doc, archive, side = save_report(report, args.output_dir)
    print('COLLECTION_COMPLETE=true\nPREPARATION_STATUS=REVIEW_REQUIRED\nCHANGES_APPLIED=false\nDELIVERY_GATE=NOT_VERIFIED')
    print('REPORT=' + str(doc) + '\nARCHIVE=' + str(archive) + '\nSIDECAR=' + str(side))
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
