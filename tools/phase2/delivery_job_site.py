"""Fixed reviewed-code site declaration for the P2 workflow. NOT a loader.

Confirmed expected Runner and source/target selection are connected through
delivery_fds_operating.assemble_observed_native_job. The inert declaration
declare_observed_native_job(select_inputs=reviewed_python_callback) defers that
assembly to build(context, command), using the driver's actual native context.
Gate/status do not invoke that callback. Issuer operator assignment is recorded
separately from authentication. Transport/layout, original/control/finish and
full existing baseline remain incomplete. Populate SITE with that composition
root after these actual inputs are connected; a partial selection is NOT a
JobSite and must not enable self-hosted dispatch.
Import must not issue/read credentials, call servers, create claims or mutate
resources. The JobSite builder owns partial resources until invocation transfer.
Do not import the preparation JSON as runtime facts or authorization.
"""

SITE = None


def configuration_status():
    # Lazy diagnostic import only; no factories, credentials or I/O.
    from delivery_fds_operating import configuration_status as report
    return report()
