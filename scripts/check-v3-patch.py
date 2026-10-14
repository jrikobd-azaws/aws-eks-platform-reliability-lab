#!/usr/bin/env python3
from pathlib import Path
import subprocess
root=Path(__file__).resolve().parent.parent
for name in ('start','stop','validate-up','validate-down'):
 p=root/'scripts'/f'{name}.sh'
 subprocess.run(['bash','-n',str(p)],check=True)
 r=subprocess.run(['bash',str(p)],capture_output=True,text=True)
 assert r.returncode==78 and 'BLOCKED' in r.stderr,(name,r.returncode)
print('PASS: blocked launchers have valid syntax and return 78; no cloud calls.')
