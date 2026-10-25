"""Reviewed metadata + private native archive, without publishing raw objects.

The trusted site code explicitly adopts the association between the pinned
metadata comment, file digest/root and the SAME per-RUN grant/Binding. A file
hash or a successfully authenticated comment alone is not that decision.
No comment parser, actual file selection, grant synthesis or fallback exists.
"""
from dataclasses import dataclass, field
import os

import delivery_adapter as core
import delivery_fds_baseline_archive as archive
import delivery_runtime_site as runtime
import delivery_site as site
import delivery_store as store
from delivery_adopted_authority import AdoptedGrant, CodeAdoptedGrantResolver, pin_identity
from delivery_github_source import CommentPin
from delivery_object_reader import BearerCredential
from delivery_verifier import Progress, ReadRequest


@dataclass(frozen=True)
class PrivateBaselineFile:
    name: str = field(repr=False)
    sha256: str = field(repr=False)


def private_metadata_checked(binding, grant, root_name, selected):
    """Inert reviewed CODE inputs; neither comment nor archive selects these."""
    archive.need(type(grant) is AdoptedGrant and grant.binding == binding
        and type(grant.original_pin) is CommentPin
        and type(root_name) is str and archive.ROOT_NAME.fullmatch(root_name)
        and type(selected) is PrivateBaselineFile
        and type(selected.name) is str and site.NAMES.fullmatch(selected.name)
        and type(selected.sha256) is str and core.HEX.fullmatch(selected.sha256))
    pin_identity(grant.original_pin); pin_identity(grant.control_pin)


def _read_archive(config, selected):
    # Same bounded POSIX descriptor checks as rendered/contract inputs. No
    # extraction, path search, permission repair or shared-folder access here.
    site.configuration_checked(config)
    archive.need(os.name == 'posix')
    root, fd = store.root_open(config.input_root)
    try:
        raw = site.read_input(fd, selected.name, selected.sha256)
        site.root_stable(root, fd)
        return raw
    finally:
        os.close(fd)


def connect_authenticated_private_baseline(config, plan, *, grant, root_name,
        selected, readers, credential, deadline, wall, monotonic):
    """Post-handoff private file -> existing plan under fresh original/control.

    Checks comment/control before file access AND after parsing, under the same
    request, memory credential and deadline. File bytes pass existing manifest,
    exact-six-object, owner/spec/previous checks. Owns only its resolver and FDs;
    readers, credential and plan are borrowed. Never acquires a claim or writes.
    """
    resolver = original = raw = request = result = error = None
    try:
        site.configuration_checked(config)  # Reject bad paths before source I/O.
        archive.need(type(plan) is runtime.RuntimeAuthorityPlan and plan.baseline is None
            and type(readers) is runtime.SourceReaders
            and type(credential) is BearerCredential and core.finite(deadline)
            and callable(wall) and callable(monotonic))
        readers.check()
        private_metadata_checked(config.binding, grant, root_name, selected)
        progress = Progress()
        request = ReadRequest(config.binding, credential, progress, deadline)
        resolver = CodeAdoptedGrantResolver(grant=grant, control_reader=readers.control,
            credential=credential, progress=lambda: progress, wall=wall, monotonic=monotonic)
        resolver.check(request)
        original = readers.original.comment(request, grant.original_pin)
        resolver(request, original)
        resolver.check(request)  # No private file access with stale authority.
        raw = _read_archive(config, selected)
        resolver.check(request)
        result = archive.connect_baseline_archive(config, plan, archive=raw,
            expected_sha256=selected.sha256, root_name=root_name, grant=grant)
        resolver.check(request)
        # Same pinned ID/update/body digest: any edit/withdrawal during local
        # read/parse fails; the existing resolver also fetches fresh control.
        original = readers.original.comment(request, grant.original_pin)
        resolver(request, original)
        resolver.check(request)
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt): error = KeyboardInterrupt()
        elif isinstance(exc, SystemExit): error = SystemExit(130)
        else: error = archive.BaselineArchiveError('FDS_PRIVATE_BASELINE_SOURCE_INVALID')
    finally:
        if resolver is not None:
            try:
                resolver.close()
            except BaseException as exc:
                # Keep an existing interrupt; otherwise preserve cleanup-time
                # interrupts without leaking a foreign close exception/body.
                if not isinstance(error, (KeyboardInterrupt, SystemExit)):
                    if isinstance(exc, KeyboardInterrupt): error = KeyboardInterrupt()
                    elif isinstance(exc, SystemExit): error = SystemExit(130)
                    else: error = archive.BaselineArchiveError('FDS_PRIVATE_BASELINE_SOURCE_INVALID')
    original = raw = resolver = readers = credential = plan = request = selected = None
    if error is not None:
        result = None
        raise error from None
    return result
