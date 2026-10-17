# Single-node platform operations

The pilot stores submissions, credentials, scrypt password hashes, sessions,
immutable revisions/public snapshots and audit history in SQLite. Default path:
`data/shapehost.sqlite`; override with `SHAPEHOST_DB_PATH`. The DB directory is
private and the DB file mode is 0600. Back up SQLite consistently using its backup
API or an offline snapshot including WAL state; copying only a live `.sqlite`
file can omit committed WAL data. Encrypt host storage and backups and restrict
operator access. Do not host data in a public static directory.

```sh
# Interactive private stdin; password never printed or saved in source.
read -r -s SHAPEHOST_ADMIN_PASSWORD
export SHAPEHOST_ADMIN_PASSWORD
npx tsx scripts/setup-admin.ts
unset SHAPEHOST_ADMIN_PASSWORD
# Optional PRIVATE synthetic queue item, never published automatically:
npx tsx scripts/seed-demo.ts
```

No default administrator/password exists. Re-running setup rotates the named
admin's password and revokes its sessions. `SHAPEHOST_ADMIN_NAME` selects the name
(default `admin`). The auth service supports administrator/reviewer/reader roles;
this setup CLI creates an administrator. Sessions are opaque random 256-bit tokens
stored only as SHA-256 digests in SQLite, with HTTPOnly, SameSite=Strict cookies,
8-hour expiry and Secure cookies in production. APIs authorize every private read
and mutation. JSON mutations require a matching Origin; set `SHAPEHOST_ORIGIN` to
the canonical external HTTPS origin in production. Login is bounded to 30 attempts
per 15 minutes globally (single-node limiter). Configure proxy request/rate limits
and admin access restrictions for a public production deployment.

Issue submission credentials from the authenticated admin credential screen.
Only the issuing response exposes the raw value; the DB retains a digest. Keep
controlled-cohort credentials distinct from invited external operator credentials.
Revoke compromised tokens immediately. Submission tokens cannot access admin or
publish a report.

Retention is intentionally conservative: this pilot has no automatic deletion.
Original reports/artifacts and audit evidence remain private until an operator
establishes an approved retention policy. Storage grows with submitted payloads;
monitor disk usage and back up before archival. Request payloads are capped at
8 MiB and credential rate limits apply. At this stage queue enumeration is capped
at 500 reports and is not a substitute for durable pagination at larger scale.

Production limitations: single-node SQLite and local files are not suitable for
multiple independent serverless replicas, ephemeral filesystem hosting or large
volumes of inline artifacts. Move to transactional PostgreSQL, private object
storage, streamed artifact manifests, durable validation workers and a shared
rate limiter before scaling. This implementation has no MFA, SSO, email reset,
self-service registration or anonymous submission. No deployment or TLS proxy
configuration was performed by the local build.

## TLS proxy and isolated local production-mode tests

In normal production, bind Next to the private/loopback interface behind a TLS
proxy. The proxy must overwrite (not append untrusted client values to)
`X-Forwarded-Proto` with `https` for HTTPS requests and forward the canonical Host.
Next's request URL derives its protocol from this forwarded header; intake
requires the resulting HTTPS URL. Block direct access to the origin port and
redirect or reject plaintext at the proxy. Merely setting `SHAPEHOST_ORIGIN` does
not assert transport security. Do not trust arbitrary incoming forwarded headers.
No live reverse proxy was configured or tested in this local build.

For an isolated `next start` demonstration/test server **bound to loopback only**,
set both `SHAPEHOST_ALLOW_LOCAL_INTAKE=true` and, for example,
`SHAPEHOST_ORIGIN=http://127.0.0.1:3100`. The explicit exception is off by default
and accepts only the exact matching loopback origin/host, port and `/api/intake`
path, rejecting external/forwarded public hosts. Next normalizes loopback IPs
to `localhost` in its internal request URL; this is accepted only when the URL
port matches and the incoming Host and forwarded Host still exactly match the
configured origin. It does not change production
cookie security, other API authorization or CSRF rules. Never enable this flag
on a publicly bound origin. The agent must independently opt in with
`--allow-local-intake` to use HTTP localhost submission.
