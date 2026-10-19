#!/usr/bin/env python3
"""Shape.host bounded benchmark agent. Python 3.12 standard library only."""
import argparse
import datetime as dt
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

VERSION = '0.2.1'
SCHEMA = 'shapehost-report/v1'
MAX_ARTIFACT = 1_000_000
META = 'http://169.254.169.254'
UTC = dt.timezone.utc

def timestamp():
    return dt.datetime.now(UTC).isoformat(timespec='milliseconds').replace('+00:00', 'Z')

def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()

def atomic_json(path, data):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as f:
        json.dump(data, f, indent=2, allow_nan=False)
        f.write('\n')
    os.chmod(temporary, 0o600)
    temporary.replace(path)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Redirects are not allowed')

HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

def fetch_metadata(path, method='GET', headers=None):
    """Only hardcoded link-local metadata paths; no proxy, redirects or whole user-data."""
    allowed = {'/hetzner/v1/metadata/region', '/hetzner/v1/metadata/availability-zone',
               '/metadata/v1/region', '/v1/region/regioncode', '/v1/token', '/v1/instance'}
    if path not in allowed:
        raise ValueError('Metadata path is not allowlisted')
    try:
        request = urllib.request.Request(META + path, method=method, headers=headers or {})
        with HTTP.open(request, timeout=0.75) as response:
            content = response.read(16_385)
            if len(content) > 16_384:
                return None
            return content.decode('utf8').strip()
    except (OSError, ValueError, urllib.error.URLError):
        return None

def observation(value=None, source='No supported signal', evidence='', status=None):
    return {'value': value, 'source': source, 'status': status or ('detected' if value is not None else 'unknown'), 'evidence': str(evidence)[:2048]}

def safe_identifier(value):
    return value if isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9_.-]{1,100}', value) else None

def detect_provider(fetch=fetch_metadata, vendor=''):
    """Five provider adapters; absence and ambiguous OVH DMI remain unknown."""
    matches = []
    hetzner = safe_identifier(fetch('/hetzner/v1/metadata/region'))
    if hetzner:
        zone = safe_identifier(fetch('/hetzner/v1/metadata/availability-zone'))
        matches.append(('Hetzner', hetzner, zone, None, 'Hetzner metadata allowlisted region/availability-zone'))
    digitalocean = safe_identifier(fetch('/metadata/v1/region'))
    if digitalocean:
        matches.append(('DigitalOcean', digitalocean, None, None, 'DigitalOcean metadata /metadata/v1/region'))
    vultr = safe_identifier(fetch('/v1/region/regioncode'))
    if vultr:
        matches.append(('Vultr', vultr, None, None, 'Vultr metadata /v1/region/regioncode'))
    token = fetch('/v1/token', method='PUT', headers={'Metadata-Token-Expiry-Seconds': '60'})
    if token and len(token) < 4096:
        raw = fetch('/v1/instance', headers={'Metadata-Token': token, 'Accept': 'application/json'})
        try:
            data = json.loads(raw or '{}')
            # Discard label, SSH keys, user data, IDs and tags. Retain only these fields.
            region = safe_identifier(data.get('region'))
            plan = safe_identifier(data.get('type'))
            if region:
                matches.append(('Akamai / Linode', region, None, plan, 'Akamai token-scoped metadata /v1/instance; selected region/type only'))
        except (ValueError, TypeError, AttributeError):
            pass
    if len(matches) == 1:
        provider, region, zone, plan, source = matches[0]
        return {key: observation(value, source, f'Allowlisted field: {key}={value}' if value else 'Field not exposed by this metadata endpoint')
                for key, value in [('provider', provider), ('region', region), ('zone', zone), ('plan', plan)]}
    if len(matches) > 1:
        return {key: observation(None, 'Conflicting metadata adapters', ', '.join(x[0] for x in matches), 'conflict') for key in ['provider','region','zone','plan']}
    # OVH VPS has no verified common authoritative in-guest metadata contract. DMI
    # is only a hint; OpenStack endpoints are shared by many providers and cannot prove OVH.
    hint = 'OVHcloud DMI hint; provider, SKU and region need reviewer confirmation' if 'ovh' in vendor.lower() else 'No authoritative provider metadata available'
    return {key: observation(None, 'Local DMI hint' if 'ovh' in vendor.lower() else 'Metadata probes', hint) for key in ['provider','region','zone','plan']}

def read_text(path, maximum=100_000):
    try:
        return Path(path).read_text()[:maximum]
    except OSError:
        return ''

def capture(command, timeout=10, cwd=None):
    """Fixed argv, minimal environment, bounded output/deadline and process group."""
    if timeout <= 0:
        raise ValueError('Subprocess timeout must be positive')
    env = {'PATH': '/usr/local/bin:/usr/bin:/bin:/usr/lib/postgresql/16/bin', 'LANG': 'C', 'LC_ALL': 'C', 'HOME': str(Path.home())}
    with tempfile.TemporaryFile() as output:
        try:
            proc = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT,
                                    cwd=cwd, env=env, start_new_session=True)
        except OSError as error:
            raise WorkloadError(f'Unable to start {Path(command[0]).name}: {error.strerror}') from None
        deadline = time.monotonic() + timeout
        try:
            while proc.poll() is None:
                if os.fstat(output.fileno()).st_size > MAX_ARTIFACT:
                    raise WorkloadError('Output limit exceeded; process group terminated')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                time.sleep(min(0.05, remaining))
            if os.fstat(output.fileno()).st_size > MAX_ARTIFACT:
                raise WorkloadError('Output limit exceeded; measurement discarded')
        except BaseException as failure:
            try:
                os.killpg(proc.pid,signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            # A parent may exit while a descendant ignores SIGTERM. Kill the
            # entire remaining process group, not only the immediate process.
            try:
                os.killpg(proc.pid,signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=5)
            output.seek(0)
            partial = output.read(MAX_ARTIFACT).decode('utf8', errors='replace')
            if isinstance(failure, subprocess.TimeoutExpired):
                raise WorkloadError(f'Command timed out after {timeout} seconds; partial output retained', partial) from None
            failure.output = partial
            raise
        output.seek(0)
        result = output.read(MAX_ARTIFACT).decode('utf8', errors='replace')
        if proc.returncode:
            raise WorkloadError(f'Command exited {proc.returncode}', result)
        return result

class WorkloadError(RuntimeError):
    def __init__(self, message, output=''):
        super().__init__(message)
        self.output = output

def parse_cpu_list(value):
    """Kernel CPU list syntax, bounded against malformed / oversized ranges."""
    cpus = set()
    for item in value.strip().split(','):
        if not item:
            continue
        match = re.fullmatch(r'(\d+)(?:-(\d+))?', item)
        if not match:
            raise ValueError('Invalid CPU list')
        first, last = int(match[1]), int(match[2] or match[1])
        if first > last or last > 65535:
            raise ValueError('CPU list is out of range')
        cpus.update(range(first, last + 1))
    return cpus


def effective_resources(read=read_text):
    """Keep host observations separate from affinity and hierarchical cgroup limits.

    Only numeric resource controls are retained, never cgroup paths/container IDs.
    vCPU count is the bounded worker count (ceil of a fractional CPU quota), not
    a claim that a quota is a physical CPU or a dedicated vCPU entitlement.
    """
    host_cpus = os.cpu_count() or 1
    try:
        affinity = set(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        affinity = set(range(host_cpus))
    memory = re.search(r'MemTotal:\s+(\d+) kB', read('/proc/meminfo'))
    if not memory:
        raise ValueError('Cannot determine host memory from /proc/meminfo')
    host_memory = int(memory[1]) * 1024
    cpu_sets = [affinity]
    quotas = []
    memories = [host_memory]
    constraints = []
    versions = set()
    memberships = []
    for line in read('/proc/self/cgroup').splitlines():
        parts = line.split(':', 2)
        if len(parts) == 3:
            memberships.append((set(parts[1].split(',')), parts[2]))
    for line in read('/proc/self/mountinfo').splitlines():
        before, sep, after = line.partition(' - ')
        fields, mount_fields = before.split(), after.split()
        if not sep or len(fields) < 5 or len(mount_fields) < 3:
            continue
        filesystem = mount_fields[0]
        if filesystem not in ['cgroup', 'cgroup2']:
            continue
        controllers = set(mount_fields[2].split(','))
        for member_controllers, member_path in memberships:
            if filesystem == 'cgroup2' and member_controllers != {''}:
                continue
            if filesystem == 'cgroup' and not member_controllers.intersection(controllers):
                continue
            mount_root, mount_path = fields[3], fields[4]
            # Mountinfo escapes whitespace; reject unusual mounts instead of
            # interpreting escapes or wandering outside the cgroup mount.
            if '\\' in mount_path or '\\' in mount_root or '..' in Path(member_path).parts:
                continue
            base = Path(mount_path)
            if member_path == '/' or mount_root == member_path:
                current = base
            elif mount_root == '/':
                current = base / member_path.lstrip('/')
            elif member_path.startswith(mount_root.rstrip('/') + '/'):
                current = base / member_path[len(mount_root):].lstrip('/')
            else:
                # A namespace can expose its own cgroup root as the mount root.
                current = base
            versions.add(2 if filesystem == 'cgroup2' else 1)
            while True:
                def control(name):
                    return read(str(current / name)).strip()
                if filesystem == 'cgroup2':
                    quota = control('cpu.max').split()
                    mem = control('memory.max')
                    cpuset = control('cpuset.cpus.effective')
                else:
                    quota = [control('cpu.cfs_quota_us'), control('cpu.cfs_period_us')] if 'cpu' in controllers else []
                    mem = control('memory.limit_in_bytes') if 'memory' in controllers else ''
                    cpuset = control('cpuset.cpus') if 'cpuset' in controllers else ''
                if len(quota) == 2:
                    try:
                        limit, period = int(quota[0]), int(quota[1])
                        if limit > 0 and period > 0:
                            cores = limit / period
                            quotas.append(cores)
                            constraints.append({'control':'cpu quota cores','value':cores})
                    except ValueError:
                        pass
                try:
                    limit = int(mem)
                    if limit > 0:
                        memories.append(limit)
                        constraints.append({'control':'memory limit bytes','value':limit})
                except ValueError:
                    pass
                if cpuset:
                    try:
                        allowed = parse_cpu_list(cpuset)
                        if allowed:
                            cpu_sets.append(allowed)
                            constraints.append({'control':'cpuset CPU count','value':len(allowed)})
                    except ValueError:
                        pass
                if current == base:
                    break
                current = current.parent
    permitted = set.intersection(*cpu_sets)
    if not permitted:
        raise ValueError('No CPU remains after applying affinity and cpuset constraints')
    quota_cores = min(quotas) if quotas else None
    worker_cpus = min(len(permitted), math.ceil(quota_cores)) if quota_cores else len(permitted)
    return {'hostCpuCount':host_cpus,'affinityCpuCount':len(affinity),
            'allowedCpuCount':len(permitted),'cpuQuotaCores':quota_cores,
            'effectiveCpuCount':max(1,worker_cpus),'hostMemoryBytes':host_memory,
            'effectiveMemoryBytes':min(memories),'cgroupVersions':sorted(versions),
            'constraints':constraints}


def environment(test_path, overrides=None, resources=None):
    cpu = read_text('/proc/cpuinfo')
    resources = resources or effective_resources()
    model = re.search(r'(?:model name|Hardware|Processor)\s*:\s*(.+)', cpu)
    detected = detect_provider(vendor=read_text('/sys/class/dmi/id/sys_vendor'))
    storage = []
    try:
        disks = json.loads(capture(['lsblk', '--json', '--bytes', '--output', 'NAME,SIZE,FSTYPE,MOUNTPOINT'], 5))
        def collect(devices):
            for device in devices:
                if len(storage) < 30:
                    storage.append({'device':str(device.get('name') or ''),'sizeBytes':int(device.get('size') or 0),'filesystem':str(device.get('fstype') or 'unknown'),'mount':str(device.get('mountpoint') or 'unmounted')})
                    collect(device.get('children') or [])
        collect(disks.get('blockdevices') or [])
    except (ValueError, OSError, WorkloadError):
        pass
    try:
        virtual = capture(['systemd-detect-virt'], 5).strip() or 'unknown'
    except (OSError, WorkloadError):
        virtual = 'unknown'
    os_release = read_text('/etc/os-release')
    distro = re.search(r'^PRETTY_NAME="?([^"\n]+)', os_release, re.M)
    try:
        clock = capture(['timedatectl', 'show', '-p', 'NTPSynchronized', '--value'], 3).strip() == 'yes'
    except (OSError, WorkloadError):
        clock = False
    return {**detected,'overrides':{k:observation(v,'Operator override', 'Original detected value remains unchanged', 'supplied') for k,v in (overrides or {}).items()},
            'cpu':{'model':model.group(1) if model else 'unknown','architecture':platform.machine(),'vcpus':resources['effectiveCpuCount']},
            'memoryBytes':resources['effectiveMemoryBytes'],'os':distro.group(1) if distro else platform.system(),
            'kernel':platform.release(),'virtualization':virtual,'storage':storage,'testPath':str(test_path),'clockReliable':clock}

def metric(name, value, unit, aggregation=None):
    if not math.isfinite(value) or value < 0:
        raise ValueError('Invalid non-finite or negative metric')
    result = {'name':name,'value':value,'unit':unit}
    if aggregation:
        result['aggregation'] = aggregation
    return result

def parse_sysbench(text, memory=False):
    pattern = r'([\d.]+) MiB transferred \(([\d.]+) MiB/sec\)' if memory else r'events per second:\s*([\d.]+)'
    match = re.search(pattern, text)
    if not match:
        raise ValueError('Missing sysbench summary')
    return [metric('Bandwidth' if memory else 'Events per second', float(match.group(2 if memory else 1)), 'MiB/s' if memory else 'events/s')]

def parse_fio(text):
    data = json.loads(text)
    result = []
    for job in data['jobs']:
        if job.get('error', 0):
            raise ValueError('fio job reported an error')
        for direction in ['read','write']:
            io = job[direction]
            if io.get('io_bytes', 0) > 0:
                result += [metric(direction+' bandwidth', float(io['bw_bytes'])/1_000_000,'MB/s'), metric(direction+' operations',float(io['iops']),'IOPS')]
                latency = io.get('clat_ns', {})
                for key, value in latency.get('percentile', {}).items():
                    if float(key) in [50,95,99]:
                        result.append(metric(direction+' p'+str(int(float(key)))+' latency',float(value)/1000,'us'))
    if not result:
        raise ValueError('Missing fio measured IO')
    return result

def parse_iperf(text):
    data = json.loads(text)
    if data.get('error'):
        raise ValueError('iperf reported an error')
    end = data['end']
    return [metric('Sender throughput',float(end['sum_sent']['bits_per_second'])/1e6,'Mbit/s'),metric('Receiver throughput',float(end['sum_received']['bits_per_second'])/1e6,'Mbit/s')]

def parse_pgbench(text):
    tps = re.search(r'tps = ([\d.]+)', text)
    latency = re.search(r'latency average = ([\d.]+) ms', text)
    failures = re.search(r'number of failed transactions:\s*(\d+)', text)
    if failures and int(failures.group(1)):
        raise ValueError('pgbench failed transactions')
    if not tps or not latency:
        raise ValueError('Missing pgbench summary')
    return [metric('Transactions',float(tps.group(1)),'transactions/s'),metric('Average latency',float(latency.group(1)),'ms')]

def tool_versions():
    commands = {'sysbench':['sysbench','--version'],'fio':['fio','--version'],'iperf3':['iperf3','--version'],'postgresql':['/usr/lib/postgresql/16/bin/postgres','--version'],'pgbench':['/usr/lib/postgresql/16/bin/pgbench','--version'],'gzip':['gzip','--version']}
    versions = {}
    for name, argv in commands.items():
        try:
            versions[name] = capture(argv,5).splitlines()[0][:200]
        except (OSError, WorkloadError, subprocess.TimeoutExpired):
            versions[name] = 'unavailable'
    for package in ['sysbench','fio','iperf3','postgresql-16','postgresql-client-16','gzip']:
        try:
            versions['apt:'+package]=capture(['dpkg-query','-W','-f=${Version}',package],5).strip()[:200]
        except (OSError,WorkloadError,subprocess.TimeoutExpired):
            versions['apt:'+package]='unavailable'
    return versions

def validate_target(target, allow_private=False):
    # Accept only bare DNS names / IP literals, never URL, userinfo or IPv6 scopes.
    try:
        ipaddress.ip_address(target)
    except ValueError:
        if not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]{0,251}[a-zA-Z0-9])?', target):
            raise ValueError('Network endpoint must be a bare hostname or IP address')
    if '%' in target:
        raise ValueError('Scoped IPv6 targets are not supported')
    addresses = socket.getaddrinfo(target,5201,type=socket.SOCK_STREAM)
    ips = [item[4][0] for item in addresses]
    private_networks = [ipaddress.ip_network(net) for net in ['10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','fc00::/7']]
    for value in ips:
        address = ipaddress.ip_address(value)
        effective = getattr(address,'ipv4_mapped',None) or address
        prohibited = effective.is_loopback or effective.is_link_local or effective.is_unspecified or effective.is_multicast
        private_owned = allow_private and any(effective.version==net.version and effective in net for net in private_networks)
        if prohibited or not (effective.is_global or private_owned):
            raise ValueError('Endpoint is not a permitted global address or explicitly enabled private RFC1918/ULA address; loopback, link-local and metadata are always forbidden')
    if not ips:
        raise ValueError('Network endpoint has no address')
    return ips[0] # Pin validated address for the measurement; do not resolve twice.

class Runner:
    def __init__(self, out, profile, targets, overrides, resume=False, allow_private_targets=False):
        supplied=Path(out)
        if supplied.is_symlink():
            raise ValueError('Report directory cannot be a symbolic link')
        self.out = supplied.resolve()
        self.out.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.out.stat().st_uid != os.getuid() or "'" in str(self.out):
            raise ValueError('Report directory must be owned by the current user')
        os.chmod(self.out,0o700)
        self.file = self.out/'report.json'
        self.work = self.out/'work'
        self.profile = profile
        self.targets = targets
        self.allow_private_targets = allow_private_targets
        self.seconds = 2 if profile == 'smoke' else 30
        self.repeats = 1 if profile == 'smoke' else 3
        self.disk_bytes = 128*1024**2 if profile == 'smoke' else 2*1024**3
        self.network_bytes = 8*1024**2 if profile == 'smoke' else 64*1024**2
        self.warmup_seconds = 1 if profile == 'smoke' else 5
        resources = effective_resources()
        config = {'profile':profile,'seconds':self.seconds,'repetitions':self.repeats,'targets':targets,'diskBytes':self.disk_bytes,'networkBytesPerDirection':self.network_bytes,'runnerRevision':2,'allowPrivateNetworkTargets':allow_private_targets,'architecture':platform.machine(),'effectiveCpuCount':resources['effectiveCpuCount'],'effectiveMemoryBytes':resources['effectiveMemoryBytes'],'databaseDatasetMode':'fixed-scale-1-2' if profile=='smoke' else 'effective-ram-20percent-200percent','warmupSeconds':self.warmup_seconds}
        if resume:
            self.report = json.loads(self.file.read_text())
            if self.report['methodology']['configHash'] != digest(json.dumps(config,sort_keys=True)):
                raise ValueError('Resume configuration differs from original report')
            if self.report.get('executionStatus')=='completed':
                raise ValueError('Completed reports are immutable; use submit to retry upload, not --resume')
            if (self.out/'receipt.json').exists():
                raise ValueError('Run was already submitted')
            for m in self.report['measurements']:
                if m['status']=='interrupted':
                    self.report['notes'].append('Interrupted attempt retained; resume uses new measurement IDs and does not merge attempts.')
        else:
            if self.file.exists():
                raise ValueError('A report already exists; use --resume or a new output directory')
            env = environment(self.out,overrides,resources)
            self.report = {'schemaVersion':SCHEMA,'runId':str(uuid.uuid4()),'deploymentId':deployment_id(),
              'agent':{'version':VERSION,'sourceCommit':source_commit(),'dependencies':tool_versions()},
              'methodology':{'version':'pilot-1','profile':profile,'configHash':digest(json.dumps(config,sort_keys=True)),
                'limitations':['Single deployment; not an independent three-instance cohort.','No hardware attestation.','Pilot memory implements read/write; copy workload deferred.','Network endpoints must be supplied by the operator; no public third-party servers are used.','Database latency distribution is not available from this pilot parser; average latency only.','This profile is not a full implementation of all proposed methodology tests.']},
              'startedAt':timestamp(),'endedAt':timestamp(),'executionStatus':'interrupted','environment':env,'measurements':[],'artifacts':[],'notes':[],'demo':False}
            if allow_private_targets:
                self.report['methodology']['limitations'].append('Operator explicitly enabled private RFC1918/ULA network targets. These endpoint-relative measurements are not public Internet throughput; retain a separate network-context comparison cohort.')
            if profile=='smoke':
                self.report['methodology']['limitations'].append('SMOKE COMPATIBILITY CHECK ONLY: 128 MiB disk file, fixed PostgreSQL scales 1/2 (~16/32 MiB), 1-second warmup, 2-second measurements, 8 MiB per network direction. Not suitable for provider rankings or RAM/storage-fit comparisons.')
            if resources['hostMemoryBytes']!=resources['effectiveMemoryBytes'] or resources['hostCpuCount']!=resources['effectiveCpuCount'] or resources['cgroupVersions']:
                self.report['methodology']['limitations'].append('Resource-constrained environment: CPU workers and RAM reflect affinity/cgroup limits, not host capacity. Private resource-limits evidence retains both; do not merge container/VM or architecture cohorts.')
            if resources['cpuQuotaCores'] is not None and resources['cpuQuotaCores']%1:
                self.report['methodology']['limitations'].append('Fractional CPU quota: worker count is rounded up for execution; actual quota cores remain separate in resource evidence. Not an equivalent whole-vCPU plan.')
            if platform.machine()=='aarch64':
                self.report['methodology']['limitations'].append('aarch64 architecture: retain a separate comparison cohort from x86_64.')
            if profile!='consistency-24h':
                self.report['methodology']['limitations'].append('Short profile: does not establish 24-hour consistency.')
            self.artifact('resource-limits',json.dumps(resources,sort_keys=True))
            atomic_json(self.file,self.report)
        if self.work.exists() and (self.work.is_symlink() or read_text(self.work/'.shapehost-work').strip()!=self.report['runId']):
            raise ValueError('Existing work path is not owned by this agent run. Choose a new output directory.')
        if self.work.exists() and not (self.work/'pgdata'/'postmaster.pid').exists():
            # Private stale work contains only agent-created files.
            shutil.rmtree(self.work)
        if self.work.exists():
            # Never delete an old PostgreSQL directory with a possibly running server.
            raise ValueError('Work directory contains a PostgreSQL PID record. Inspect the private cluster; stop it with pg_ctl -D <report>/work/pgdata -m fast stop before resuming. No automatic deletion is performed.')
        self.work.mkdir(mode=0o700)
        (self.work/'.shapehost-work').write_text(self.report['runId'])
        self.pg_started = False
        self.stopping = False

    def save(self):
        self.report['endedAt'] = timestamp()
        atomic_json(self.file,self.report)

    def artifact(self, measurement_id, output):
        aid = measurement_id+'-raw'
        existing = {a['id'] for a in self.report['artifacts']}
        if aid in existing:
            aid += '-attempt'+str(sum(a['id'].startswith(aid) for a in self.report['artifacts']))
        content = output[:MAX_ARTIFACT]
        entry = {'id':aid,'name':aid+'.log','mediaType':'text/plain','content':content,'sha256':digest(content)}
        self.report['artifacts'].append(entry)
        (self.out/entry['name']).write_text(content)
        os.chmod(self.out/entry['name'],0o600)
        return aid

    def completed(self, key):
        return any(re.fullmatch(re.escape(key)+r'(?:-retry\d+)?', m['id']) and m['status']=='passed'
                   for m in self.report['measurements'])

    def next_attempt(self, key):
        ids = {m['id'] for m in self.report['measurements']}
        if key not in ids:
            return key
        number = 1
        while f'{key}-retry{number}' in ids:
            number += 1
        return f'{key}-retry{number}'

    def measure(self, key, category, name, command, parser, config, repetition=1, timeout=None):
        # A successful retry is a completed boundary too. Failed attempts never
        # disappear, and are not mixed into measured consistency samples.
        if self.completed(key):
            return
        key = self.next_attempt(key)
        start = time.monotonic()
        m = {'id':key,'category':category,'name':name,'status':'interrupted','startedAt':timestamp(),'endedAt':timestamp(),'durationSeconds':0,'repetition':repetition,'metrics':[],'config':config,'artifactIds':[],'error':'Attempt did not reach a safe boundary'}
        self.report['measurements'].append(m)
        self.save()
        print(f'[{category}] {name}',flush=True)
        output = ''
        try:
            output = command() if callable(command) else capture(command,timeout or self.seconds+30,cwd=self.work)
            m['metrics'] = parser(output)
            if not m['metrics']:
                raise ValueError('Parser produced no measurements')
            m['status'] = 'passed'
            m.pop('error',None)
        except (KeyboardInterrupt, SystemExit) as failure:
            output=getattr(failure,'output',output)
            m['status']='interrupted'
            m['error']='Interrupted; partial measurement excluded'
            raise
        except Exception as e:
            output = getattr(e,'output',None) or output
            m['metrics']=[]
            m['status']='failed'
            m['error']=f'{type(e).__name__}: {str(e)[:300]}'
        finally:
            m['durationSeconds']=round(time.monotonic()-start,3)
            m['endedAt']=timestamp()
            m['artifactIds']=[self.artifact(key,output or m.get('error','No output'))]
            self.save()

    def outcome(self, key, category, name, status, reason, output='', config=None):
        if status == 'skipped' and any(m['id']==key for m in self.report['measurements']):
            return
        key = self.next_attempt(key)
        self.report['measurements'].append({'id':key,'category':category,'name':name,
            'status':status,'startedAt':timestamp(),'endedAt':timestamp(),'durationSeconds':0,
            'repetition':1,'metrics':[],'config':config or {},
            'artifactIds':[self.artifact(key,output or reason)],'error':reason[:2000]})
        self.save()

    def skipped(self, key, category, name, reason):
        self.outcome(key,category,name,'skipped',reason)

    def failed(self, key, category, name, error, config=None):
        self.outcome(key,category,name,'failed',f'{type(error).__name__}: {str(error)[:500]}',
                     getattr(error,'output',''),config)

    def prepare_resumed_disk(self, disk):
        command=['fio','--name=shapehost-resume-prepare',f'--filename={disk}',
                 f'--size={self.disk_bytes}','--rw=write','--bs=1M','--direct=1',
                 '--ioengine=libaio','--iodepth=32','--numjobs=1','--end_fsync=1',
                 '--output-format=json']
        output=capture(command,30 if self.profile=='smoke' else 180,cwd=self.work)
        parse_fio(output)
        self.artifact('disk-resume-preparation',output)
        self.report['notes'].append('Resume recreated the dedicated disk file with an unscored full sequential write before continuing. Retain this precondition difference when reviewing partial/retried runs.')
        self.save()

    def baseline(self, round_index=0, selected=False):
        cpus=self.report['environment']['cpu']['vcpus']
        for rep in range(1,(1 if selected else self.repeats)+1):
            prefix=f'r{round_index}-n{rep}'
            for threads in ([cpus] if selected else sorted(set([1,cpus]))):
                self.measure(f'{prefix}-cpu-{threads}','cpu',f'CPU {threads} thread(s)', ['sysbench','cpu',f'--threads={threads}',f'--time={self.seconds}','--events=0','--cpu-max-prime=20000','run'],parse_sysbench,{'threads':threads,'prime':20000,'seconds':self.seconds},rep)
            for operation in (['read'] if selected else ['read','write']):
                self.measure(f'{prefix}-memory-{operation}','memory',f'Memory {operation}', ['sysbench','memory','--threads=1','--memory-block-size=256M','--memory-total-size=1000000G',f'--time={self.seconds}','--events=0',f'--memory-oper={operation}','run'],lambda x:parse_sysbench(x,True),{'blockBytes':256*1024**2,'threads':1,'operation':operation,'seconds':self.seconds},rep)
            disk=self.work/'fio-test.bin'
            modes=['randread'] if selected else ['write','read','randwrite','randread','randrw']
            for mode in modes:
                command=['fio','--name=shapehost',f'--filename={disk}',f'--size={self.disk_bytes}','--ioengine=libaio','--direct=1','--iodepth=32','--numjobs=1',f'--rw={mode}',f'--bs={"1M" if mode in ["read","write"] else "4k"}',f'--runtime={self.seconds}','--time_based=1','--group_reporting=1','--output-format=json','--percentile_list=50:95:99']
                if mode=='randrw':
                    command.append('--rwmixread=70')
                if not self.completed(f'{prefix}-disk-{mode}') and not disk.exists() and mode!='write':
                    self.prepare_resumed_disk(disk)
                self.measure(f'{prefix}-disk-{mode}','disk',f'Disk {mode}',command,parse_fio,{'rw':mode,'fileBytes':self.disk_bytes,'blockBytes':1024**2 if mode in ['read','write'] else 4096,'queueDepth':32,'direct':True,'seconds':self.seconds,'readMixPercent':70 if mode=='randrw' else None},rep)
        if not selected and not self.completed('compression'):
            source=self.work/'compression-input'
            # Deterministic, bounded corpus; no files from the host are compressed.
            source.write_bytes((b'Shape.host fixed compression corpus 0123456789\n'*1600)*1024)
            def compress_file():
                start=time.monotonic()
                capture(['gzip','-6','-c',str(source)],60,cwd=self.work)
                return json.dumps({'elapsed':time.monotonic()-start,'bytes':source.stat().st_size})
            self.measure('compression','cpu','gzip fixed corpus',compress_file,lambda x:[metric('Compression input throughput',json.loads(x)['bytes']/json.loads(x)['elapsed']/1024**2,'MiB/s')],{'level':6,'inputBytes':source.stat().st_size})
            source.unlink(missing_ok=True)

    def network(self):
        if not self.targets:
            self.skipped('network-unconfigured','network','Controlled network endpoints','No controlled endpoint configured. Network benchmark was not run.')
            return
        for index,target in enumerate(self.targets):
            try:
                address=validate_target(target,self.allow_private_targets)
            except (ValueError,OSError) as e:
                self.failed(f'network-{index}-invalid','network','Configured network endpoint unavailable',e)
                continue
            for reverse in [False,True]:
                direction='download' if reverse else 'upload'
                command=['iperf3','-c',address,'-p','5201','-n',str(self.network_bytes),'-b','100M','-P','1','-J','--connect-timeout','5000']
                if reverse:
                    command.append('-R')
                self.measure(f'network-{index}-{direction}','network',f'Network {direction} endpoint {index+1}',command,parse_iperf,{'destination':target,'resolvedAddress':address,'allowPrivateTargets':self.allow_private_targets,'direction':direction,'bytesLimit':self.network_bytes,'rateBitsPerSecond':100_000_000,'streams':1},timeout=60)
            self.measure(f'network-{index}-latency','network',f'Latency endpoint {index+1}',['ping','-n','-c','10','-W','2',address],lambda s:[metric('Round-trip mean',float(re.search(r'= [\d.]+/([\d.]+)/',s).group(1)),'ms')],{'destination':target,'probes':10,'allowPrivateTargets':self.allow_private_targets},timeout=30)

    def database(self):
        binary='/usr/lib/postgresql/16/bin/'
        if all(self.completed(f'pg-{kind}-{rep}') for kind in ['memory','storage'] for rep in range(1,self.repeats+1)):
            return
        cluster=self.work/'pgdata'
        socket_dir=self.work/'pgsocket'
        stage='prepare'
        try:
            socket_dir.mkdir(mode=0o700)
            if not Path(binary+'initdb').exists():
                raise WorkloadError('PostgreSQL 16 unavailable. Run the installer.')
            stage='initdb'
            output=capture([binary+'initdb','-D',str(cluster),'--no-locale','--encoding=UTF8','--auth-local=trust','--auth-host=reject'],60)
            self.artifact('postgresql-initdb',output)
            with (cluster/'postgresql.conf').open('a') as f:
                f.write(f"\nlisten_addresses = ''\nunix_socket_directories = '{socket_dir}'\nshared_buffers = '256MB'\nfsync = on\nsynchronous_commit = on\nfull_page_writes = on\nmax_connections = 20\n")
            stage='start'
            self.pg_started=True # Also clean a server that starts just before pg_ctl fails.
            capture([binary+'pg_ctl','-D',str(cluster),'-l',str(self.work/'postgres.log'),'-w','-t','30','start'],35)
            ram=self.report['environment']['memoryBytes']
            scales={'memory':1,'storage':2} if self.profile=='smoke' else {
                'memory':max(1,int(ram*0.20/(16*1024**2))),
                'storage':max(1,math.ceil(ram*2/(16*1024**2)))}
            for kind,scale in scales.items():
                if all(self.completed(f'pg-{kind}-{rep}') for rep in range(1,self.repeats+1)):
                    continue
                database='shapehost_'+kind
                stage='createdb:'+kind
                capture([binary+'createdb','-h',str(socket_dir),database],10)
                stage='initialize:'+kind
                init=capture([binary+'pgbench','-h',str(socket_dir),'-i','-s',str(scale),database],30 if self.profile=='smoke' else min(900,max(120,scale*2)))
                self.artifact('pg-'+kind+'-initialization',init)
                stage='warmup:'+kind
                warm=capture([binary+'pgbench','-h',str(socket_dir),'-c','2','-j','2','-T',str(self.warmup_seconds),database],self.warmup_seconds+15)
                self.artifact('pg-'+kind+'-warmup',warm)
                for rep in range(1,self.repeats+1):
                    self.measure(f'pg-{kind}-{rep}','database',f'PostgreSQL {kind} dataset',[binary+'pgbench','-h',str(socket_dir),'-c','4','-j','2','-T',str(self.seconds),'-P','1' if self.profile=='smoke' else '5',database],parse_pgbench,{'dataset':kind,'scale':scale,'estimatedDatasetBytes':scale*16*1024**2,'datasetMode':'fixed-small-compatibility' if self.profile=='smoke' else 'RAM-relative','clients':4,'threads':2,'sharedBuffersMiB':256,'warmupSeconds':self.warmup_seconds,'seconds':self.seconds,'fsync':True,'synchronousCommit':True,'fullPageWrites':True,'clientPlacement':'same private Unix socket; no TCP listening'},rep)
                stage='dropdb:'+kind
                capture([binary+'dropdb','-h',str(socket_dir),database],30)
        except Exception as error:
            self.failed('database-setup-failed','database','PostgreSQL setup failed',error,{'stage':stage})
        finally:
            log=read_text(self.work/'postgres.log',MAX_ARTIFACT)
            if log:
                self.artifact('postgresql-server',log)
            try:
                self.stop_postgres()
            except Exception as error:
                self.failed('database-cleanup-failed','database','PostgreSQL cleanup failed',error)

    def stop_postgres(self):
        pid=self.work/'pgdata'/'postmaster.pid'
        if pid.exists():
            capture(['/usr/lib/postgresql/16/bin/pg_ctl','-D',str(self.work/'pgdata'),'-m','fast','-w','-t','30','stop'],35)
        self.pg_started=False

    def successful_boundary(self, key):
        return next((m for m in self.report['measurements']
                     if re.fullmatch(re.escape(key)+r'(?:-retry\d+)?',m['id']) and m['status']=='passed'),None)

    def consistency(self):
        if self.profile!='consistency-24h':
            self.skipped('consistency-short','consistency','24-hour consistency','Short profile only. No 24-hour consistency result is claimed.')
            return
        start=dt.datetime.fromisoformat(self.report['startedAt'].replace('Z','+00:00')).timestamp()
        observations={}
        for artifact in self.report['artifacts']:
            match=re.fullmatch(r'consistency-hour-(\d+)-observation-raw',artifact['id'])
            if match:
                observations[int(match[1])]=json.loads(artifact['content'])
        cpus=self.report['environment']['cpu']['vcpus']
        keys=lambda hour:[f'r{hour}-n1-cpu-{cpus}',f'r{hour}-n1-memory-read',f'r{hour}-n1-disk-randread']
        for hour in range(1,25):
            if all(self.completed(key) for key in keys(hour)):
                continue
            target=start+hour*3600
            if time.time() > target+300:
                self.skipped(f'consistency-hour-{hour}-missed','consistency',f'Missed consistency hour {hour}','Scheduled observation missed by more than five minutes; not backfilled.')
                continue
            while time.time()<target:
                time.sleep(max(0.01,min(30,target-time.time())))
            if hour not in observations:
                stat=read_text('/proc/stat')
                observations[hour]={'hour':hour,'at':timestamp(),'procStatCpuLine':stat.splitlines()[0] if stat else 'unknown'}
                self.artifact(f'consistency-hour-{hour}-observation',json.dumps(observations[hour]))
                self.save() # A restart reuses the original time-series observation.
            self.baseline(round_index=hour,selected=True)
        complete_hours=[hour for hour in range(1,25) if hour in observations and all(self.completed(key) for key in keys(hour))]
        aggregate=[metric('Observed interval',max(0,time.time()-start),'seconds')]
        for index,category in enumerate(['cpu','memory','disk']):
            samples=[self.successful_boundary(keys(hour)[index])['metrics'][0] for hour in complete_hours]
            if samples:
                units={sample['unit'] for sample in samples}
                names={sample['name'] for sample in samples}
                if len(units)!=1 or len(names)!=1:
                    raise ValueError('Incomparable consistency metric identities; refusing aggregation')
                values=[sample['value'] for sample in samples]
                unit=samples[0]['unit']
                aggregate.extend([metric(category+' consistency median',statistics.median(values),unit,'median'),
                                  metric(category+' consistency minimum',min(values),unit,'minimum'),
                                  metric(category+' consistency maximum',max(values),unit,'maximum')])
        raw=json.dumps({'observations':[observations[h] for h in sorted(observations)],
                        'completeHours':complete_hours,'partialSummary':aggregate},sort_keys=True)
        def parse_summary(text):
            data=json.loads(text)
            if len(data['completeHours'])!=24:
                raise WorkloadError(f'Only {len(data["completeHours"])}/24 scheduled hourly boundaries completed; partial aggregates are retained in raw evidence',text)
            return data['partialSummary']
        self.measure('consistency-summary','consistency','24-hour repeated observations',lambda:raw,parse_summary,
                     {'scheduledSamples':24,'actualObservations':len(complete_hours),
                      'attemptedObservations':len(observations),'spacingSeconds':3600,
                      'cpuSteal':'raw cumulative /proc/stat cpu counters; not normalized steal percentage'})

    def run(self):
        interrupted=False
        try:
            self.baseline()
            self.network()
            self.database()
            self.consistency()
        except (KeyboardInterrupt,SystemExit):
            interrupted=True
            self.report['executionStatus']='interrupted'
            self.report['notes'].append('Run interrupted; incomplete attempts retained. Resume at completed test boundaries only.')
            raise
        except Exception as error:
            self.failed('execution-failed','consistency','Agent execution failed',error)
        finally:
            try:
                self.stop_postgres()
            except Exception as error:
                self.failed('database-final-cleanup-failed','database','PostgreSQL final cleanup failed',error)
            if (self.work/'pgdata'/'postmaster.pid').exists():
                self.report['notes'].append('Private PostgreSQL PID record remains; work files preserved. Inspect and stop only this agent-created cluster before resume.')
            else:
                try:
                    shutil.rmtree(self.work)
                except OSError as error:
                    self.failed('work-cleanup-failed','consistency','Private work-directory cleanup failed',error)
            for category in ['cpu','memory','disk','network','database','consistency']:
                if not any(m['category']==category for m in self.report['measurements']):
                    self.skipped(f'not-attempted-{category}',category,category.title()+' not attempted',
                                 'Agent execution ended before this workload; no score is claimed.')
            if not interrupted:
                self.report['executionStatus']='failed' if any(m['status'] in ['failed','interrupted'] for m in self.report['measurements']) else 'completed'
            self.save()
        return self.report

def deployment_id():
    # A persisted random deployment identity prevents simple retries being independent
    # samples. It is not a host fingerprint or proof of independent provisioning.
    base=Path.home()/'.local/state/shapehost-agent'
    base.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=base/'deployment-id'
    if path.exists():
        return str(uuid.UUID(path.read_text().strip()))
    value=str(uuid.uuid4())
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:
        f.write(value+'\n')
    return value

def source_commit():
    try:
        value=Path(__file__).with_name('SOURCE_COMMIT').read_text().strip()
        return value[:100]
    except OSError:
        return 'development-unreleased'

def submit(report_path, endpoint, token, attempts=3, allow_local=False):
    endpoint=endpoint.rstrip('/')+'/api/intake' if not endpoint.rstrip('/').endswith('/api/intake') else endpoint
    url=urllib.parse.urlparse(endpoint)
    if url.username or url.password or url.query or url.fragment:
        raise ValueError('Submission endpoint must not contain credentials, query or fragment')
    local=allow_local and url.hostname in ['localhost','127.0.0.1','::1']
    if url.scheme!='https' and not (local and url.scheme=='http'):
        raise ValueError('HTTPS submission endpoint required')
    if not token or len(token)>200 or not re.fullmatch(r'[A-Za-z0-9_-]+',token):
        raise ValueError('Submission token missing or invalid')
    payload=Path(report_path).read_bytes()
    if len(payload)>8*1024**2:
        raise ValueError('Report exceeds 8 MiB intake limit')
    for attempt in range(attempts):
        request=urllib.request.Request(endpoint,data=payload,headers={'Content-Type':'application/json','Authorization':'Bearer '+token},method='POST')
        try:
            with HTTP.open(request,timeout=30) as response:
                receipt=json.loads(response.read(16384))
                if not receipt.get('id'):
                    raise ValueError('Receipt lacks submission identifier')
                atomic_json(Path(report_path).with_name('receipt.json'),receipt)
                return receipt
        except urllib.error.HTTPError as e:
            if e.code not in [408,429,500,502,503,504]:
                raise RuntimeError(f'Intake rejected submission (HTTP {e.code}); local report retained') from None
        except (OSError,urllib.error.URLError):
            pass
        if attempt+1<attempts:
            time.sleep(2**attempt)
    raise RuntimeError('Submission failed after retries. Local report retained; use submit without rerunning workloads.')

def preflight(profile, output):
    release=read_text('/etc/os-release')
    if platform.system()!='Linux' or platform.machine() not in ['x86_64','aarch64'] or not re.search(r'^ID=ubuntu$',release,re.M) or not re.search(r'^VERSION_ID="24.04"$',release,re.M):
        raise ValueError('Supported benchmark environment: Ubuntu 24.04 x86-64 or aarch64. Use --dry-run to inspect workload configuration on other systems.')
    if os.geteuid()==0:
        raise ValueError('Run the workload as an unprivileged user after installing dependencies with sudo.')
    missing=[name for name,version in tool_versions().items() if version=='unavailable']
    if missing:
        raise ValueError('Run agent/install.sh to automatically prepare dependencies: '+', '.join(missing))
    ram=effective_resources()['effectiveMemoryBytes']
    if not 2*1024**3 <= ram <= 16*1024**3:
        raise ValueError('Pilot supports 2–16 GiB RAM to bound memory and PostgreSQL storage workload sizes.')
    base=Path(output).resolve()
    while not base.exists():
        base=base.parent
    required=(512*1024**2 if profile=='smoke' else ram*3+4*1024**3)
    if shutil.disk_usage(base).free<required:
        raise ValueError(f'Need at least {required//1024**3} GiB free for dedicated files and isolated PostgreSQL datasets.')

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version',action='version',version='Shape.host agent '+VERSION)
    sub=parser.add_subparsers(dest='command',required=True)
    run=sub.add_parser('run')
    run.add_argument('--profile',choices=['pilot','smoke','consistency-24h'],default='pilot')
    run.add_argument('--output',default='./shapehost-report')
    run.add_argument('--network-endpoint',action='append',default=[])
    run.add_argument('--allow-private-network-targets',action='store_true',help='Explicitly allow operator-owned RFC1918/ULA endpoints; loopback/link-local/metadata remain forbidden')
    run.add_argument('--provider')
    run.add_argument('--region')
    run.add_argument('--plan')
    run.add_argument('--resume',action='store_true')
    run.add_argument('--dry-run',action='store_true')
    run.add_argument('--local-only',action='store_true')
    run.add_argument('--allow-local-intake',action='store_true')
    upload=sub.add_parser('submit')
    upload.add_argument('report')
    upload.add_argument('--allow-local-intake',action='store_true')
    inspect=sub.add_parser('inspect')
    inspect.add_argument('report')
    args=parser.parse_args(argv)
    if args.command=='inspect':
        report=json.loads(Path(args.report).read_text())
        print(json.dumps({'runId':report['runId'],'profile':report['methodology']['profile'],'executionStatus':report['executionStatus'],'measurements':[{'name':m['name'],'status':m['status'],'metrics':m['metrics']} for m in report['measurements']]},indent=2))
        return 0
    endpoint=os.environ.get('SHAPEHOST_ENDPOINT','')
    token=os.environ.get('SHAPEHOST_SUBMISSION_TOKEN','')
    if args.command=='submit':
        if not endpoint:
            raise ValueError('Configure SHAPEHOST_ENDPOINT and SHAPEHOST_SUBMISSION_TOKEN')
        receipt=submit(args.report,endpoint,token,allow_local=args.allow_local_intake)
        print('Submission receipt: '+receipt['id'])
        return 0
    if len(args.network_endpoint)>2:
        raise ValueError('At most two controlled network endpoints are supported')
    summary={'profile':args.profile,'os':'Ubuntu 24.04 x86_64 / aarch64 (architecture cohorts remain separate)','privileges':'unprivileged; install uses sudo apt','expectedDuration':'24 hours + workload tail' if args.profile=='consistency-24h' else 'normally under 2 minutes; workload compatibility only, not ranking evidence' if args.profile=='smoke' else 'approximately 10–60 minutes including database initialization; each initialization has a 15-minute maximum (smoke uses shorter measurements)','disk':'512 MiB free; 128 MiB file and fixed small datasets' if args.profile=='smoke' else '3 × effective RAM + 4 GiB free; dedicated files only','networkMaxPayloadMiB':(16 if args.profile=='smoke' else 128)*len(args.network_endpoint),'networkRateMbps':100,'networkEndpoints':args.network_endpoint,'allowPrivateNetworkTargets':args.allow_private_network_targets,'limitations':'No configured endpoints means skipped network tests. Pilot is not a full methodology campaign. No local heavy workload is run in --dry-run.'}
    print(json.dumps(summary,indent=2),flush=True)
    if args.dry_run:
        return 0
    if not args.local_only and bool(endpoint)!=bool(token):
        raise ValueError('Configure both endpoint and submission token, or use --local-only')
    preflight(args.profile,args.output)
    overrides={key:getattr(args,key) for key in ['provider','region','plan'] if getattr(args,key)}
    runner=Runner(args.output,args.profile,args.network_endpoint,overrides,args.resume,args.allow_private_network_targets)
    runner.run()
    print('Local report saved: '+str(runner.file),flush=True)
    if endpoint and token and not args.local_only:
        receipt=submit(runner.file,endpoint,token,allow_local=args.allow_local_intake)
        print('Submission receipt: '+receipt['id'])
    elif not args.local_only:
        print('No intake credential configured; report retained locally. Configure credentials to submit.')
    return 0 if runner.report['executionStatus']=='completed' else 2

if __name__=='__main__':
    try:
        sys.exit(main())
    except (ValueError,RuntimeError,OSError) as error:
        print('Error: '+str(error),file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print('Interrupted; local evidence retained.',file=sys.stderr)
        sys.exit(130)
