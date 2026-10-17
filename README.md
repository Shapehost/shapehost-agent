# Shape.host benchmark agent 0.2.0

This directory is a standalone MIT licensed executable and source release. The upstream repository is
[Shapehost/shapehost-agent](https://github.com/Shapehost/shapehost-agent).
Version 0.2.0 is a pilot prerelease; live-provider qualification remains open.
The Python agent detects supported metadata fields, runs bounded sequential
workloads, preserves local evidence and automatically submits to Shape.host when
an invited submission credential is configured. The server always requires human
review before publication.

## Install and run

Target: a disposable **Ubuntu 24.04 x86-64 or ARM64 VM with 2–16 GiB effective RAM**.
Use the same reviewed package lock across comparison deployments. Do not run on a
busy production database or application host. This runner deliberately consumes
CPU, RAM, disk IO and network capacity.

Download the source archive and checksum from
[GitHub Releases](https://github.com/Shapehost/shapehost-agent/releases), verify
the archive checksum, and extract it. From the extracted `agent` directory:

```sh
sha256sum -c SHA256SUMS
./install.sh
shapehost-agent --version
shapehost-agent run --dry-run
shapehost-agent run --profile pilot --output ./report-01 --local-only
```

The installer uses `sudo`, Ubuntu's signed package repositories, resolves exact
package versions, preserves already installed versions and records the resulting
lock at `/opt/shapehost-agent/packages.lock`. It installs Python 3, sysbench, fio,
iperf3, PostgreSQL 16/pgbench, gzip, ping and util-linux automatically. It suppresses
new automatic PostgreSQL service-cluster creation during package installation and
restores the previous `createcluster.conf`. It does not run workloads as root.
The only installed command wrapper is `/usr/local/bin/shapehost-agent`.

Before execution the command prints privileges, duration, disk and transfer limits.
The pilot usually takes 10–60 minutes including dataset initialization; the precise
time depends on the VM; each database initialization times out after at most 15
minutes. It requires **3 × observed RAM + 4 GiB of free disk space**.
At most two controlled iperf3 endpoints can be used, with **64 MiB per direction
per endpoint**, one stream and a 100 Mbit/s rate cap (256 MiB payload total for two
endpoints, excluding protocol overhead and ping). No third-party benchmark
server is selected automatically. No configured endpoint produces a visible
skipped workload, never a zero score.

```sh
# Set these through your shell's secret manager or a private environment file.
# Never put the token in a public issue, process argv, or a source file.
export SHAPEHOST_ENDPOINT='https://your-shapehost.example'
read -r -s SHAPEHOST_SUBMISSION_TOKEN
export SHAPEHOST_SUBMISSION_TOKEN
shapehost-agent run --profile pilot --output ./report-01 \
  --network-endpoint endpoint-a.example --network-endpoint endpoint-b.example
```

A configured endpoint and credential trigger upload automatically after the local
report has been saved. Credential eligibility is **invited intake**, issued by an
administrator. The server assigns external versus controlled provenance from the
credential; the client cannot select that trust level.

```sh
shapehost-agent inspect ./report-01/report.json
shapehost-agent submit ./report-01/report.json
# Resume complete test boundaries after interruption; all failed attempts remain.
shapehost-agent run --profile pilot --output ./report-01 --resume
```

Use the same profile and endpoints when resuming. Completed or submitted reports
cannot resume; retry their upload with `submit`.
Upload uses at most three bounded attempts and saves `receipt.json` separately;
it never modifies the submitted report. An unchanged retry is idempotent. A changed
payload with the same run ID is a conflict. A failed upload can be retried without
rerunning any workload. For a local development server only, explicitly add
`--allow-local-intake`; HTTP is otherwise rejected.

## Implemented workload profiles

`pilot-1` is an implemented **pilot profile**, distinct from the broader proposed
methodology in the platform plan. It is not advertised as a finished campaign.

| Area | Workloads | Parameters and limitations |
| --- | --- | --- |
| CPU | sysbench prime events, single/all visible vCPU; gzip compression | 30 seconds, 3 repetitions; max prime 20,000; deterministic compression corpus, level 6 |
| Memory | sysbench read/write bandwidth | 256 MiB block beyond common CPU caches, one thread, 30 seconds, 3 repetitions; copy workload remains deferred |
| Disk | fio sequential read/write, random read/write, 70/30 mixed | Dedicated 2 GiB file, direct IO, libaio, queue 32, 1 job; 1 MiB sequential / 4 KiB random blocks; 30 seconds, 3 repetitions; p50/p95/p99 completion latency |
| Network | controlled iperf3 upload/download, ICMP latency | Two operator-controlled endpoints; byte/rate caps above; reports endpoint-relative throughput; TCP loss/jitter not claimed |
| PostgreSQL | pgbench memory/storage datasets | Major 16, scale estimates 20% / 200% of observed RAM, 256 MiB shared buffers, 4 clients, 2 threads, 5 second warmup; durability enabled, local private Unix socket; average latency only |
| Consistency | `consistency-24h` only | Repeat all-vCPU CPU, memory read and random disk read every hour for 24 hours; retain raw cumulative CPU counters, per-sample timings and min/median/max; missed time slots remain explicitly skipped |

`--profile smoke` uses 2 second measurements, one repetition, a 128 MiB fio
file, PostgreSQL scales 1 and 2, a 1 second warmup, and an 8 MiB per-direction
network cap. These are real workloads for compatibility testing only. Smoke
results are not eligible for provider rankings. `--dry-run` prints configuration only and works on macOS without running
metadata detection, tools or workloads. The `fixture` schema value is reserved
for clearly marked synthetic test data; no real run can select it.

The PostgreSQL datasets are isolated, per-run agent resources. No existing
cluster/database or raw block device is used. Normal completion and handled
interruptions stop the private cluster and remove only the agent work directory.
If the process is killed abruptly and a PostgreSQL PID record remains, resume
refuses automatic deletion. Inspect it and stop that private cluster with
`/usr/lib/postgresql/16/bin/pg_ctl -D <report>/work/pgdata -m fast stop` as the same
user, then resume. Never point cleanup commands at a pre-existing host cluster.

## Detection and privacy

Every provider, region, zone and SKU observation includes value, source, status
and selected evidence. Unknown values remain null. `--provider`, `--region` and
`--plan` are visibly supplied overrides and preserve the original observations.
Hetzner region is a **network zone**, not a city. No pricing is inferred from
hardware. The reviewer must separately verify plan/catalog/pricing context.

The agent probes a fixed set of link-local metadata URLs with no redirects,
proxies, credentials or arbitrary URLs, a 750 ms per-call timeout and 16 KiB
response cap. It never requests metadata user-data, SSH keys, identities, account
IDs, network interfaces or hostname. See [verified detector sources](docs/DETECTORS.md).
Akamai's ephemeral metadata token is held only in memory, never saved.
OVH VPS currently returns explicit unknowns with a DMI hint where present;
OpenStack metadata alone would not prove OVH identity. All five adapters have
fixture tests. DigitalOcean / fra1 metadata detection and all smoke workloads have also been
verified on a real Ubuntu 24.04 x86-64 VPS. Full pilot workloads, the 24-hour
schedule and the remaining provider detectors require separate qualification;
see [qualification evidence and limits](docs/QUALIFICATION.md).

Hardware collection is allowlisted: CPU model/architecture/count, RAM, OS/kernel,
virtualization, block names/sizes/filesystems/mounts and the test-file location.
It does not collect environment variables, machine IDs, serial numbers, MACs,
cloud API keys or hostnames. A random persisted deployment ID under
`~/.local/state/shapehost-agent/deployment-id` prevents retries being counted as
new deployments; it is not a hardware fingerprint or attestation. Reinstallation
can change this identity, so independent sampling still requires human review.

Reports and artifacts are private and stored with restrictive permissions. Network
destination addresses and test paths may be visible to authenticated reviewers.
Public snapshots omit raw artifacts, test paths, mount details, detector evidence,
credential identities and private review notes. Review the local report before
submission where your environment has additional confidentiality requirements.

## Testing and release

```sh
python3 -m unittest discover -s tests -v
python3 shapehost_agent.py run --dry-run
```

The tests cover fixed metadata adapters and unknown/conflicting values, secret
field exclusion, parser summaries/failures, network target bounds, submission
retry without rerunning, HTTPS enforcement and failed-workload evidence.
No unit test runs sysbench, fio, iperf3 or a database benchmark.

The schema lives at [report.schema.json](report.schema.json). The receiving Shape.host platform also validates evidence and review state;
its admin and ingestion server are separate from this standalone agent.
To build from a Git checkout of this repository, run
`python3 scripts/build-agent.py`, then `python3 scripts/verify-agent-build.py`. This builds a deterministic executable
Python zip application (`.pyz`) and source archive with SHA-256 manifests in
`dist`. The installer installs the packaged executable. It still
needs Python and the external benchmark tools; they are installed automatically,
not embedded in the executable. The build identifies both the Git revision and
the actual source-content digest. Publish these artifacts through the selected
open-source repository. The public source and prereleases are hosted in this GitHub repository. Pin the agent source commit
and exact package lock for each accepted cohort. Signed Ubuntu apt artifacts are
verified by apt, but current apt repositories do not guarantee indefinite access
to old versions. A retained/signed package mirror or qualified VM image is needed
for long-term reproducible cohorts; that release gate remains open.

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md),
[CHANGELOG.md](CHANGELOG.md), [LICENSE](LICENSE) and
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md). Source licensing does not clear
redistribution of bundled third-party binaries; no such binaries are bundled.

## Container and private network qualification

CPU affinity, cgroup v1/v2 CPU quotas and memory limits constrain the effective
resources used by workloads. Host observations and resource limits are retained
in a private artifact. Fractional quotas and container execution are explicit
limitations; containers do not establish VPS performance.

For a network endpoint you own on a private test network, add
`--allow-private-network-targets`. This permits RFC1918 IPv4 and ULA IPv6 only;
loopback, link-local metadata addresses, multicast and unspecified addresses
remain rejected. The exception is recorded in the report configuration.

The repository includes `qa/Dockerfile` for a clean Ubuntu install; build it
with `dist` as the context after packaging. The local qualification recorded
15 passing real ARM64 smoke measurements and a private idempotent upload.
See [qualification](docs/QUALIFICATION.md). Smoke compatibility is separate
from live-provider qualification.
