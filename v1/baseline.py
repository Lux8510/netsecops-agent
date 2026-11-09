"""Bounded, credential-free live baseline collection. No model calls."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / 'v1/lab/topology.clab.yml'
RESULTS = ROOT / 'v1/results'
PREFIX = 'clab-netsecops-v1-http-'

def command(args: list[str], timeout: int = 20) -> dict:
    started = datetime.now(timezone.utc).isoformat()
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return dict(command=args, collected_at=started, exit_code=p.returncode, stdout=p.stdout, stderr=p.stderr)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return dict(command=args, collected_at=started, exit_code=None, stdout='', stderr=str(exc))

def collect() -> dict:
    topology = json.loads((ROOT / 'v1/lab/topology.json').read_text())
    evidence: list[dict] = []
    checks: list[dict] = []
    def observe(node, args):
        item = command(['docker', 'exec', PREFIX + node, *args])
        item.update(evidence_id=f'E{len(evidence)+1:03}', node=node)
        evidence.append(item)
        return item
    def check(name, items, passed):
        checks.append(dict(name=name, status=('collection_failure' if any(i['exit_code'] != 0 for i in items) else 'pass' if passed else 'fail'), evidence_ids=[i['evidence_id'] for i in items]))
    for node in topology['nodes']:
        item = command(['docker', 'inspect', PREFIX + node, '--format', '{{.State.Running}}'])
        item.update(evidence_id=f'E{len(evidence)+1:03}', node=node)
        evidence.append(item)
        check(f'{node} running', [item], item['stdout'].strip() == 'true')
    neighbors = {}
    for node in ('r1','r2','r3','r4'):
        neighbors[node] = observe(node, ['vtysh','-c','show ip ospf neighbor'])
    for link in topology['links']:
        if 'ospf_cost' not in link:
            continue
        for a,b in (link['endpoints'], list(reversed(link['endpoints']))):
            item = neighbors[a['node']]
            rid = topology['nodes'][b['node']]['router_id']
            check(f"{link['name']} {a['node']} neighbor FULL", [item], any(line.split() and line.split()[0] == rid and 'Full/' in line for line in item['stdout'].splitlines()))
    for node, address, hop in [('r1','10.104.0.2','10.12.0.2'),('r2','10.104.0.2','10.24.0.2'),('r4','10.101.0.2','10.24.0.1'),('r2','10.101.0.2','10.12.0.1')]:
        item = observe(node, ['ip','route','get',address])
        check(f'{node} route to {address} via {hop}', [item], f'via {hop} ' in item['stdout'])
    for node, address, hop in [('client','10.104.0.2','10.101.0.1'),('server','10.101.0.2','10.104.0.1')]:
        item = observe(node, ['ip','route','get',address])
        check(f'{node} data route', [item], f'via {hop} dev eth1' in item['stdout'])
        item = observe(node, ['ping','-c','3','-W','2',address])
        check(f'{node} data ping', [item], '3 packets received' in item['stdout'] or '3 received' in item['stdout'])
    item = observe('client',['python3','-c','import urllib.request; r=urllib.request.urlopen("http://10.104.0.2:8080/health",timeout=5); b=r.read(); print(r.status,repr(b)); assert r.status==200 and b==b"ok\\n"'])
    check('HTTP 200 /health body ok', [item], item['exit_code'] == 0)
    passed = all(c['status'] == 'pass' for c in checks)
    return dict(schema_version=1, run_id=uuid.uuid4().hex, collected_at=datetime.now(timezone.utc).isoformat(), mode='live-read-only', status='pass' if passed else 'collection_failure' if any(c['status']=='collection_failure' for c in checks) else 'fail', topology=topology, checks=checks, evidence=evidence, actual_primary_path=['client','r1','r2','r4','server'] if all(c['status']=='pass' for c in checks if 'route' in c['name']) else None)

def save(result):
    RESULTS.mkdir(exist_ok=True)
    data = json.dumps(result, ensure_ascii=False, indent=2)
    (RESULTS / (result['run_id']+'.json')).write_text(data)
    temp = RESULTS / 'latest.tmp'
    temp.write_text(data)
    temp.replace(RESULTS / 'latest.json')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['check','start','stop','status','workbench'])
    args = parser.parse_args()
    if args.action == 'status':
        result = command(['docker','ps','-a','--filter','name='+PREFIX])
        print(json.dumps(result, indent=2)); return 0 if result['exit_code']==0 else 1
    if args.action in ('start','stop','workbench'):
        for node in ('client','r1','r2','r3','r4','server'):
            existing = command(['docker','inspect',PREFIX+node])
            if existing['exit_code'] == 0:
                info = json.loads(existing['stdout'])[0]
                if info['Config'].get('Labels',{}).get('containerlab') != 'netsecops-v1-http':
                    print('Refusing unrelated container name collision: '+PREFIX+node)
                    return 1
        # Containerlab reconciles existing resources; partial labs are not deleted.
        result = command(['containerlab','destroy' if args.action=='stop' else 'deploy','-t',str(LAB)],120)
        print(json.dumps(result, indent=2))
        if result['exit_code'] != 0: return 1
        if args.action == 'stop': return 0
    deadline = time.monotonic() + (90 if args.action in ('start','workbench') else 0)
    while True:
        result = collect(); save(result)
        if result['status']=='pass' or time.monotonic() >= deadline: break
        time.sleep(2)
    print(json.dumps({k: result[k] for k in ('run_id','status','checks')},ensure_ascii=False,indent=2))
    if result['status'] != 'pass': return 1
    if args.action == 'workbench':
        # A foreground process keeps this WSL session alive while the lab is used.
        return subprocess.call(['npm','run','dev','--','--strictPort'], cwd=ROOT/'web')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
