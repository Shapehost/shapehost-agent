# Security reporting

Report vulnerabilities through
[GitHub private vulnerability reporting](https://github.com/Shapehost/shapehost-agent/security/advisories/new). Do not include submission tokens, provider credentials or private
host reports in public issues. Rotate a disclosed submission credential in admin.

The agent is a benchmark runner, not a remote shell. It accepts no remote jobs or
commands. Only fixed argv workloads execute; subprocess shell execution is not
used. Credentials are sent in HTTPS headers and omitted from logs and reports.
Checksums establish integrity, not authenticity or hardware attestation.

A production release requires an identified private reporting channel, release
signing/verification policy and qualified package snapshots. The current pilot is a prerelease; release signing and package snapshot
qualification remain open.
