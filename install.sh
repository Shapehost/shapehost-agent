#!/bin/sh
# Install this checked-out release on Ubuntu 24.04 x86-64 or ARM64; no pipe-to-shell download.
set -eu
if [ "$(uname -s)" != Linux ]; then
  echo 'Supported installation target: Ubuntu 24.04 x86-64 or ARM64.' >&2; exit 1
fi
case "$(uname -m)" in x86_64|aarch64) ;; *) echo "Unsupported CPU architecture" >&2; exit 1 ;; esac
. /etc/os-release
if [ "${ID:-}" != ubuntu ] || [ "${VERSION_ID:-}" != 24.04 ]; then
  echo 'Supported installation target: Ubuntu 24.04 x86-64 or ARM64.' >&2; exit 1
fi
if [ "$(id -u)" -ne 0 ]; then
  exec sudo -- sh "$0" "$@"
fi
agent_source=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -f "$agent_source/SHA256SUMS" ]; then
  (cd "$agent_source" && sha256sum -c SHA256SUMS)
fi
printf '%s\n' 'Preparing signed Ubuntu packages: sysbench, fio, iperf3, PostgreSQL 16, gzip, Python 3.'
printf '%s\n' 'Installation needs root. Workloads run as your unprivileged user. No VM is provisioned.'
export DEBIAN_FRONTEND=noninteractive
apt-get update
# Resolve exact package versions from the signed configured Ubuntu repositories and
# save the lock. Existing versions are preserved. Never silently upgrade packages.
install -d -m 755 /opt/shapehost-agent
lock_file=$(mktemp)
restore_file=''
restore_target=/etc/postgresql-common/createcluster.conf
restore_cluster_config() {
  if [ -n "$restore_file" ]; then
    cp -p "$restore_file" "$restore_target"
    rm -f "$restore_file"
  fi
  rm -f "$lock_file"
}
trap restore_cluster_config EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
# Install common first, then suppress automatic creation of a new service cluster
# during postgres package setup. Preserve the exact previous global configuration.
if ! dpkg-query -W -f='${Status}' postgresql-common 2>/dev/null | grep -q 'install ok installed'; then
  apt-get install -y --no-install-recommends postgresql-common
fi
if ! dpkg-query -W -f='${Status}' postgresql-16 2>/dev/null | grep -q 'install ok installed'; then
  restore_file=$(mktemp)
  cp -p "$restore_target" "$restore_file"
  printf '\ncreate_main_cluster = false\n' >> "$restore_target"
fi
for package in python3 sysbench fio iperf3 postgresql-16 postgresql-client-16 gzip iputils-ping util-linux; do
  installed=$(dpkg-query -W -f='${Version}' "$package" 2>/dev/null || true)
  if [ -n "$installed" ] && dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed'; then
    version=$installed
  else
    version=$(apt-cache policy "$package" | awk '/Candidate:/ {print $2; exit}')
    if [ -z "$version" ] || [ "$version" = '(none)' ]; then
      echo "No installable candidate: $package" >&2; exit 1
    fi
    apt-get install -y --no-install-recommends --no-upgrade "$package=$version"
  fi
  printf '%s=%s\n' "$package" "$version" >> "$lock_file"
done
# The installer preserves actual installed versions and records them. Cohorts
# must share this reviewed package lock; availability in apt is not permanent.
cp "$lock_file" /opt/shapehost-agent/packages.lock
install -m 755 "$agent_source/shapehost_agent.py" /opt/shapehost-agent/shapehost_agent.py
if [ -f "$agent_source/SOURCE_COMMIT" ]; then
  install -m 644 "$agent_source/SOURCE_COMMIT" /opt/shapehost-agent/SOURCE_COMMIT
fi
if [ -f "$agent_source/bin/shapehost-agent.pyz" ]; then
  install -m 755 "$agent_source/bin/shapehost-agent.pyz" /opt/shapehost-agent/shapehost-agent.pyz
  agent_program=/opt/shapehost-agent/shapehost-agent.pyz
else
  agent_program=/opt/shapehost-agent/shapehost_agent.py
fi
printf '#!/bin/sh\nexec python3 "%s" "$@"\n' "$agent_program" > /usr/local/bin/shapehost-agent
chmod 755 /usr/local/bin/shapehost-agent
printf '%s\n' 'Installed. Run as an unprivileged user: shapehost-agent run --profile pilot --output ./shapehost-report'
printf '%s\n' 'Package lock: /opt/shapehost-agent/packages.lock. Two controlled iperf3 endpoints can be supplied with --network-endpoint.'
