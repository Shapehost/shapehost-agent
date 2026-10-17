# Qualification status: 0.2.0 pilot

The runtime (`shapehost_agent.py`) and installer (`install.sh`) are byte-identical
to the Shape.host platform build qualified on 17 October 2026. Standalone
repository documentation, CI and build tooling were added for this publication.
The standalone packages have their own source revision and checksum manifest.

- 31 Python tests passed; standalone package reproducibility and CLI checks passed.
- A clean Ubuntu 24.04 ARM64 container installed all dependencies automatically.
- 15 actual smoke measurements passed, including CPU, memory, five disk workloads,
  gzip, network upload/download/latency and both PostgreSQL datasets.
- Workloads ran as an unprivileged user, detected the 2 vCPU/2 GiB resource limits,
  exited 0 and removed their temporary work directory.
- The actual report was submitted twice through the packaged CLI. It remained one
  private pending-review submission; public access returned 404.
- Smoke and synthetic results are not eligible for provider comparisons.

These are compatibility results, not hosting-provider performance results.
Full pilot and 24-hour runs, plus metadata on other launch providers, remain
to be qualified. The network profile caps traffic at 100 Mbit/s, so it does not measure
maximum multi-gigabit uplink capacity. Release signing and retained package
mirrors remain future work. Do not use this pilot on busy production hosts.

Raw reports, private administration, credentials and platform databases are not
part of this public repository.

## Real DigitalOcean VPS — 17 October 2026

The published v0.2.0 executable was installed on a disposable Ubuntu 24.04 x86-64
DigitalOcean Basic Droplet (2 vCPU / 4 GiB plan). Without provider or region
overrides, the agent detected **DigitalOcean / fra1**, matching the provider API.
Plan and zone stayed explicitly unknown because the selected metadata endpoint
does not expose them. Observed memory was approximately 3.82 GiB.

All **15 real smoke measurements passed** in 42.249 seconds: single/all-vCPU CPU,
gzip, memory read/write, five disk workloads, network upload/download/latency,
and both PostgreSQL datasets. An operator-owned Amsterdam Droplet served as the
controlled network endpoint. The private database cluster and work directory
were cleaned up; the runner exited 0.

The agent automatically submitted through an encrypted SSH tunnel to the local
platform. The result remained pending manual review and unavailable publicly.
Its admin presentation was checked against the original evidence. No approval
or publication was performed. Both Droplets were deleted and API lookups
confirmed 404; the temporary SSH key and scoped submission credential were
deleted or revoked. No API tokens, raw reports or private admin URLs are included
in this public repository.

This was the short smoke profile requested for compatibility testing. It does
not qualify the full pilot, 24-hour consistency, other providers or provider
rankings. No agent runtime changes were needed.
