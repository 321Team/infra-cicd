"""Explicit FDS six-object EXISTING-target connection; no baseline discovery.

Requires actual reviewed BaselineAdoption bytes/owners and full previous images
from their real producer/adoption original. No source/current -> previous copy,
fresh GET adoption, deletion, approval generation or default configured SITE.
"""
from dataclasses import replace

import delivery_adapter as core
import delivery_fds_site_inputs as fds
import delivery_runtime_site as runtime
from delivery_adopted_authority import AdoptedGrant, pin_identity
from delivery_objects import decode, normalized


class FdsExistingInputError(RuntimeError):
    """Fixed public code, no original, policy, path, credential or body text."""


def need(ok):
    if not ok:
        raise FdsExistingInputError('FDS_EXISTING_INPUT_MISMATCH')


def validate_existing_plan(config, plan):
    """Shape/profile agreement, NOT authentication or owner-policy adoption.

    Return the SAME actual plan and immutable original bytes. Existing factory
    authenticates baseline original/control and fresh UID/spec/owner equality
    before claim/write. Missing raw/previous stays blocked, not synthesized.
    """
    try:
        fds._source_profile(config, plan)
        b, baseline, previous = config.binding, plan.baseline, plan.producer.facts.previous_images
        need(type(baseline) is runtime.BaselineAdoption
             and type(baseline.grant) is AdoptedGrant and baseline.grant.binding == b
             and type(baseline.objects) is tuple and len(baseline.objects) == 6
             and type(previous) is tuple and len(previous) == 2)
        pin_identity(baseline.grant.original_pin); pin_identity(baseline.grant.control_pin)
        prior_binding = replace(b, images=previous)
        core.binding_checked(prior_binding)
        need(core.services_for(prior_binding) == core.services_for(b))
        # Validate exact supplied raw; never rewrite/normalize it into evidence.
        seen = set()
        for key, raw in zip(core.objects_for(b), baseline.objects):
            obj = decode(raw)
            normalized(obj, key)  # Known volatility only; retains UID/spec/owners.
            uid = obj['metadata']['uid']
            need(uid not in seen); seen.add(uid)
            if key[1] == 'Deployment':
                pod = obj['spec']['template']['spec']
                containers = pod['containers']
                need(type(containers) is list and len(containers) == 1
                     and type(containers[0]) is dict and containers[0].get('name') == key[2]
                     and containers[0].get('image') == dict(previous)[key[2]]
                     and not pod.get('initContainers') and not pod.get('ephemeralContainers'))
        return plan
    except Exception:
        raise FdsExistingInputError('FDS_EXISTING_INPUT_MISMATCH') from None


def connect_existing_parent(*, config, finish_slot, expected_peer, routes, deadline,
                            wire, publish_ready, open_channel, monotonic,
                            finish_capture=None, producer_pins=None, plan=None, materialize_plan=None):
    """Explicit reviewed existing-target full/prepared plan, never auto-select.

    Uses the SAME native parent/peer/one-use source handoff and authority factory.
    The five-reader baseline role must be wired; current objects are not deleted.
    """
    from delivery_job_sources import SourceRoleWiring, ROLES
    if type(wire) is SourceRoleWiring:
        need(wire.binding == config.binding and wire.roles == ROLES + ('baseline',))
    return fds._connect_parent(validate_existing_plan, config=config, finish_slot=finish_slot,
        expected_peer=expected_peer, routes=routes, deadline=deadline, wire=wire,
        publish_ready=publish_ready, open_channel=open_channel, monotonic=monotonic,
        finish_capture=finish_capture, producer_pins=producer_pins, plan=plan,
        materialize_plan=materialize_plan)
