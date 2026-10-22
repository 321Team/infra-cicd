"""Explicit reviewed plan pins -> borrowed private source readers, no I/O.

This only replaces hand-ordered tuple assembly in a trusted session.wire.
Pins and repositories are NOT authority, adoption, authentication or a GO.
No actual/default repositories, future finish originals, loader or discovery.
"""
from delivery_adopted_authority import AdoptedGrant, pin_identity
from delivery_github_source import BlobPin, repository_checked
from delivery_private_source import PrivateGitHubOriginalAdapter
from delivery_source_session import PrivateSourceSession
from delivery_actions_source import policy_checked
import delivery_adapter as core
import delivery_runtime_site as runtime


class SourceRolesError(RuntimeError):
    """Only fixed codes; no source, repository, endpoint or callback text."""


SAFE_CODES = frozenset(('SOURCE_ROLES_CONFIGURATION_INVALID',
    'SOURCE_ROLES_PRODUCER_MISMATCH', 'SOURCE_ROLES_READER_INVALID', 'SOURCE_ROLES_UNAVAILABLE'))


def need(ok, code):
    if not ok:
        raise SourceRolesError(code)


def plan_source_roles(session, *, plan, finish_repositories, producer_pins=None):
    """Wire a consumed private session to the existing runtime role contract.

    finish_repositories is the reviewed (original_repo, control_repo), not a
    precreated/future finish pin. The actual terminal original remains required
    by FinishAdoptionSlot or the existing collector/observation configuration.
    Caller may add those typed finish roles to the returned dict afterwards.
    No ownership transfers; session/driver still close all exact owned handles.
    """
    try:
        need(type(session) is PrivateSourceSession
             and type(plan) is runtime.RuntimeAuthorityPlan
             and type(plan.approval) is AdoptedGrant
             and type(plan.issuer) is runtime.EvidenceAdoption
             and type(plan.producer) is runtime.EvidenceAdoption,
             'SOURCE_ROLES_CONFIGURATION_INVALID')
        binding = plan.approval.binding
        core.binding_checked(binding)
        need(plan.baseline is None or type(plan.baseline) is runtime.BaselineAdoption,
             'SOURCE_ROLES_CONFIGURATION_INVALID')
        need(binding.action == 'deploy' or plan.baseline is not None,
             'SOURCE_ROLES_CONFIGURATION_INVALID')
        need(type(finish_repositories) is tuple and len(finish_repositories) == 2,
             'SOURCE_ROLES_CONFIGURATION_INVALID')
        for repo in finish_repositories:
            repository_checked(repo)

        def repositories(grant):
            need(type(grant) is AdoptedGrant and type(grant.control_pin) is BlobPin
                 and grant.binding == binding, 'SOURCE_ROLES_CONFIGURATION_INVALID')
            pin_identity(grant.original_pin)
            pin_identity(grant.control_pin)
            return grant.original_pin.repository, grant.control_pin.repository

        # Validate all selected source pins before borrowing any session reader.
        scopes = [repositories(plan.approval), repositories(plan.issuer.grant),
                  repositories(plan.producer.grant), finish_repositories]
        if plan.baseline is not None:
            scopes.append(repositories(plan.baseline.grant))
        action_repo = None
        if producer_pins is not None:
            policy_checked(producer_pins)
            facts = plan.producer.facts
            need(tuple(pin.producer_reference for pin in producer_pins) ==
                 (facts.test_reference, facts.build_reference, facts.scan_reference,
                  facts.push_reference), 'SOURCE_ROLES_PRODUCER_MISMATCH')
            action_repo = producer_pins[0].run.repository

        def reader(repo):
            value = session.reader(repo)  # Exact configured scope, never fallback.
            need(type(value) is PrivateGitHubOriginalAdapter, 'SOURCE_ROLES_READER_INVALID')
            return value

        readers = tuple(runtime.SourceReaders(reader(original), reader(control))
                        for original, control in scopes)
        roles = {'readers': readers}
        if action_repo is not None:
            roles['producer_run_reader'] = reader(action_repo)
        return roles
    except Exception as error:
        code = error.args[0] if type(error) is SourceRolesError and len(error.args) == 1 else None
        code = code if type(code) is str and code in SAFE_CODES else 'SOURCE_ROLES_UNAVAILABLE'
        raise SourceRolesError(code) from None
