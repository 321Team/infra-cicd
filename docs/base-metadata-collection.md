# Base image metadata — one approved collection window

## Authority and scope

On 2026-09-17, user 이권욱 instructed `착수` after the concrete recommendation:
Runner `10.250.10.20`, owner 이권욱, `auth.docker.io:443` and
`registry-1.docker.io:443`, `library/python:3.12-slim` metadata only,
maximum 60 minutes from actual first execution, stop on TLS failure or redirect.
This adopts the proposed values for this task. It is not 이하영's technical
review, a permanent egress rule, or approval of application Delivery.
Decision reference: [infra-cicd #11](https://github.com/321Team/infra-cicd/issues/11),
local approval label `P1-CICD-OID03-20260917-01` (not a new canonical Decision ID).

The source authority remains [v3.1.18 §15.1.2 OID-03 / §15.2](https://github.com/321Team/fds-project-baseline/blob/docs-v3.1.18/baseline/FDS_Project_Implementation_Baseline_v3.1.18.md).
The [v19 Trivy input](https://github.com/321Team/fds-project-baseline/blob/48b8d836e74a723ea145360f63456de0d4183f96/audit/v3.1.19/intake/p1-cicd-trivy-update-policy-20260916.md)
does not itself grant registry access. The new user adoption supplies the narrow
collection scope; final v19 publication remains separate.

## Collector behavior

`tools/delivery/collect_base_metadata.py` is a standalone Python standard-library
script, no installed dependencies. Run manually as
`server01_cicd-runner` / UID 1000 on `cicd-runner01`, with Python 3.12 already
observed on that server. Do not run with sudo/root.

- Uses the account database's exact home `/home/server01_cicd-runner`;
  verifies directory type, owner and mode 0700. Accepts no caller-supplied paths.
- Uses the existing resolver and default route. Configured HTTP/HTTPS/ALL proxy
  or custom SSL CA environment overrides cause a stop, rather than bypassing a
  configured route. Does not use `.56:3128` (Alertmanager/Discord only).
- Direct HTTPS through `http.client`, hostname and CA validation, TLS >=1.2.
  Only fixed Docker Hub hosts and fixed/validated repository paths. Anonymous
  short-lived `repository:library/python:pull` token is held only in memory;
  no Docker login, refresh token, client credentials or authentication file.
- Retrieves the tag's root index or manifest, exactly one linux/amd64 manifest
  when indexed, and its referenced config. Validates response manifest digests
  and child/config size/hash. No layer download, catalog enumeration, PyPI,
  Skopeo installation, image import/build/scan/push or Kubernetes operation.
- Bounded responses: auth 64 KiB, metadata 4 MiB each; no compressed HTTP body;
  20-second socket timeout, checks before requests/reads plus whole-operation
  alarm tied to the remaining window. No automatic retries or redirects.
- Any redirect, including to the same host, stops the collection. Evidence may
  include only a sanitized redirect hostname; no signed URL path/query or token.
  These two hosts are an initial scope, not a promise that Docker Hub can serve
  every config without redirect. A newly required host needs a separately
  defined scope or collection through an approved offline-import route.
- The first run creates `FDS_CICD_OID03_20260917_01.json` exclusively in the
  account home with owner-only access. Subsequent runs use the same start/expiry;
  they do not extend it. Expired or altered scope files stop before network use.
  Do not delete/edit the file to renew authorization. Another window requires a
  new recorded execution decision. An incomplete first write also fails closed.
- Each invocation ends its own network work on success/failure. Manual retries
  within the original window are possible; no background job or renewal exists.
- Writes new `FDS_CICD_Base_Metadata.<UTC>.<random>` directories only. Existing
  files, home permissions, services, firewall/DNS/NAT/CA settings are untouched.
  No chmod/chown, deletion, extraction, cleanup, subprocess or arbitrary output
  directory. The private files are accessed through directory descriptors with
  exclusive creation and no final symlink following. This is not isolation from
  another hostile process running as the same UID.

These are client-side destination/time restrictions. They do not install or
prove a network firewall allowlist or its expiry. `NETWORK_ENFORCEMENT` remains
`NOT_VERIFIED`. Existing network denies are not circumvented. If additional
network policy is required, stop and use an approved destination-limited,
expiring change or the pre-import alternative. No broad permanent rule is
authorized by this script or the collection result.

## Execute and return evidence

Transfer the published `collect_base_metadata.py` file to the account home using
the existing file transfer method, verify its published SHA-256, then run:

```bash
/usr/bin/python3 -I -B /home/server01_cicd-runner/collect_base_metadata.py --collect
```

`-I` isolates Python import environment; `-B` avoids bytecode files. The script
still checks explicit proxy/CA environment routing overrides before networking.
Output includes the actual start/expiry epochs, collection status, request
receipts and a generated evidence directory. On failure return the evidence as
well; do not change security settings or retry with insecure TLS options.

From that printed directory, return:

- `base-metadata.tar.gz`
- `base-metadata.tar.gz.sha256`

The archive contains only root `index.json` when available, `manifest.json`,
`config.json` when verified/available, `result.json`, and `SHA256SUMS`.
Partial collections are labeled FAIL. The result records the collector file's
SHA-256. Token bodies/headers, redirect query strings and raw exception messages
are never saved. Public upstream config bytes are preserved for digest
verification; they are not printed to the terminal. File hashes do not provide
remote attestation or independent authentication of user-submitted evidence.

After return, compare byte hashes and use the [PR #16 offline verifier](https://github.com/321Team/infra-cicd/pull/16)
for the full manifest/config profile. `COLLECTION=PASS` is not that verifier's
PASS, a vulnerability PASS, approval of a final app Source, or Delivery PASS.
`BASE_MANIFEST_CHECK=NOT_EXECUTED`, `IMAGE_SCAN=NOT_EXECUTED`,
`DELIVERY_GATE=NOT_VERIFIED` remain explicit.

This manual metadata collection is the user-authorized next activity. It does
not activate a workflow or merge this PR. Independent code review and merge are
tracked separately under repository governance, using the existing temporary
reviewer substitution to 이재환. Actual Test/Build/Scan keeps its approved
Source/Workflow and supply prerequisites.

## Verification and references

Negative tests cover redirect/token confidentiality, TLS/status failures,
unapproved endpoints, root/config digest mismatch, duplicate platform and
external/path-traversal descriptors, response bounds, malformed JSON, expired
or backwards windows, no automatic renewal, symlink/existing-file protection,
private evidence output and wrong-target/proxy refusal.
All HTTP responses in these tests are synthetic; no registry is queried.

- [Docker Registry token authentication](https://docs.docker.com/reference/api/registry/auth/)
- [Docker Hub-supported Registry API](https://docs.docker.com/reference/api/registry/latest/)
- [OCI image index v1.1.0](https://github.com/opencontainers/image-spec/blob/v1.1.0/image-index.md)
