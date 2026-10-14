"""P2 consumer bridge to the reviewed #43 checker in a committed Git snapshot.

No cluster access or approval inference. Reads only commit objects, never imports
working-tree Python. Does NOT prove the render was built from that source; the
trusted authorizer still needs the clean build/render and approval receipts.
Optional consumer dependency: the separately tested PyYAML 6.0.3 environment.
"""
import os
import re
import subprocess
from pathlib import Path

import delivery_adapter as core

CHECKER_PATH = 'tools/check_delivery_render.py'
CONTRACT_PATH = 'deploy/delivery/rosa.yaml'
# Original blob at fds-msa 6e7acf1053b1e44164d38851e477a2d2d64b9c3a.
# Updating this executable-code pin needs a new source review, not a receipt flag.
CHECKER_SHA256 = 'b889d6123fe0d566424a17ab88a6e41e49696bb1bb572b16ed88f22a654df2e1'
PYYAML_VERSION = '6.0.3'
MAX_BYTES = 1024 * 1024


class BridgeError(RuntimeError):
    """Only static codes; do not include source text, YAML errors or git stderr."""


def need(ok, code):
    if not ok:
        raise BridgeError(code)


class GitSnapshot:
    """Read only two fixed paths at a full commit. HEAD/worktree are irrelevant.

    This identifies committed bytes, not review/approval or a clean build. Git,
    Python and their installation/environment must themselves be trusted.
    Missing local objects fail; protocols and automatic lazy fetch are disabled.
    """
    def __init__(self, root, commit):
        need(type(commit) is str and re.fullmatch(r'[0-9a-f]{40}', commit), 'SOURCE_INVALID')
        self.root, self.commit = Path(root).resolve(strict=True), commit
        self.env = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}
        self.env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0', GIT_NO_LAZY_FETCH='1')
        # Probe the actual binary instead of guessing support from a version
        # string. No repository access or fallback that could enable lazy fetch.
        require_local_only_git(self.env)
        need(self._git('-t', commit).strip() == b'commit', 'SOURCE_NOT_COMMIT')

    def _git(self, *args):
        try:
            command = ['git', '--no-replace-objects', '--no-lazy-fetch',
                       '-c', 'protocol.allow=never', '-c', 'core.fsmonitor=false',
                       '-C', str(self.root), 'cat-file', *args]
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    stdin=subprocess.DEVNULL, env=self.env, timeout=15, check=False)
            need(result.returncode == 0, 'SOURCE_READ_FAILED')
            return result.stdout
        except Exception:
            raise BridgeError('SOURCE_READ_FAILED') from None

    def blob(self, path):
        need(path in (CHECKER_PATH, CONTRACT_PATH), 'SOURCE_PATH_INVALID')
        spec = self.commit + ':' + path
        size_raw = self._git('-s', spec).strip()
        need(re.fullmatch(rb'[0-9]{1,8}', size_raw), 'SOURCE_SIZE_INVALID')
        size = int(size_raw)
        need(0 < size <= MAX_BYTES, 'SOURCE_SIZE_INVALID')
        raw = self._git('blob', spec)
        need(len(raw) == size, 'SOURCE_SIZE_CHANGED')
        return raw


def require_local_only_git(env):
    try:
        result = subprocess.run(['git', '--no-replace-objects', '--no-lazy-fetch', '--version'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL, env=env, timeout=15, check=False)
        need(result.returncode == 0, 'GIT_LOCAL_ONLY_UNAVAILABLE')
    except Exception:
        raise BridgeError('GIT_LOCAL_ONLY_UNAVAILABLE') from None


def yaml_module():
    import yaml  # From the consumer environment, not a producer checkout path.
    need(yaml.__version__ == PYYAML_VERSION, 'PARSER_VERSION_INVALID')
    return yaml


def strict_documents(raw, yaml):
    """Reject ambiguous/recursive input before running the unmodified checker."""
    need(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES, 'INPUT_SIZE_INVALID')
    text = raw.decode('utf-8')
    depth = count = 0
    opening = (yaml.BlockMappingStartToken, yaml.BlockSequenceStartToken,
               yaml.FlowMappingStartToken, yaml.FlowSequenceStartToken)
    closing = (yaml.BlockEndToken, yaml.FlowMappingEndToken, yaml.FlowSequenceEndToken)
    for token in yaml.scan(text):
        count += 1
        need(count <= 100000, 'YAML_COMPLEXITY_LIMIT')
        need(not isinstance(token, (yaml.AliasToken, yaml.AnchorToken, yaml.TagToken)),
             'YAML_INDIRECTION_FORBIDDEN')
        if isinstance(token, opening):
            depth += 1
            need(depth <= 48, 'YAML_COMPLEXITY_LIMIT')
        elif isinstance(token, closing):
            depth -= 1

    class StrictLoader(yaml.SafeLoader):
        pass

    def mapping(loader, node):
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=True)
            need(type(key) is str and key not in result and key != '<<', 'YAML_MAPPING_INVALID')
            result[key] = loader.construct_object(value_node, deep=True)
        return result

    StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    documents = list(yaml.load_all(text, Loader=StrictLoader))
    need(len(documents) <= 6 and all(type(doc) is dict for doc in documents), 'DOCUMENT_SET_INVALID')
    # Reject non-JSON scalar types (timestamps, sets, etc.) and non-finite numbers.
    core.encoded(documents)
    return documents


class CommittedProducerBridge:
    """Bind caller-approved source/hash inputs without claiming their approval.

    A saved PASS report is never consumed. Each call runs exact pinned checker
    bytes read from binding.source_commit. The raw render is parsed and checked
    in memory. Contract comparison permits only CRLF/LF checkout differences;
    receipt hashes still refer to the exact ORIGINAL transferred bytes.
    """
    def __init__(self, root, binding):
        core.binding_checked(binding)
        self.root, self.binding = Path(root), binding

    def __call__(self, rendered, contract):
        try:
            b = self.binding
            need(all(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES for raw in (rendered, contract)),
                 'INPUT_SIZE_INVALID')
            need(core.digest(rendered) == b.rendered_sha256 and core.digest(contract) == b.contract_sha256,
                 'INPUT_HASH_MISMATCH')
            source = GitSnapshot(self.root, b.source_commit)
            checker = source.blob(CHECKER_PATH)
            need(core.digest(checker) == CHECKER_SHA256, 'CHECKER_NOT_REVIEWED')
            committed_contract = source.blob(CONTRACT_PATH)
            need(contract.replace(b'\r\n', b'\n') == committed_contract.replace(b'\r\n', b'\n'),
                 'CONTRACT_SOURCE_MISMATCH')
            yaml = yaml_module()
            contracts = strict_documents(contract, yaml)
            need(len(contracts) == 1, 'CONTRACT_DOCUMENT_INVALID')
            docs = strict_documents(rendered, yaml)
            need(len(docs) == 6, 'DOCUMENT_SET_INVALID')
            # Execute only the exact reviewed blob in fresh globals. No .pyc or
            # checkout imports; the checker imports the consumer's pinned yaml.
            module = {'__name__': '_p2_reviewed_producer_checker',
                      '__file__': str(self.root.resolve() / CHECKER_PATH)}
            exec(compile(checker, '<reviewed-p2-producer-checker>', 'exec'), module)
            report = module['check'](rendered.decode('utf-8'), 'app', contracts[0], allow_placeholders=False)
            need(type(report) is dict, 'PRODUCER_REPORT_INVALID')
            # Failures may quote submitted YAML. Return only controlled fields
            # and a static error; do not expose producer exception/error text.
            passed = report.get('result') == 'PASS' and report.get('errors') == []
            result = {key: report.get(key) for key in (
                'result', 'mode', 'namespace', 'placeholders_allowed', 'placeholders', 'approved_route_host')}
            result.update(result='PASS' if passed else 'FAIL', errors=[] if passed else ['PRODUCER_REJECTED'],
                          rendered_sha256=core.digest(rendered), contract_sha256=core.digest(contract),
                          producer_commit=b.source_commit, checker_sha256=CHECKER_SHA256,
                          parser_version=yaml.__version__,
                          render_generation='NOT_VERIFIED', source_approval='NOT_VERIFIED')
            return result, docs
        except BridgeError as exc:
            if str(exc) == 'GIT_LOCAL_ONLY_UNAVAILABLE':
                raise BridgeError('GIT_LOCAL_ONLY_UNAVAILABLE') from None
            raise BridgeError('PRODUCER_BRIDGE_FAILED') from None
        except Exception:
            raise BridgeError('PRODUCER_BRIDGE_FAILED') from None
