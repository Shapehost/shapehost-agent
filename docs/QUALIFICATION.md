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
Live provider metadata, x86-64 runtime, full pilot and 24-hour runs remain to be
qualified. The network profile caps traffic at 100 Mbit/s, so it does not measure
maximum multi-gigabit uplink capacity. Release signing and retained package
mirrors remain future work. Do not use this pilot on busy production hosts.

Raw reports, private administration, credentials and platform databases are not
part of this public repository.
