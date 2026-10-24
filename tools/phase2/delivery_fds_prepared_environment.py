"""Observed P2 private source/parser preparation; never an authority loader.

Paths/bytes were prepared and read back on the adopted Runner. They are not a
live readiness check, actual RUN inputs, grant, baseline or configured SITE.
"""
from pathlib import PurePosixPath
import sys
from delivery_fds_site_inputs import SOURCE_COMMIT, CONTRACT_SHA256, RENDERED_SHA256
from producer_bridge import yaml_module, PYYAML_VERSION

OBSERVED_AT = '2026-10-24T18:05:46.334767+00:00'
ROOT = '/home/server01_cicd-runner/fds-p2-delivery'
SOURCE_ROOT = ROOT + '/sources/' + SOURCE_COMMIT
INPUT_TEMPLATE_ROOT = ROOT + '/inputs/prepared-' + SOURCE_COMMIT
STORE_ROOT = ROOT + '/state'
RUNTIME_ROOT = ROOT + '/runtime-py312-yaml603'
RUNTIME_PYTHON = RUNTIME_ROOT + '/bin/python3'
PYYAML_WHEEL_SHA256 = 'ba1cc08a7ccde2d2ec775841541641e4548226580ab850948cbfda66a1befcdc'


class PreparedEnvironmentError(RuntimeError):
    """Fixed public code, no environment/package traceback or credentials."""


def configuration_paths():
    """Known byte/cache/store paths for EXISTING build_configuration kwargs.

    The caller supplies actual RUN binding/receipts/budget and per-run input_root.
    The prepared input template is not automatically used as a delivery RUN.
    """
    return dict(source_root=SOURCE_ROOT, store_root=STORE_ROOT,
                rendered_name='rendered.yaml', contract_name='contract.yaml')


def check_interpreter():
    """Private P2 interpreter/version check before existing native job entry.

    Uses the existing exact parser version guard. This is installation/routing
    sanity, not package provenance, current host identity or execution authority.
    No network, source reads, login, TokenRequest, claim or global SITE change.
    """
    invalid = False
    try:
        if sys.version_info[:2] != (3, 12) or sys.prefix != RUNTIME_ROOT:
            raise ValueError()
        parser = yaml_module()
        path = PurePosixPath(parser.__file__)
        if not (path.is_absolute() and '..' not in path.parts
                and path.is_relative_to(PurePosixPath(RUNTIME_ROOT))):
            raise ValueError()
    except Exception:
        invalid = True
    if invalid:
        raise PreparedEnvironmentError('P2_PRIVATE_RUNTIME_REJECTED') from None


def selection_status():
    return {'observed_at': OBSERVED_AT, 'source_root': SOURCE_ROOT,
        'input_template_root': INPUT_TEMPLATE_ROOT, 'store_root': STORE_ROOT,
        'runtime_python': RUNTIME_PYTHON, 'expected_pyyaml': PYYAML_VERSION,
        'wheel_sha256': PYYAML_WHEEL_SHA256, 'contract_sha256': CONTRACT_SHA256,
        'rendered_sha256': RENDERED_SHA256, 'source_mode': 'BARE_SHALLOW_PINNED_COMMIT',
        'input_template_is_run': False, 'actual_native_run_id': None,
        'execution_authorized': False, 'live_checked': False}
