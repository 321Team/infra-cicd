"""Explicit repository-scoped source wiring after the native job's handoff.

No credentials, files, target discovery, policy parser or authority defaults.
Routes are reviewed CODE; actual originals/control meanings remain site-owned.
PrivateSourceSession owns every borrowed credential/transport through finish.
"""
from dataclasses import dataclass, field
import threading

import delivery_adapter as core
from delivery_adopted_authority import AdoptedGrant, pin_identity
from delivery_crosshost_ingress import _NoCopy
from delivery_finish_collector import (FinishCollectionPolicy, FinishCollectorConfig,
                                       policy_checked)
from delivery_finish_locator import NativeFinishLocator
from delivery_finish_observation import FinishObservationConfig, FOLLOWUPS
from delivery_github_source import RepositoryPin, BlobPin, repository_checked
from delivery_runtime_site import SourceReaders, RuntimeAuthorityPlan, EvidenceAdoption, BaselineAdoption
from delivery_source_session import PrivateSourceSession

ROLES = ('approval', 'platform', 'producer', 'finish')


class JobSourcesError(RuntimeError):
    """Fixed codes only; never originals, credentials or callback text."""


def need(ok, code):
    if not ok:
        raise JobSourcesError(code)


@dataclass(frozen=True, repr=False)
class RoleRoute:
    role: str
    original: RepositoryPin = field(repr=False)
    control: RepositoryPin = field(repr=False)


@dataclass(frozen=True, repr=False)
class ObservationRoute:
    # Explicit existing completion scope, NOT a copy from deploy approval.
    scope_grant: AdoptedGrant = field(repr=False)
    required_followups: tuple


@dataclass(frozen=True, repr=False)
class CollectionRoute:
    policy: FinishCollectionPolicy = field(repr=False)
    decode: object = field(repr=False)
    comment_id: int | None
    max_seconds: float


class SourceRoleWiring(_NoCopy):
    """One consumed PrivateSourceSession -> exact existing runtime role configs.

    Constructor is inert. Repositories must have distinct actual scoped source
    channels supplied by the parent; shared roles can borrow the same reader.
    No additional token, auth query, finish observation or native discovery is
    performed here. Configuration shape/routing never authenticates authority.
    """
    def __init__(self, *, binding, routes, finish=None, producer_repository=None,
                 wall, monotonic):
        try:
            core.binding_checked(binding)
            need(type(routes) is tuple and all(type(x) is RoleRoute for x in routes),
                 'P2_SOURCE_ROUTES_INVALID')
            need(tuple(x.role for x in routes) in (ROLES, ROLES + ('baseline',)),
                 'P2_SOURCE_ROUTES_INVALID')
            for route in routes:
                repository_checked(route.original)
                repository_checked(route.control)
            need(producer_repository is None or type(producer_repository) is RepositoryPin,
                 'P2_SOURCE_ROUTES_INVALID')
            if producer_repository is not None:
                repository_checked(producer_repository)
            need(callable(wall) and callable(monotonic), 'P2_SOURCE_ROUTES_INVALID')
            end = routes[3]
            if type(finish) is ObservationRoute:
                grant = finish.scope_grant
                need(type(grant) is AdoptedGrant and grant.binding == binding
                     and type(grant.control_pin) is BlobPin
                     and type(finish.required_followups) is tuple
                     and finish.required_followups == FOLLOWUPS,
                     'P2_SOURCE_FINISH_ROUTE_INVALID')
                pin_identity(grant.original_pin); pin_identity(grant.control_pin)
                need((grant.original_pin.repository, grant.control_pin.repository)
                     == (end.original, end.control), 'P2_SOURCE_FINISH_ROUTE_INVALID')
            elif type(finish) is CollectionRoute:
                policy_checked(finish.policy)
                need(finish.policy.binding == binding and callable(finish.decode)
                     and core.finite(finish.max_seconds) and 0.05 <= finish.max_seconds <= 120
                     and (finish.policy.location.repository, finish.policy.control_pin.repository)
                     == (end.original, end.control), 'P2_SOURCE_FINISH_ROUTE_INVALID')
                from delivery_finish_collector import BlobLocation, CommentLocation
                need((type(finish.policy.location) is BlobLocation and finish.comment_id is None)
                     or (type(finish.policy.location) is CommentLocation
                         and type(finish.comment_id) is int and finish.comment_id > 0),
                     'P2_SOURCE_FINISH_ROUTE_INVALID')
            else:
                need(finish is None, 'P2_SOURCE_FINISH_ROUTE_INVALID')
        except Exception:
            raise JobSourcesError('P2_SOURCE_CONFIGURATION_INVALID') from None
        self._binding, self._routes, self._finish = binding, routes, finish
        self._producer, self._wall, self._clock = producer_repository, wall, monotonic
        self._used, self._lock = False, threading.Lock()

    def __repr__(self):
        return '<P2 source role wiring: private, single-use, not authority>'

    @property
    def binding(self):
        return self._binding

    def check_channels(self, binding, routes):
        """Before public readiness: required repositories must be explicitly routed."""
        from delivery_crosshost_ingress import SourceRoute
        try:
            need(binding == self.binding and type(routes) is tuple
                 and all(type(x) is SourceRoute for x in routes),
                 'P2_SOURCE_CHANNEL_ROUTES_INVALID')
            available = {x.repository for x in routes}
            required = {repo for route in self._routes for repo in (route.original, route.control)}
            if self._producer is not None:
                required.add(self._producer)
            need(required <= available, 'P2_SOURCE_CHANNEL_ROUTES_INVALID')
        except Exception:
            raise JobSourcesError('P2_SOURCE_CHANNEL_ROUTES_INVALID') from None

    def check_plan(self, plan):
        """Routing/shape only, after plan construction; never authenticate facts."""
        try:
            need(type(plan) is RuntimeAuthorityPlan and type(plan.approval) is AdoptedGrant
                 and type(plan.issuer) is EvidenceAdoption and type(plan.producer) is EvidenceAdoption,
                 'P2_SOURCE_PLAN_ROUTE_INVALID')
            grants = (plan.approval, plan.issuer.grant, plan.producer.grant)
            if len(self._routes) == 5:
                need(type(plan.baseline) is BaselineAdoption, 'P2_SOURCE_PLAN_ROUTE_INVALID')
                grants += (plan.baseline.grant,)
            else:
                need(plan.baseline is None, 'P2_SOURCE_PLAN_ROUTE_INVALID')
            selected = self._routes[:3] + self._routes[4:]
            for route, grant in zip(selected, grants):
                need(type(grant) is AdoptedGrant and grant.binding == self.binding
                     and type(grant.control_pin) is BlobPin, 'P2_SOURCE_PLAN_ROUTE_INVALID')
                pin_identity(grant.original_pin); pin_identity(grant.control_pin)
                need((grant.original_pin.repository, grant.control_pin.repository)
                     == (route.original, route.control), 'P2_SOURCE_PLAN_ROUTE_INVALID')
        except Exception:
            raise JobSourcesError('P2_SOURCE_PLAN_ROUTE_INVALID') from None

    def __call__(self, session):
        with self._lock:
            need(not self._used, 'P2_SOURCE_WIRING_CONSUMED')
            self._used = True  # Failure also consumes this wiring; no hidden retry.
        locator = None
        try:
            need(type(session) is PrivateSourceSession and session._state == 'CONSUMED',
                 'P2_SOURCE_SESSION_NOT_RECEIVED')
            # Session clock owns the existing capped handoff budget. Never reset it.
            need(session.now() < session.deadline, 'P2_SOURCE_WIRING_EXPIRED')
            readers = tuple(SourceReaders(session.reader(x.original), session.reader(x.control))
                            for x in self._routes)
            for reader in readers:
                reader.check()
            result = {'readers': readers}
            if self._producer is not None:
                result['producer_run_reader'] = session.reader(self._producer)
            finish = self._finish
            if type(finish) is ObservationRoute:
                config = FinishObservationConfig(finish.scope_grant, readers[3],
                                                  finish.required_followups)
                config.check(self.binding)
                result['finish_observation'] = config
            elif type(finish) is CollectionRoute:
                locator = NativeFinishLocator(policy=finish.policy, reader=readers[3].original,
                    decode=finish.decode, comment_id=finish.comment_id,
                    wall=self._wall, monotonic=self._clock)
                config = FinishCollectorConfig(finish.policy, readers[3], locator,
                                               finish.max_seconds)
                config.check()
                result['finish_collection'] = config
            need(session.now() < session.deadline, 'P2_SOURCE_WIRING_EXPIRED')
            return result
        except KeyboardInterrupt:
            if locator is not None:
                locator.close()
            raise KeyboardInterrupt() from None
        except SystemExit:
            if locator is not None:
                locator.close()
            raise SystemExit(130) from None
        except Exception:
            if locator is not None:
                locator.close()
            raise JobSourcesError('P2_SOURCE_WIRING_FAILED') from None
