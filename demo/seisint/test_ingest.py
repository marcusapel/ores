"""Quick test: try to ingest 1 RDDMS-built record via Storage API and Workflow."""
import httpx, json, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from _shared import load_env

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
env = load_env([os.path.join(_root, '.env')])
tok = httpx.post(
    f'https://login.microsoftonline.com/{env["tenant"]}/oauth2/v2.0/token',
    data={'grant_type':'refresh_token','client_id':env['client_id'],
          'refresh_token':env['refresh_token'],'scope':env['scope']},
    timeout=30,
).json()['access_token']

base = f'https://{env["host"].replace("https://","")}'
part = env['partition']
h = {'Authorization': f'Bearer {tok}', 'data-partition-id': part, 'Content-Type': 'application/json'}

manifest_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'manifest_rddms_drogon.json')
with open(manifest_path) as f:
    manifest = json.load(f)

wpcs = manifest['Data']['WorkProductComponents']
print(f"Manifest has {len(wpcs)} WPCs")

# ── 1. Storage API ──
rec = wpcs[0]
print(f"\n1) Storage API PUT /api/storage/v2/records (1 record)")
print(f"   kind: {rec['kind']}")
print(f"   id:   {rec['id']}")
r1 = httpx.put(f'{base}/api/storage/v2/records', headers=h, json=[rec], timeout=60)
print(f"   → {r1.status_code} {r1.text[:300]}")

# ── 2. Workflow Osdu_ingest ──
print(f"\n2) Workflow PUT Osdu_ingest (full manifest)")
body = {'executionContext': {'Payload': {'data-partition-id': part}, 'manifest': manifest}}
r2 = httpx.post(f'{base}/api/workflow/v1/workflow/Osdu_ingest/workflowRun', headers=h, json=body, timeout=60)
print(f"   → {r2.status_code} {r2.text[:300]}")

# ── 3. Check if there's a manifest-based ingestion endpoint ──
print(f"\n3) POST /api/storage/v2/query/records (search existing)")
search_body = {"ids": [rec['id']]}
r3 = httpx.post(f'{base}/api/storage/v2/query/records', headers=h, json=search_body, timeout=30)
print(f"   → {r3.status_code} {r3.text[:300]}")
