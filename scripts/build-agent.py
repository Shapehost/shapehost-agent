#!/usr/bin/env python3
"""Build a reproducible source archive and executable Python zip application."""
import ast
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import subprocess
import tarfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT
OUTPUT=ROOT/'dist'

def build(output=None):
    global OUTPUT
    if output is not None:OUTPUT=Path(output)
    OUTPUT.mkdir(parents=True,exist_ok=True)
    module=ast.parse((SOURCE/'shapehost_agent.py').read_text())
    version=next(ast.literal_eval(n.value) for n in module.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='VERSION' for t in n.targets))
    # Package only tracked files from this standalone Git checkout.
    tracked=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0')
    contents={name:(ROOT/name).read_bytes() for name in sorted(tracked) if name and name not in ['SHA256SUMS','SOURCE_COMMIT']}
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    digest=hashlib.sha256()
    for name,data in sorted(contents.items()):digest.update((name+'\0').encode());digest.update(data)
    provenance=revision+'+source.'+digest.hexdigest()[:24]
    contents['SOURCE_COMMIT']=(provenance+'\n').encode()
    (ROOT/'SOURCE_COMMIT').write_text(provenance+'\n')
    (ROOT/'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(data).hexdigest()}  {name}\n' for name,data in sorted(contents.items())))
    entry=b'''import sys
import subprocess\nimport shapehost_agent as agent\nfrom build_info import SOURCE_COMMIT\nagent.source_commit=lambda: SOURCE_COMMIT\ntry:\n    sys.exit(agent.main())\nexcept (ValueError,RuntimeError,OSError) as error:\n    print('Error: '+str(error),file=sys.stderr)\n    sys.exit(1)\nexcept KeyboardInterrupt:\n    print('Interrupted; local evidence retained.',file=sys.stderr)\n    sys.exit(130)\n'''
    archive=io.BytesIO()
    archive.write(b'#!/usr/bin/env python3\n')
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as app:
        for name,data in sorted({'__main__.py':entry,'shapehost_agent.py':contents['shapehost_agent.py'],'build_info.py':f'SOURCE_COMMIT={provenance!r}\n'.encode(),'LICENSE':contents['LICENSE'],'report.schema.json':contents['report.schema.json']}.items()):
            info=zipfile.ZipInfo(name,(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
            app.writestr(info,data)
    executable=archive.getvalue()
    app_name=f'shapehost-agent-{version}.pyz'
    (OUTPUT/app_name).write_bytes(executable);(OUTPUT/app_name).chmod(0o755)
    contents['bin/shapehost-agent.pyz']=executable
    contents['SHA256SUMS']=''.join(f'{hashlib.sha256(data).hexdigest()}  {name}\n' for name,data in sorted(contents.items())).encode()
    payload=io.BytesIO()
    with tarfile.open(fileobj=payload,mode='w',format=tarfile.PAX_FORMAT) as tar:
        for name,data in sorted(contents.items()):
            info=tarfile.TarInfo('agent/'+name);info.size=len(data);info.mtime=0;info.uid=info.gid=0;info.uname=info.gname='';info.mode=0o755 if name in ['install.sh','shapehost_agent.py','bin/shapehost-agent.pyz'] else 0o644
            tar.addfile(info,io.BytesIO(data))
    bundle=f'shapehost-agent-{version}.tar.gz'
    (OUTPUT/bundle).write_bytes(gzip.compress(payload.getvalue(),compresslevel=9,mtime=0))
    artifacts=[]
    for name in [app_name,bundle]:
        file=OUTPUT/name;sha=hashlib.sha256(file.read_bytes()).hexdigest()
        (OUTPUT/(name+'.sha256')).write_text(f'{sha}  {name}\n')
        artifacts.append({'file':name,'sha256':sha,'bytes':file.stat().st_size})
    manifest={'version':version,'source':provenance,'runtime':'Python 3.12 on Ubuntu 24.04','architectures':['x86_64','aarch64'],'artifacts':artifacts}
    (OUTPUT/'agent-release.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest,indent=2))
if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    build(parser.parse_args().output)
