"""Fixed reviewed-code site declaration for the P2 workflow. NOT a loader.

Actual issuer/source/credential/paths/scope remain unadopted. Populate SITE in
an independently reviewed code change only after those originals are available.
Import must not issue/read credentials, call servers, create claims or mutate
resources. The JobSite builder owns partial resources until invocation transfer.
Do not import the preparation JSON as runtime facts or authorization.
"""

SITE = None
