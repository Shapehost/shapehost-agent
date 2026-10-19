# Changelog

## 0.2.1 — 2026-10-19

- Request `Accept: application/json` for the Linode `/v1/instance` metadata
  endpoint so a plain-text response cannot break JSON provider/plan detection.
  The token request is unchanged and its token remains in memory only.
- Regression coverage verifies the JSON header, selected metadata fields and
  exclusion of the ephemeral token from the detector result.
- The platform accepts 0.2.1 reports through private intake while preserving
  support for 0.1.0 and 0.2.0. Publication still requires administrator review.
- Workload settings and the pilot methodology are unchanged. Earlier campaign
  reports keep their recorded version and source; this release does not relabel
  them or claim automatic detection where metadata was unavailable.

## 0.2.0 — 2026-10-17

- Reproducible executable Python zipapp and source archive, with checksums and
  actual source-content provenance. Installer selects the packaged executable.
- Ubuntu 24.04 ARM64 and x86-64 targets, effective cgroup and CPU-affinity limits.
- Bounded compatibility smoke profile with small disk and database datasets.
- Explicit operator-owned private-network testing option, recorded in reports.
- Bounded process output, process-group cleanup, setup-failure reporting, and
  persistent hourly observations for interrupted consistency runs.
- Installer restores PostgreSQL configuration on handled interruption.
- Clean-install container and actual private-intake qualification harnesses.

Live provider metadata, full pilot and 24-hour qualification, public source
hosting and release signing remain separate release gates.

## 0.1.0 candidate — 2026-10-17

- Python standard-library CLI, Ubuntu dependency installer and exact resolved
  package lock.
- Bounded CPU, memory, dedicated-file disk, controlled network and isolated
  PostgreSQL workloads, with short pilot/smoke and 24-hour scheduled profiles.
- Allowlisted metadata adapters, truthful unknowns and visible overrides.
- Local checksummed raw evidence and retryable authenticated submission.
- Private intake/manual approval/public snapshot platform contract.

Acceptance still open: clean Ubuntu installation, actual workload/tool
compatibility, live provider metadata for every launch provider, controlled
network endpoint capacity, 24-hour campaign, release signing and public hosting.
This candidate must not be described as an observed performance dataset.
