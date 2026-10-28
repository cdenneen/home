import subprocess, json, hashlib, os, time, glob
from datetime import datetime, timezone, timedelta

START_URL = "https://d-9067f453fb.awsapps.com/start#"
REGION = "us-east-1"

reg = json.loads(subprocess.run([
    'aws', 'sso-oidc', 'register-client',
    '--client-name', 'nyx-sso-login', '--client-type', 'public', '--region', REGION
], capture_output=True, text=True).stdout)

auth = json.loads(subprocess.run([
    'aws', 'sso-oidc', 'start-device-authorization',
    '--client-id', reg['clientId'], '--client-secret', reg['clientSecret'],
    '--start-url', START_URL, '--region', REGION
], capture_output=True, text=True).stdout)

url = auth.get('verificationUriComplete',
               f"https://d-9067f453fb.awsapps.com/start/#/device?user_code={auth['userCode']}")
print(f"APPROVE: {url}", flush=True)

for _ in range(60):
    time.sleep(auth.get('interval', 5))
    r = subprocess.run([
        'aws', 'sso-oidc', 'create-token',
        '--client-id', reg['clientId'], '--client-secret', reg['clientSecret'],
        '--grant-type', 'urn:ietf:params:oauth:grant-type:device_code',
        '--device-code', auth['deviceCode'], '--region', REGION
    ], capture_output=True, text=True)
    if r.returncode == 0:
        d = json.loads(r.stdout)
        access_token = d['accessToken']
        expires_at = (datetime.now(timezone.utc) +
                      timedelta(seconds=d.get('expiresIn', 28800))).strftime('%Y-%m-%dT%H:%M:%SZ')

        cache_dir = os.path.expanduser('~/.aws/sso/cache')
        os.makedirs(cache_dir, exist_ok=True)

        # 1. Update any existing cache files that have refresh tokens (preserves client creds)
        updated = []
        for p in glob.glob(os.path.join(cache_dir, '*.json')):
            try:
                with open(p) as f:
                    existing = json.load(f)
                if 'refreshToken' in existing or existing.get('startUrl') == START_URL:
                    existing['accessToken'] = access_token
                    existing['expiresAt'] = expires_at
                    with open(p, 'w') as f:
                        json.dump(existing, f)
                    updated.append(os.path.basename(p))
            except Exception:
                pass

        # 2. Write new cache files for both key formats (covers all profile types)
        for key, data in [
            (json.dumps({'startUrl': START_URL, 'region': REGION}, sort_keys=True),
             {'startUrl': START_URL, 'region': REGION, 'accessToken': access_token, 'expiresAt': expires_at}),
            (json.dumps({'sessionName': 'cdenneen', 'startUrl': START_URL, 'region': REGION}, sort_keys=True),
             {'sessionName': 'cdenneen', 'startUrl': START_URL, 'region': REGION,
              'accessToken': access_token, 'expiresAt': expires_at}),
        ]:
            path = os.path.join(cache_dir, f"{hashlib.sha1(key.encode()).hexdigest()}.json")
            if os.path.basename(path) not in updated:
                with open(path, 'w') as f:
                    json.dump(data, f)

        print(f"Token written. Expires: {expires_at}")
        break
    elif 'AuthorizationPendingException' not in r.stderr:
        print(f"Error: {r.stderr[:100]}")
        break
