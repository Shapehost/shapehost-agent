#!/usr/bin/env python3
"""Verify reproducibility, archive integrity, source provenance and real CLI entrypoints."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import zipfile
ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='shapehost-build-') as directory:
    output=Path(directory)
    command=[sys.executable,str(ROOT/'scripts/build-agent.py'),'--output',directory]
    subprocess.run(command,check=True,capture_output=True)
    initial={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir()}
    subprocess.run(command,check=True,capture_output=True)
    assert initial=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir()},'Non-reproducible build'
    manifest=json.loads((output/'agent-release.json').read_text())
    version=manifest['version'];executable=output/f'shapehost-agent-{version}.pyz'
    for artifact in manifest['artifacts']:
        assert initial[artifact['file']]==artifact['sha256']
    with tarfile.open(output/f'shapehost-agent-{version}.tar.gz') as tar:
        entries=tar.extractfile('agent/SHA256SUMS').read().decode().splitlines()
        for line in entries:
            expected,name=line.split('  ',1)
            assert name and not Path(name).is_absolute() and '..' not in Path(name).parts
            assert hashlib.sha256(tar.extractfile('agent/'+name).read()).hexdigest()==expected,name
        assert tar.getmember('agent/install.sh').mode==0o755
        assert tar.extractfile('agent/bin/shapehost-agent.pyz').read()==executable.read_bytes()
    result=subprocess.run([sys.executable,str(executable),'--version'],capture_output=True,text=True,check=True)
    assert version in result.stdout
    dry=subprocess.run([sys.executable,str(executable),'run','--dry-run'],capture_output=True,text=True,check=True)
    assert json.loads(dry.stdout)['profile']=='pilot'
    fixture=output/'inspect-fixture.json'
    fixture.write_text(json.dumps({'runId':'test-only','methodology':{'profile':'fixture'},'executionStatus':'completed','measurements':[]}))
    inspected=subprocess.run([sys.executable,str(executable),'inspect',str(fixture)],capture_output=True,text=True,check=True)
    assert json.loads(inspected.stdout)['profile']=='fixture'
    with zipfile.ZipFile(executable) as app:
        assert manifest['source'] in app.read('build_info.py').decode()
        assert 'report.schema.json' in app.namelist()
    print(json.dumps({'reproducibleBuild':True,'archiveChecksums':len(entries),'versionCLI':version,'dryRun':'passed','inspectCLI':'passed','embeddedProvenance':True}))
