import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import sys
import datetime as dt
import unittest
from unittest.mock import patch
import urllib.error

MODULE = Path(__file__).resolve().parents[1]/'shapehost_agent.py'
spec=importlib.util.spec_from_file_location('agent',MODULE)
a=importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)

class DetectionTests(unittest.TestCase):
    def detector(self, values):
        calls=[]
        def fetch(path,**kwargs):
            calls.append(path)
            return values.get(path)
        result=a.detect_provider(fetch)
        self.assertLessEqual(len(calls),7)
        self.assertFalse(any('user-data' in c or 'identity' in c for c in calls))
        return result

    def test_hetzner_zone_is_not_city_and_plan_unknown(self):
        found=self.detector({'/hetzner/v1/metadata/region':'eu-central','/hetzner/v1/metadata/availability-zone':'fsn1-dc14'})
        self.assertEqual(found['provider']['value'],'Hetzner')
        self.assertEqual(found['zone']['value'],'fsn1-dc14')
        self.assertEqual(found['region']['value'],'eu-central')
        self.assertEqual(found['plan']['status'],'unknown')

    def test_digitalocean(self):
        found=self.detector({'/metadata/v1/region':'fra1'})
        self.assertEqual(found['provider']['value'],'DigitalOcean')
        self.assertEqual(found['plan']['status'],'unknown')

    def test_vultr(self):
        self.assertEqual(self.detector({'/v1/region/regioncode':'EWR'})['provider']['value'],'Vultr')

    def test_akamai_allowlist_discards_secrets(self):
        found=self.detector({'/v1/token':'do-not-retain-token','/v1/instance':json.dumps({'region':'de-fra-2','type':'g6-standard-2','label':'SECRET_LABEL','id':123,'public_keys':['SECRET_KEY']})})
        self.assertEqual(found['provider']['value'],'Akamai / Linode')
        for secret in ['SECRET_LABEL','SECRET_KEY','do-not-retain-token']:
            self.assertNotIn(secret,json.dumps(found))

    def test_ovh_hint_does_not_claim_identity(self):
        found=a.detect_provider(lambda *a,**k:None,vendor='OVHcloud')
        self.assertEqual(found['provider']['status'],'unknown')
        self.assertIsNone(found['region']['value'])
        self.assertIn('hint',found['provider']['evidence'])

    def test_conflict_unknown_and_arbitrary_metadata_paths(self):
        found=self.detector({'/metadata/v1/region':'fra1','/v1/region/regioncode':'EWR'})
        self.assertEqual(found['provider']['status'],'conflict')
        found=self.detector({'/metadata/v1/region':'<html>not metadata</html>'})
        self.assertEqual(found['provider']['status'],'unknown')
        with self.assertRaises(ValueError):
            a.fetch_metadata('/latest/user-data')

class ParserTests(unittest.TestCase):
    def test_sysbench(self):
        self.assertEqual(a.parse_sysbench('events per second: 1234.56')[0]['value'],1234.56)
        self.assertEqual(a.parse_sysbench('1000.00 MiB transferred (2048.00 MiB/sec)',True)[0]['value'],2048)
        with self.assertRaises(ValueError):a.parse_sysbench('command failed')

    def test_fio(self):
        metrics=a.parse_fio(json.dumps({'jobs':[{'error':0,'read':{'io_bytes':1234,'bw_bytes':100000000,'iops':5000,'clat_ns':{'percentile':{'50.000000':10000,'95.000000':20000,'99.000000':30000}}},'write':{'io_bytes':0}}]}))
        self.assertEqual(metrics[0]['value'],100)
        self.assertEqual(metrics[-1]['value'],30)
        with self.assertRaises(ValueError):a.parse_fio('{"jobs":[{"error":5}]}')

    def test_network_pgbench_failure(self):
        self.assertEqual(a.parse_iperf('{"end":{"sum_sent":{"bits_per_second":100000000},"sum_received":{"bits_per_second":90000000}}}')[0]['value'],100)
        text='latency average = 1.25 ms\ntps = 500.00 (without initial connection time)\nnumber of failed transactions: 0'
        self.assertEqual(a.parse_pgbench(text)[0]['value'],500)
        with self.assertRaises(ValueError):a.parse_pgbench(text.replace('transactions: 0','transactions: 1'))

    def test_ipv6_reserved_and_mapped_addresses_are_rejected(self):
        for address in ['fe80::1','::1','2001:db8::1','::ffff:10.0.0.1']:
            with patch.object(a.socket,'getaddrinfo',return_value=[(10,1,6,'',(address,5201,0,0))]):
                with self.assertRaises(ValueError):a.validate_target('endpoint.example')
        with patch.object(a.socket,'getaddrinfo',return_value=[(10,1,6,'',('2606:4700:4700::1111',5201,0,0))]):
            self.assertEqual(a.validate_target('endpoint.example'),'2606:4700:4700::1111')

    def test_target_rejects_linklocal_and_shell(self):
        with self.assertRaises(ValueError):a.validate_target('a; curl bad')
        with patch.object(a.socket,'getaddrinfo',return_value=[(2,1,6,'',('169.254.169.254',5201))]):
            with self.assertRaises(ValueError):a.validate_target('metadata.example')

class ResourceTests(unittest.TestCase):
    def resources(self, files, affinity=None):
        defaults={'/proc/meminfo':'MemTotal: 8388608 kB'}
        defaults.update(files)
        with patch.object(a.os,'cpu_count',return_value=16),patch.object(a.os,'sched_getaffinity',return_value=set(range(8)) if affinity is None else affinity,create=True):
            return a.effective_resources(lambda path:defaults.get(str(path),''))

    def test_v2_limits_include_ancestors_and_do_not_expose_cgroup_identity(self):
        resources=self.resources({'/proc/self/cgroup':'0::/secret-container',
            '/proc/self/mountinfo':'1 0 0:1 / /sys/fs/cgroup rw - cgroup2 cgroup rw',
            '/sys/fs/cgroup/secret-container/cpu.max':'200000 100000',
            '/sys/fs/cgroup/secret-container/memory.max':str(2*1024**3),
            '/sys/fs/cgroup/secret-container/cpuset.cpus.effective':'0-3',
            '/sys/fs/cgroup/cpu.max':'100000 100000','/sys/fs/cgroup/memory.max':str(4*1024**3)})
        self.assertEqual(resources['effectiveCpuCount'],1)
        self.assertEqual(resources['effectiveMemoryBytes'],2*1024**3)
        self.assertEqual(resources['hostCpuCount'],16)
        self.assertEqual(resources['hostMemoryBytes'],8*1024**3)
        self.assertNotIn('secret-container',json.dumps(resources))

    def test_v1_and_fractional_cpu_quota(self):
        resources=self.resources({'/proc/self/cgroup':'2:cpu,cpuacct:/slice\n3:memory:/slice',
            '/proc/self/mountinfo':'1 0 0:1 / /sys/fs/cgroup/cpu rw - cgroup cgroup rw,cpu,cpuacct\n2 0 0:2 / /sys/fs/cgroup/memory rw - cgroup cgroup rw,memory',
            '/sys/fs/cgroup/cpu/slice/cpu.cfs_quota_us':'150000',
            '/sys/fs/cgroup/cpu/slice/cpu.cfs_period_us':'100000',
            '/sys/fs/cgroup/memory/slice/memory.limit_in_bytes':str(2*1024**3)})
        self.assertEqual(resources['cpuQuotaCores'],1.5)
        self.assertEqual(resources['effectiveCpuCount'],2)
        self.assertEqual(resources['effectiveMemoryBytes'],2*1024**3)
        self.assertEqual(resources['cgroupVersions'],[1])

    def test_unlimited_cgroup_and_affinity(self):
        resources=self.resources({'/proc/self/cgroup':'0::/',
            '/proc/self/mountinfo':'1 0 0:1 / /sys/fs/cgroup rw - cgroup2 cgroup rw',
            '/sys/fs/cgroup/cpu.max':'max 100000','/sys/fs/cgroup/memory.max':'max'},affinity={3,5})
        self.assertEqual(resources['effectiveCpuCount'],2)
        self.assertEqual(resources['effectiveMemoryBytes'],8*1024**3)
        self.assertIsNone(resources['cpuQuotaCores'])
        with self.assertRaises(ValueError):a.parse_cpu_list('2-99999999')

    def test_native_arm_smoke_preflight_uses_effective_not_host_ram(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(a.platform,'system',return_value='Linux'),patch.object(a.platform,'machine',return_value='aarch64'),patch.object(a.os,'geteuid',return_value=1000),patch.object(a,'read_text',return_value='ID=ubuntu\nVERSION_ID="24.04"'),patch.object(a,'tool_versions',return_value={'sysbench':'1.0.20'}),patch.object(a,'effective_resources',return_value={'effectiveMemoryBytes':2*1024**3}):
            a.preflight('smoke',folder)


class UploadTests(unittest.TestCase):
    def test_local_requires_explicit_flag_and_https(self):
        with self.assertRaises(ValueError):a.submit('/missing','http://remote.test','token')
        with self.assertRaises(ValueError):a.submit('/missing','https://user:secret@remote.test','token')

    def test_upload_retries_identical_payload_retains_report_and_saves_receipt(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,n):return b'{"id":"server-receipt","status":"pending_review"}'
        requests=[]
        def upload(req,timeout):
            requests.append(req)
            if len(requests)==1:raise urllib.error.URLError('temporary failure')
            return Response()
        with tempfile.TemporaryDirectory() as folder:
            report=Path(folder)/'report.json'
            payload=b'{"runId":"retained"}'
            report.write_bytes(payload)
            with patch.object(a.HTTP,'open',side_effect=upload),patch.object(a.time,'sleep'):
                receipt=a.submit(report,'https://shape.host','private-token')
            self.assertEqual(receipt['id'],'server-receipt')
            self.assertEqual(requests[0].data,requests[1].data)
            self.assertEqual(report.read_bytes(),payload)
            self.assertTrue((Path(folder)/'receipt.json').exists())
            self.assertNotIn('private-token',(Path(folder)/'receipt.json').read_text())

    def test_upload_401_does_not_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'report.json';path.write_text('{}')
            with patch.object(a.HTTP,'open',side_effect=urllib.error.HTTPError('https://shape.host',401,'',{},None)) as mocked:
                with self.assertRaises(RuntimeError):a.submit(path,'https://shape.host','private-token')
                self.assertEqual(mocked.call_count,1)
            self.assertTrue(path.exists())

class ExecutionBoundaryTests(unittest.TestCase):
    def test_dry_run_never_detects_or_runs_benchmarks(self):
        with patch.object(a,'preflight',side_effect=AssertionError('must not preflight')),contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(a.main(['run','--dry-run']),0)
            self.assertIn('No local heavy workload',output.getvalue())

    def test_workload_argv_has_private_file_and_explicit_units(self):
        with tempfile.TemporaryDirectory() as folder:
            runner=object.__new__(a.Runner);runner.work=Path(folder);runner.seconds=30;runner.disk_bytes=2*1024**3
            runner.prepare_resumed_disk=lambda path:None
            runner.report={'environment':{'cpu':{'vcpus':4}},'measurements':[]}
            calls=[]
            runner.measure=lambda *args,**kwargs:calls.append(args)
            runner.baseline(round_index=1,selected=True)
            memory=next(call for call in calls if call[1]=='memory')
            self.assertIn('--memory-block-size=256M',memory[3])
            self.assertIn('--time=30',memory[3])
            disk=next(call for call in calls if call[1]=='disk')
            self.assertIn('--size=2147483648',disk[3])
            self.assertIn('--direct=1',disk[3])
            filename=next(arg for arg in disk[3] if arg.startswith('--filename='))
            self.assertTrue(filename.startswith('--filename='+folder))
            self.assertFalse(any('/dev/' in arg for arg in disk[3]))

    def test_process_timeout_retains_partial_output(self):
        with self.assertRaises(a.WorkloadError) as result:
            a.capture([sys.executable,'-c',"import time; print('partial fixture output',flush=True); time.sleep(3)"],timeout=0.2)
        self.assertIn('timed out',str(result.exception))
        self.assertIn('partial fixture output',result.exception.output)

    def test_consistency_missed_slots_are_not_backfilled(self):
        runner=object.__new__(a.Runner);runner.profile='consistency-24h'
        start=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(hours=24)).isoformat().replace('+00:00','Z')
        runner.report={'startedAt':start,'measurements':[],'artifacts':[],'environment':{'cpu':{'vcpus':2}}}
        missed=[];rounds=[];summary=[]
        runner.skipped=lambda *args:missed.append(args)
        runner.baseline=lambda **kwargs:rounds.append(kwargs['round_index'])
        runner.artifact=lambda *args:'fixture-observation'
        runner.save=lambda:None
        runner.measure=lambda *args,**kwargs:summary.append(args)
        runner.consistency()
        self.assertEqual(len(missed),23)
        self.assertEqual(rounds,[24])
        self.assertEqual(summary[0][5]['actualObservations'],0)
        self.assertEqual(summary[0][5]['attemptedObservations'],1)
        with self.assertRaises(a.WorkloadError):summary[0][4](summary[0][3]())

    def bare_runner(self, folder, profile='smoke'):
        runner=object.__new__(a.Runner)
        runner.out=Path(folder);runner.file=runner.out/'report.json';runner.work=runner.out/'work'
        runner.work.mkdir()
        runner.profile=profile;runner.seconds=2 if profile=='smoke' else 30
        runner.repeats=1 if profile=='smoke' else 3
        runner.disk_bytes=128*1024**2 if profile=='smoke' else 2*1024**3
        runner.network_bytes=8*1024**2 if profile=='smoke' else 64*1024**2
        runner.warmup_seconds=1 if profile=='smoke' else 5
        runner.pg_started=False
        runner.report={'measurements':[],'artifacts':[],'notes':[],
                       'environment':{'cpu':{'vcpus':2},'memoryBytes':2*1024**3}}
        return runner

    def test_successful_retry_is_a_complete_boundary_on_further_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            runner=self.bare_runner(folder)
            with contextlib.redirect_stdout(io.StringIO()):
                runner.measure('cpu','cpu','CPU',lambda:'failure',a.parse_sysbench,{})
                runner.measure('cpu','cpu','CPU',lambda:'events per second: 12.5',a.parse_sysbench,{})
                runner.measure('cpu','cpu','CPU',lambda:(_ for _ in ()).throw(AssertionError('must not rerun')),a.parse_sysbench,{})
            self.assertEqual(len(runner.report['measurements']),2)
            self.assertEqual(runner.report['measurements'][0]['status'],'failed')
            self.assertEqual(runner.report['measurements'][1]['id'],'cpu-retry1')
            self.assertEqual(runner.report['measurements'][1]['status'],'passed')

    def test_parser_failure_keeps_raw_output_and_no_scores(self):
        with tempfile.TemporaryDirectory() as folder:
            runner=self.bare_runner(folder)
            with contextlib.redirect_stdout(io.StringIO()):
                runner.measure('cpu','cpu','CPU',lambda:'malformed raw evidence',a.parse_sysbench,{})
            self.assertEqual(runner.report['measurements'][0]['metrics'],[])
            self.assertEqual(runner.report['artifacts'][0]['content'],'malformed raw evidence')

    def test_missing_database_is_failed_not_skipped_and_run_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            runner=self.bare_runner(folder)
            runner.baseline=lambda:None;runner.network=lambda:None;runner.consistency=lambda:None
            with patch.object(a.Path,'exists',return_value=False):
                result=runner.run()
            self.assertEqual(result['executionStatus'],'failed')
            failure=next(m for m in result['measurements'] if m['id']=='database-setup-failed')
            self.assertEqual(failure['status'],'failed')
            self.assertIn('unavailable',failure['error'])
            self.assertFalse(runner.work.exists())

    def test_subprocess_output_limit_and_missing_binary_are_actionable(self):
        with self.assertRaises(a.WorkloadError) as result:
            a.capture([sys.executable,'-c',"import sys; sys.stdout.write('x'*2000000);sys.stdout.flush()"],timeout=3)
        self.assertIn('Output limit',str(result.exception))
        self.assertLessEqual(len(result.exception.output),a.MAX_ARTIFACT)
        with self.assertRaises(a.WorkloadError) as result:a.capture(['/does/not/exist'])
        self.assertIn('Unable to start',str(result.exception))

    def test_smoke_database_fixed_small_scale_and_pilot_scales_remain_ram_relative(self):
        for profile,expected in [('smoke',[1,2]),('pilot',[25,256])]:
            with tempfile.TemporaryDirectory() as folder:
                runner=self.bare_runner(folder,profile)
                commands=[];measurements=[]
                runner.measure=lambda *args,**kwargs:measurements.append(args)
                def capture(argv,*args,**kwargs):
                    commands.append(argv)
                    if argv[0].endswith('initdb'):
                        (runner.work/'pgdata').mkdir();(runner.work/'pgdata'/'postgresql.conf').touch()
                    return 'fixture setup output'
                with patch.object(a,'capture',side_effect=capture),patch.object(a.Path,'exists',return_value=True):
                    runner.database()
                scales=[int(cmd[cmd.index('-s')+1]) for cmd in commands if '-i' in cmd]
                self.assertEqual(scales,expected)
                self.assertEqual({m[5]['datasetMode'] for m in measurements},{'fixed-small-compatibility' if profile=='smoke' else 'RAM-relative'})
                config=(runner.work/'pgdata'/'postgresql.conf').read_text()
                self.assertIn("listen_addresses = ''",config)
                self.assertIn('fsync = on',config)

    def test_private_targets_require_explicit_flag_and_never_allow_metadata(self):
        for address in ['10.2.3.4','172.18.0.2','192.168.1.10','fd00::2']:
            family=10 if ':' in address else 2
            with patch.object(a.socket,'getaddrinfo',return_value=[(family,1,6,'',(address,5201))]):
                with self.assertRaises(ValueError):a.validate_target('endpoint.example')
                self.assertEqual(a.validate_target('endpoint.example',True),address)
        for address in ['127.0.0.1','169.254.169.254','0.0.0.0','224.0.0.1','::1','fe80::1','ff02::1','::','::ffff:169.254.169.254','2001:db8::1']:
            family=10 if ':' in address else 2
            with patch.object(a.socket,'getaddrinfo',return_value=[(family,1,6,'',(address,5201))]):
                with self.assertRaises(ValueError):a.validate_target('endpoint.example',True)

    def test_consistency_resume_reuses_observations_and_counts_each_boundary_once(self):
        with tempfile.TemporaryDirectory() as folder:
            runner=self.bare_runner(folder,'consistency-24h')
            runner.report['startedAt']=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(hours=24)).isoformat().replace('+00:00','Z')
            runner.baseline=lambda **kwargs:(_ for _ in ()).throw(AssertionError('completed hourly tests must not rerun'))
            for hour in range(1,25):
                runner.artifact(f'consistency-hour-{hour}-observation',json.dumps({'hour':hour,'at':f'original-hour-{hour}','procStatCpuLine':'cpu fixture'}))
                for category,suffix,unit,factor in [('cpu','cpu-2','events/s',1),('memory','memory-read','MiB/s',100),('disk','disk-randread','MB/s',10)]:
                    key=f'r{hour}-n1-{suffix}'
                    if hour==7:
                        runner.report['measurements'].append({'id':key,'category':category,'status':'failed','metrics':[]})
                        key+='-retry1'
                    runner.report['measurements'].append({'id':key,'category':category,'status':'passed','metrics':[a.metric(category+' throughput',hour*factor,unit)]})
            runner.report['measurements'].append({'id':'r0-n1-cpu-2','category':'cpu','status':'passed','metrics':[a.metric('baseline outlier',999999,'events/s')]})
            with contextlib.redirect_stdout(io.StringIO()):runner.consistency()
            summary=next(m for m in runner.report['measurements'] if m['id']=='consistency-summary')
            self.assertEqual(summary['status'],'passed')
            self.assertEqual(summary['config']['actualObservations'],24)
            metrics={m['name']:m for m in summary['metrics']}
            self.assertEqual(metrics['cpu consistency median']['value'],12.5)
            self.assertEqual(metrics['memory consistency median']['value'],1250)
            self.assertEqual(metrics['disk consistency median']['value'],125)
            artifact=next(x for x in runner.report['artifacts'] if x['id']==summary['artifactIds'][0])
            observations=json.loads(artifact['content'])['observations']
            self.assertEqual(len(observations),24)
            self.assertEqual(observations[6]['at'],'original-hour-7')

    def test_cli_version_is_available_without_platform_preflight(self):
        with contextlib.redirect_stdout(io.StringIO()) as output,self.assertRaises(SystemExit) as result:
            a.main(['--version'])
        self.assertEqual(result.exception.code,0)
        self.assertIn('0.2.0',output.getvalue())

    def test_failed_measurement_retains_error_without_zero_metrics(self):
        with tempfile.TemporaryDirectory() as folder:
            runner=object.__new__(a.Runner);runner.out=Path(folder);runner.file=runner.out/'report.json';runner.work=runner.out
            runner.report={'measurements':[],'artifacts':[]}
            runner.seconds=2
            def fail():raise a.WorkloadError('failed command','raw failure evidence')
            runner.measure('test','cpu','Failure fixture',fail,a.parse_sysbench,{})
            measurement=runner.report['measurements'][0]
            self.assertEqual(measurement['status'],'failed')
            self.assertEqual(measurement['metrics'],[])
            self.assertEqual(runner.report['artifacts'][0]['content'],'raw failure evidence')
            self.assertEqual(runner.report['artifacts'][0]['sha256'],hashlib.sha256(b'raw failure evidence').hexdigest())

if __name__=='__main__':unittest.main()
