#!/usr/bin/env bash
# Human-approved, local encrypted PostgreSQL backup. Does not approve teardown.
set -euo pipefail
umask 077
cd "$(dirname "$(realpath "$0")")/../.."
exec python3 - "$@" <<'PY'
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ACCOUNT = '275544662748'
REGION = 'eu-west-1'
CONTEXT = 'eks-reliability-lab'
CLUSTER = 'eks-platform-reliability-lab'
SECRET = CLUSTER + '/dev/app-runtime'
TF_DIR = 'infra/terraform/environments/lab'
POD = 'postgres-backup-client'
BACKUP_DIR = Path.home() / '.local' / 'share' / 'eks-lab-backups' / 'postgres'
IDENTITY = Path.home() / '.config' / 'eks-lab' / 'age-backup-key.txt'

def cmd(args, *, data=None, timeout=120):
    return subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=timeout, check=True).stdout

def text(args):
    return cmd(args).decode().strip()

def fail(message):
    raise RuntimeError(message)

if len(sys.argv) != 2 or sys.argv[1] not in ('--preflight', '--execute'):
    print('Usage: bash scripts/lifecycle/lab-db-backup.sh --preflight|--execute', file=sys.stderr)
    sys.exit(2)

mode = sys.argv[1]
try:
    for tool in ('aws', 'terraform', 'kubectl', 'age', 'age-keygen', 'pg_restore'):
        if not __import__('shutil').which(tool):
            fail('Missing required tool: ' + tool)
    if os.environ.get('AWS_REGION', REGION) != REGION:
        fail('AWS_REGION mismatch')
    if text(['aws','sts','get-caller-identity','--query','Account','--output','text']) != ACCOUNT:
        fail('Wrong AWS account')
    if text(['kubectl','config','current-context']) != CONTEXT:
        fail('Wrong Kubernetes context')
    cluster_arn = text(['terraform',f'-chdir={TF_DIR}','output','-raw','cluster_arn'])
    if cluster_arn != f'arn:aws:eks:{REGION}:{ACCOUNT}:cluster/{CLUSTER}':
        fail('Terraform cluster ARN mismatch')
    if text(['aws','eks','describe-cluster','--name',CLUSTER,'--query','cluster.status','--output','text']) != 'ACTIVE':
        fail('EKS not ACTIVE')
    host = text(['terraform',f'-chdir={TF_DIR}','output','-raw','rds_address'])
    db = text(['terraform',f'-chdir={TF_DIR}','output','-raw','rds_database_name'])
    master_arn = text(['terraform',f'-chdir={TF_DIR}','output','-raw','rds_master_secret_arn'])
    if not re.fullmatch(r'[A-Za-z0-9_]+', db) or not re.fullmatch(r'[A-Za-z0-9.-]+', host):
        fail('Unexpected database identifier')
    if text(['aws','rds','describe-db-instances','--db-instance-identifier',CLUSTER+'-postgres','--query','DBInstances[0].DBInstanceStatus','--output','text']) != 'available':
        fail('RDS unavailable')
    if not IDENTITY.is_file() or IDENTITY.is_symlink():
        fail(f'Missing age identity file: {IDENTITY}; set up key first')
    if IDENTITY.stat().st_mode & 0o077:
        fail('age identity permissions must be 0600')
    recipient = text(['age-keygen','-y',str(IDENTITY)])
    if not recipient.startswith('age1'):
        fail('Invalid age recipient')
    if mode == '--preflight':
        print('PASS: Identity, infrastructure, age key and tools checked. No backup created.')
        print('NOTE: The execute mode creates and removes one temporary pod in dev.')
        sys.exit(0)
    if os.environ.get('LAB_DB_BACKUP_APPROVED') != 'YES':
        fail('Explicit approval required: LAB_DB_BACKUP_APPROVED=YES')
    # Refuse to interfere with an existing pod of the same name.
    existing = subprocess.run(['kubectl','-n','dev','get','pod',POD,'-o','name'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if existing.returncode == 0:
        fail('Backup pod name already exists; investigate manually')
    master = json.loads(text(['aws','secretsmanager','get-secret-value','--secret-id',master_arn,'--query','SecretString','--output','text']))
    username, password = master['username'], master['password']
    if any('\n' in x or '\r' in x for x in (username,password)):
        fail('Unexpected newline in credential')
    manifest = {'apiVersion':'v1','kind':'Pod','metadata':{'name':POD,'namespace':'dev','labels':{'app':'postgres-backup-client'}},
        'spec':{'restartPolicy':'Never','automountServiceAccountToken':False,
          'securityContext':{'runAsNonRoot':True,'runAsUser':65534,'runAsGroup':65534,'seccompProfile':{'type':'RuntimeDefault'}},
          'containers':[{'name':'pg','image':'postgres:16.15-alpine','command':['sh','-c','sleep 1800'],
            'securityContext':{'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':True,'capabilities':{'drop':['ALL']}},
            'resources':{'requests':{'cpu':'25m','memory':'32Mi'},'limits':{'cpu':'200m','memory':'256Mi'}}}]}}
    created = False
    try:
        cmd(['kubectl','apply','-f','-'],data=json.dumps(manifest).encode())
        created = True
        cmd(['kubectl','-n','dev','wait','--for=condition=Ready','pod/'+POD,'--timeout=180s'],timeout=210)
        # Password is passed via stdin, not command arguments, pod spec, or logs.
        script = 'IFS= read -r PGPASSWORD; export PGPASSWORD PGSSLMODE=require; exec pg_dump -h "$1" -p 5432 -U "$2" -d "$3" -Fc --no-owner --no-acl'
        dump = cmd(['kubectl','-n','dev','exec','-i',POD,'--','sh','-c',script,'backup',host,username,db],data=(password+'\n').encode(),timeout=900)
        if not dump.startswith(b'PGDMP'):
            fail('Invalid pg_dump custom archive header')
        encrypted = cmd(['age','-r',recipient],data=dump,timeout=180)
        # Verify decryptability and archive structure without writing plaintext to disk.
        restored = cmd(['age','-d','-i',str(IDENTITY)],data=encrypted,timeout=180)
        if restored != dump:
            fail('Encrypted backup round-trip mismatch')
        cmd(['pg_restore','--list'],data=restored,timeout=180)
        BACKUP_DIR.mkdir(parents=True,exist_ok=True,mode=0o700)
        if BACKUP_DIR.is_symlink():
            fail('Backup directory must not be a symlink')
        timestamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        target = BACKUP_DIR / f'{CLUSTER}-{db}-{timestamp}.dump.age'
        with target.open('xb') as out:
            out.write(encrypted)
        os.chmod(target,0o600)
        digest = hashlib.sha256(encrypted).hexdigest()
        metadata = {'timestamp_utc':timestamp,'account_id':ACCOUNT,'cluster_name':CLUSTER,'database':db,
            'encrypted_file':str(target),'sha256_encrypted':digest,'encrypted_bytes':len(encrypted),
            'format':'pg_dump custom / age','archive_verified':True,'restore_tested_to_database':False,
            'safe_to_destroy':False}
        sidecar = target.with_suffix(target.suffix+'.json')
        sidecar.write_text(json.dumps(metadata,indent=2)+'\n')
        os.chmod(sidecar,0o600)
        print('PASS: Encrypted PostgreSQL backup created and archive verified')
        print('File:',target)
        print('SHA256:',digest)
        print('NOTE: Full database restore test not performed; teardown not authorised.')
    finally:
        if created:
            cleanup = subprocess.run(['kubectl','-n','dev','delete','pod',POD,'--wait=true','--timeout=120s'],capture_output=True,text=True)
            if cleanup.returncode != 0:
                print('WARNING: Backup pod cleanup failed; investigate before teardown.',file=sys.stderr)
except (RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired, KeyError, ValueError, OSError) as exc:
    # Do not emit raw command stderr, which might contain sensitive connection data.
    print('BLOCKED: Backup failed. Check prerequisites and private diagnostics; no teardown approval.',file=sys.stderr)
    if isinstance(exc, RuntimeError):
        print(str(exc),file=sys.stderr)
    sys.exit(1)
PY
