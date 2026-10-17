# Submission contract v1

POST `/api/intake` with `Content-Type: application/json` and a scoped Bearer token
issued in admin. HTTPS is required in production. A complete report includes all
inline artifacts and their SHA-256 hashes; no incomplete/multipart upload can be
queued. Maximum request: 8 MiB; stream duration: 30 seconds; maximum 300 artifacts,
1.5 million characters each, and 1000 measurements. Intake limits each credential
to 30 requests/hour. Coordinate limits with the production TLS proxy before use.

See `report.schema.json` and the platform runtime validator. The schema rejects
unknown top-level trust/source fields. Client source labels cannot confer trust.
Malformed reports return 422; oversized input 413; invalid credentials 401;
changed content or owner for an existing run ID 409. Exact retries return the
same ID with `idempotent:true`, otherwise receipt status 201 and `idempotent:false`.
Validation errors, including checksum mismatches, persist privately as
`validation_failed`; they cannot be approved. Complete, valid reports enter
`pending_review`. No intake operation approves or publishes.

The server recalculates artifact hashes and re-parses CPU sysbench event summaries
where an `events/s` metric is submitted. Other parser summaries remain
client-reported with raw evidence visible to the reviewer; they are not attested.
This is an explicit validation limitation, not a claim of independent verification
of every metric or of hardware execution.

Admin review uses an expected revision. Approval with warnings requires a written
warning disposition. Publication uses only that exact approved revision.
Corrections append attributed annotations in a new revision and invalidate its
approval; original measurements stay immutable. Existing published snapshots
remain immutable until a new revision is approved/published or an editor withdraws
them with a reason. Private artifacts and decision notes never enter public APIs.

One run is one deployment observation, not three independent samples. The server
retains run/deployment IDs and provenance; aggregation must deduplicate deployments
and compare profile/version/hardware configurations before making any ranking.
