"""Explicit native-RUN file placement, not source/grant/baseline adoption.

Called by the existing native builder only after inert site assembly and before
READY. Reuses existing POSIX readers/durable O_EXCL writers. No authority file,
credentials, extraction, path discovery, overwrite, retry or automatic deletion.
"""
from dataclasses import dataclass, field
import os
from pathlib import PurePosixPath

import delivery_adapter as core
import delivery_fds_prepared_environment as prepared
import delivery_fds_site_inputs as profile
import delivery_job as job
import delivery_site as site
import delivery_store as store
from delivery_fds_private_baseline import PrivateBaselineFile


class RunInputError(RuntimeError):
    """Fixed public code; private file bytes/paths and OS errors stay private."""


def need(ok):
    if not ok:
        raise RunInputError('P2_RUN_INPUT_PREPARATION_FAILED')


@dataclass(frozen=True)
class RunInputFiles:
    baseline_source_root: str = field(repr=False)
    baseline_file: PrivateBaselineFile = field(repr=False)


def files_checked(files):
    """Inert explicit CODE metadata, not a file/grant loader or existence check."""
    need(type(files) is RunInputFiles and type(files.baseline_file) is PrivateBaselineFile
         and type(files.baseline_source_root) is str)
    root = files.baseline_source_root
    path = PurePosixPath(root)
    need(path.is_absolute() and str(path) == root and '..' not in path.parts
         and '\\' not in root and all(ord(c) >= 32 for c in root)
         and type(files.baseline_file.name) is str
         and site.NAMES.fullmatch(files.baseline_file.name)
         and type(files.baseline_file.sha256) is str
         and core.HEX.fullmatch(files.baseline_file.sha256))


def run_input_root(context):
    """Derive a lexical path from an ACTUAL driver context; no future RUN IDs."""
    need(type(context) is job.JobContext)
    context.check()
    return prepared.ROOT + '/inputs/' + context.run_id + '-' + str(context.attempt)


def prepare_run_inputs(config, context, *, files):
    """Copy three hash-selected inputs into a NEW RUN directory, before READY.

    The parent/template and explicit baseline staging root must already exist.
    Root0700/files0600 and current UID are enforced by the existing FD boundary;
    the native driver separately checks the expected Runner/UID before building.
    No return value is an authority. Partial directories remain for inspection;
    the same directory is never reused, repaired or cleaned up automatically.
    """
    fds, raws = [], []
    error = None
    try:
        files_checked(files)
        b = profile._binding_checked(config)
        need(os.name == 'posix' and str(config.input_root) == run_input_root(context))
        names = (config.rendered_name, config.contract_name, files.baseline_file.name)
        need(len(set(names)) == 3)
        destination = PurePosixPath(str(config.input_root))
        source = PurePosixPath(str(files.baseline_source_root))
        need(source != destination and destination not in source.parents)
        template, template_fd = store.root_open(prepared.INPUT_TEMPLATE_ROOT)
        fds.append(template_fd)
        raws.append(site.read_input(template_fd, 'rendered.yaml', b.rendered_sha256))
        raws.append(site.read_input(template_fd, 'contract.yaml', b.contract_sha256))
        site.root_stable(template, template_fd)
        baseline, baseline_fd = store.root_open(files.baseline_source_root)
        fds.append(baseline_fd)
        raws.append(site.read_input(baseline_fd, files.baseline_file.name, files.baseline_file.sha256))
        site.root_stable(baseline, baseline_fd)
        parent, parent_fd = store.root_open(prepared.ROOT + '/inputs')
        fds.append(parent_fd)
        # mkdir is exclusive: existing complete/partial dirs and symlinks fail.
        os.mkdir(destination.name, 0o700, dir_fd=parent_fd)
        os.fsync(parent_fd)
        output_fd = os.open(destination.name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd)
        fds.append(output_fd)
        need(store.inode(store.directory(output_fd)) == store.inode(
            os.stat(destination.name, dir_fd=parent_fd, follow_symlinks=False)))
        output = config.input_root
        site.root_stable(output, output_fd)
        for name, raw in zip(names, raws):
            store.create_file(output_fd, name, raw)
        expected = (b.rendered_sha256, b.contract_sha256, files.baseline_file.sha256)
        for name, sha in zip(names, expected):
            site.read_input(output_fd, name, sha)
        need(set(os.listdir(output_fd)) == set(names))
        site.root_stable(template, template_fd)
        site.root_stable(baseline, baseline_fd)
        site.root_stable(output, output_fd)
        site.root_stable(parent, parent_fd)
        os.fsync(output_fd); os.fsync(parent_fd)
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt): error = KeyboardInterrupt()
        elif isinstance(exc, SystemExit): error = SystemExit(130)
        else: error = RunInputError('P2_RUN_INPUT_PREPARATION_FAILED')
    finally:
        for fd in reversed(fds):
            try:
                os.close(fd)
            except BaseException as exc:
                if not isinstance(error, (KeyboardInterrupt, SystemExit)):
                    if isinstance(exc, KeyboardInterrupt): error = KeyboardInterrupt()
                    elif isinstance(exc, SystemExit): error = SystemExit(130)
                    else: error = RunInputError('P2_RUN_INPUT_PREPARATION_FAILED')
        raws.clear(); fds.clear()
    raw = files = config = context = template = baseline = parent = output = None
    if error is not None:
        raise error from None
