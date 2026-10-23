"""Inert, explicit FDS EXISTING-target JobSite assembly. No global SITE loader.

Combines reviewed 5-role source wiring, FDS existing-object plan checks, local
helper UID rendezvous and native owned builder. Never discovers actual values.
"""
import delivery_adapter as core
import delivery_crosshost_ingress as ingress
import delivery_fds_existing_inputs as existing
import delivery_job as job
import delivery_job_rendezvous as rendezvous
import delivery_job_sources as sources
import delivery_runtime_site as runtime


class SiteAssemblyError(RuntimeError):
    """Fixed codes only, no original/plan/callback exception or secret text."""


def need(ok):
    if not ok:
        raise SiteAssemblyError('P2_FDS_SITE_CONFIGURATION_INVALID')


def assemble_existing_job_site(*, runner, config, finish_slot, expected_peer,
        routes, wire, expected_helper_uid, deadline, monotonic, publish_ready,
        plan=None, materialize_plan=None, finish_capture=None, producer_pins=None):
    """Explicit actual inputs -> exact native JobSite, without executing it.

    Caller must select this existing-six-object profile. Full actual plan OR
    post-handoff materializer, never auto baseline capture/current->previous.
    publish_ready only notifies an authenticated independent sender; waiting for
    bridge completion here can deadlock before accept under socket backpressure.
    Constructor callbacks must not perform I/O. This function calls none of them.
    """
    owner = builder = None
    owns_prepared = False
    try:
        need(type(runner) is job.RunnerIdentity and 0 < runner.uid < 0xFFFFFFFF)
        runner.check()
        need(type(expected_helper_uid) is int and 0 < expected_helper_uid < 0xFFFFFFFF
             and type(expected_peer) is ingress.PeerIdentity
             and type(wire) is sources.SourceRoleWiring
             and wire.roles == sources.ROLES + ('baseline',)
             and wire._clock is monotonic
             and core.finite(deadline) and callable(monotonic) and callable(publish_ready))
        expected_peer.check()
        need((plan is None and callable(materialize_plan))
             or (plan is not None and materialize_plan is None))
        owns_prepared = plan is None  # Provenance fixed before constructor/return.
        # All actual config/plan/role/channel checks stay in existing profile and
        # parent. The owner allocates no FD/socket or public nonce at construction.
        owner = rendezvous.UnixJobRendezvous(expected_uid=expected_helper_uid,
            peer=expected_peer, deadline=deadline, monotonic=monotonic,
            publish_ready=publish_ready)
        builder = existing.connect_existing_parent(config=config, plan=plan,
            materialize_plan=materialize_plan, finish_slot=finish_slot,
            expected_peer=expected_peer, routes=routes, wire=wire,
            deadline=deadline, monotonic=monotonic,
            publish_ready=owner.publish_ready, open_channel=owner.open_channel,
            finish_capture=finish_capture, producer_pins=producer_pins)
        wrapped = rendezvous.OwnedRendezvousBuilder(builder, owner)
        value = job.JobSite(runner, wrapped)
        value.check()
        return value  # Inert owner/internal prepared transfer to native builder.
    except BaseException as exc:
        cleanup = True
        # Only the prepared plan made by THIS connector is ours. Borrowed full
        # plan, finish slot and wire are never closed on assembly failure.
        if owns_prepared and builder is not None:
            try:
                need(type(builder._plan) is runtime.PreparedAuthorityPlan)
                cleanup = builder._plan.close() is True
            except BaseException:
                cleanup = False
        if owner is not None:
            try:
                cleanup = owner.close() is True and cleanup
            except BaseException:
                cleanup = False
        if not cleanup:
            raise SiteAssemblyError('P2_FDS_SITE_CLEANUP_FAILED') from None
        if isinstance(exc, KeyboardInterrupt):
            raise KeyboardInterrupt() from None
        if isinstance(exc, SystemExit):
            raise SystemExit(130) from None
        raise SiteAssemblyError('P2_FDS_SITE_CONFIGURATION_INVALID') from None
