#!/usr/bin/env python3
"""Interactive Hive authentication setup.

Run once on the Joggler to obtain and save long-lived tokens:
    python3 /home/of/hive-setup.py

Or run on Mac and SCP tokens to Joggler:
    python3 hive-setup.py --token-file ./hive-tokens.json
    scp hive-tokens.json of@172.16.10.168:/home/of/hive-tokens.json && rm hive-tokens.json

The saved hive-tokens.json is read by transport-proxy.py at /api/hive.
"""

import argparse
import base64
import binascii
import datetime
import hashlib
import hmac
import json
import os
import re
import sys
import time

import requests

TOKEN_FILE = '/home/gduthie/twyford-dashboard/hive-tokens.json'
SSO_URL    = 'https://sso.hivehome.com/'
API_BASE   = 'https://beekeeper-uk.hivehome.com/1.0'

# ── SRP constants ─────────────────────────────────────────────────────────────
# From https://github.com/aws/amazon-cognito-identity-js
N_HEX = (
    'FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD1'
    '29024E088A67CC74020BBEA63B139B22514A08798E3404DD'
    'EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245'
    'E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED'
    'EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3D'
    'C2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F'
    '83655D23DCA3AD961C62F356208552BB9ED529077096966D'
    '670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B'
    'E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9'
    'DE2BCBF6955817183995497CEA956AE515D2261898FA0510'
    '15728E5A8AAAC42DAD33170D04507A33A85521ABDF1CBA64'
    'ECFB850458DBEF0A8AEA71575D060C7DB3970F85A6E1E4C7'
    'ABF5AE8CDB0933D71E8C94E04A25619DCEE3D2261AD2EE6B'
    'F12FFA06D98A0864D87602733EC86A64521F2B18177B200C'
    'BBE117577A615D6C770988C0BAD946E208E24FA074E5AB31'
    '43DB5BFCE0FD108E4B82D120A93AD2CAFFFFFFFFFFFFFFFF'
)
G_HEX     = '2'
INFO_BITS = bytearray('Caldera Derived Key', 'utf-8')


# ── SRP helpers ───────────────────────────────────────────────────────────────

def _hex_to_long(h):
    return int(h, 16)

def _long_to_hex(n):
    return f'{n:x}'

def _get_random(nbytes):
    return _hex_to_long(binascii.hexlify(os.urandom(nbytes)).decode())

def _hash_sha256(buf):
    a = hashlib.sha256(buf).hexdigest()
    return (64 - len(a)) * '0' + a

def _hex_hash(h):
    return _hash_sha256(bytearray.fromhex(h))

def _pad_hex(n):
    h = _long_to_hex(n) if not isinstance(n, str) else n
    if len(h) % 2 == 1:
        return '0' + h
    if h[0] in '89ABCDEFabcdef':
        return '00' + h
    return h

def _calc_u(a, b):
    return _hex_to_long(_hex_hash(_pad_hex(a) + _pad_hex(b)))

def _compute_hkdf(ikm, salt):
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, INFO_BITS + bytearray(chr(1), 'utf-8'), hashlib.sha256).digest()[:16]


# ── Hive SRP auth class ───────────────────────────────────────────────────────

class HiveSRPAuth:

    def __init__(self, username, password):
        self.username  = username
        self.password  = password
        self.pool_id   = None
        self.client_id = None
        self.region    = None
        self.user_id   = None

        big_n         = _hex_to_long(N_HEX)
        g             = _hex_to_long(G_HEX)
        self._k       = _hex_to_long(_hex_hash('00' + N_HEX + '0' + G_HEX))
        self._big_n   = big_n
        self._g       = g
        self._small_a = _get_random(128) % big_n
        self._large_a = pow(g, self._small_a, big_n)

    def fetch_config(self):
        resp = requests.get(SSO_URL, timeout=10)
        resp.raise_for_status()
        html = resp.text
        pool   = re.search(r'HiveSSOPoolId="([^"]+)"', html)
        client = re.search(r'HiveSSOPublicCognitoClientId="([^"]+)"', html)
        if not pool or not client:
            raise RuntimeError('Cannot extract Cognito config from sso.hivehome.com')
        self.pool_id   = pool.group(1)       # e.g. eu-west-1_SamNfoWtf
        self.client_id = client.group(1)
        self.region    = self.pool_id.split('_')[0]  # eu-west-1

    def _cognito(self, target, body):
        url = f'https://cognito-idp.{self.region}.amazonaws.com/'
        resp = requests.post(url, data=json.dumps(body), headers={
            'X-Amz-Target': f'AWSCognitoIdentityProviderService.{target}',
            'Content-Type': 'application/x-amz-json-1.1',
        }, timeout=15)
        if not resp.ok:
            raise RuntimeError(f'Cognito {target} error {resp.status_code}: {resp.text[:300]}')
        return resp.json()

    def _password_key(self, user_id, srp_b_hex, salt_hex):
        server_b  = _hex_to_long(srp_b_hex)
        u         = _calc_u(self._large_a, server_b)
        if u == 0:
            raise ValueError('U cannot be zero')
        pool_name = self.pool_id.split('_')[1]
        pw_hash   = _hash_sha256(f'{pool_name}{user_id}:{self.password}'.encode())
        x         = _hex_to_long(_hex_hash(_pad_hex(int(salt_hex, 16)) + pw_hash))
        g_mod     = pow(self._g, x, self._big_n)
        s         = pow(server_b - self._k * g_mod, self._small_a + u * x, self._big_n)
        return _compute_hkdf(
            bytearray.fromhex(_pad_hex(s)),
            bytearray.fromhex(_pad_hex(_long_to_hex(u))),
        )

    def _process_challenge(self, params):
        self.user_id = params['USER_ID_FOR_SRP']
        hkdf         = self._password_key(self.user_id, params['SRP_B'], params['SALT'])
        secret_bytes = base64.standard_b64decode(params['SECRET_BLOCK'])
        timestamp    = re.sub(r' 0(\d) ', r' \1 ',
            datetime.datetime.now(datetime.timezone.utc).strftime('%a %b %d %H:%M:%S UTC %Y'))
        pool_name    = self.pool_id.split('_')[1]
        msg = (bytearray(pool_name, 'utf-8')
               + bytearray(self.user_id, 'utf-8')
               + bytearray(secret_bytes)
               + bytearray(timestamp, 'utf-8'))
        sig = base64.standard_b64encode(hmac.new(hkdf, msg, hashlib.sha256).digest()).decode()
        return {
            'TIMESTAMP': timestamp,
            'USERNAME': self.user_id,
            'PASSWORD_CLAIM_SECRET_BLOCK': params['SECRET_BLOCK'],
            'PASSWORD_CLAIM_SIGNATURE': sig,
        }

    def initiate(self):
        """Step 1: USER_SRP_AUTH → PASSWORD_VERIFIER challenge."""
        return self._cognito('InitiateAuth', {
            'AuthFlow': 'USER_SRP_AUTH',
            'AuthParameters': {
                'USERNAME': self.username,
                'SRP_A': _long_to_hex(self._large_a),
            },
            'ClientId': self.client_id,
        })

    def verify_password(self, challenge_params):
        """Step 2: respond to PASSWORD_VERIFIER → tokens or SMS_MFA."""
        resp = self._process_challenge(challenge_params)
        return self._cognito('RespondToAuthChallenge', {
            'ClientId': self.client_id,
            'ChallengeName': 'PASSWORD_VERIFIER',
            'ChallengeResponses': resp,
        })

    def verify_sms(self, session, code):
        """Step 3 (if required): respond to SMS_MFA challenge → tokens."""
        return self._cognito('RespondToAuthChallenge', {
            'ClientId': self.client_id,
            'ChallengeName': 'SMS_MFA',
            'Session': session,
            'ChallengeResponses': {
                'SMS_MFA_CODE': str(code).strip(),
                'USERNAME': self.user_id,
            },
        })


def _save_tokens(path, auth_result, pool_id, client_id, region):
    tokens = {
        'pool_id':       pool_id,
        'client_id':     client_id,
        'region':        region,
        'IdToken':       auth_result['IdToken'],
        'AccessToken':   auth_result['AccessToken'],
        'RefreshToken':  auth_result.get('RefreshToken', ''),
        'token_expiry':  time.time() + auth_result.get('ExpiresIn', 3600) - 60,
    }
    with open(path, 'w') as f:
        json.dump(tokens, f, indent=2)
    os.chmod(path, 0o600)
    return tokens


def _discover_home(id_token):
    """Find which home has heating products; return (home_id, sensors) or (None, [])."""
    resp = requests.get(
        f'{API_BASE}/nodes/all?products=true',
        headers={'authorization': id_token, 'content-type': 'application/json'},
        timeout=15,
    )
    if not resp.ok:
        return None, []
    data = resp.json()
    # Check top-level home first
    heating = [p for p in data.get('products', [])
               if p.get('type') == 'heating' and (p.get('props') or {}).get('temperature') is not None]
    if heating:
        return None, heating  # top-level — no home_id needed

    # Try each home in the homes list
    for h in (data.get('homes') or {}).get('homes', []):
        hid = h['id']
        r2 = requests.get(
            f'{API_BASE}/nodes/all?products=true&homeId={hid}',
            headers={'authorization': id_token, 'content-type': 'application/json'},
            timeout=15,
        )
        if not r2.ok:
            continue
        d2 = r2.json()
        heating2 = [p for p in d2.get('products', [])
                    if p.get('type') == 'heating' and (p.get('props') or {}).get('temperature') is not None]
        if heating2:
            return hid, heating2
    return None, []


def _test_api(id_token, token_path):
    home_id, heating = _discover_home(id_token)
    if not heating:
        print('No heating products with temperature found.')
        print('Querying top-level to see all product types:')
        resp = requests.get(f'{API_BASE}/nodes/all?products=true',
                            headers={'authorization': id_token, 'content-type': 'application/json'}, timeout=15)
        if resp.ok:
            types = {p.get('type') for p in resp.json().get('products', [])}
            print('  Types:', types or '(none)')
        return

    print(f'Found {len(heating)} heating zone(s)' + (f' in home {home_id}' if home_id else '') + ':')
    for p in heating:
        name = (p.get('state') or {}).get('name') or p.get('id', '?')
        temp = round(float((p.get('props') or {})['temperature']), 1)
        print(f'  {name}: {temp}°C')

    if home_id:
        with open(token_path) as f:
            tokens = json.load(f)
        tokens['home_id'] = home_id
        with open(token_path, 'w') as f:
            json.dump(tokens, f, indent=2)
        os.chmod(token_path, 0o600)
        print(f'home_id {home_id} saved to {token_path}')


CREDS_FILE = '/home/gduthie/twyford-dashboard/hive-credentials.json'


def main():
    parser = argparse.ArgumentParser(description='Hive first-time auth setup')
    parser.add_argument('--token-file', default=TOKEN_FILE,
                        help=f'Where to save tokens (default: {TOKEN_FILE})')
    parser.add_argument('--credentials-file', default=None,
                        help='JSON file with {"username":"…","password":"…"} for unattended re-auth')
    parser.add_argument('--save-credentials', action='store_true',
                        help=f'Save username+password to {CREDS_FILE} after successful login')
    parser.add_argument('--username', help='Hive account email')
    parser.add_argument('--password', help='Hive account password')
    args = parser.parse_args()

    if args.credentials_file:
        with open(args.credentials_file) as f:
            creds = json.load(f)
        username = creds['username']
        password = creds['password']
    else:
        username = args.username or input('Hive email: ').strip()
        password = args.password or input('Hive password: ').strip()

    auth = HiveSRPAuth(username, password)

    print('Fetching Cognito config from sso.hivehome.com...')
    auth.fetch_config()
    print(f'Pool: {auth.pool_id}  Client: {auth.client_id}  Region: {auth.region}')

    print('Initiating SRP auth...')
    result = auth.initiate()

    challenge = result.get('ChallengeName')
    if challenge != 'PASSWORD_VERIFIER':
        print(f'Unexpected challenge after InitiateAuth: {challenge}')
        print(json.dumps(result, indent=2))
        sys.exit(1)

    print('Verifying password...')
    result = auth.verify_password(result['ChallengeParameters'])
    challenge = result.get('ChallengeName')

    if challenge == 'SMS_MFA':
        print('SMS 2FA required — check your phone.')
        code   = input('Enter SMS code: ').strip()
        result = auth.verify_sms(result.get('Session'), code)
        challenge = result.get('ChallengeName')

    if 'AuthenticationResult' not in result:
        print('Login failed:')
        print(json.dumps(result, indent=2))
        sys.exit(1)

    tokens = _save_tokens(args.token_file, result['AuthenticationResult'],
                          auth.pool_id, auth.client_id, auth.region)
    print(f'Tokens saved to {args.token_file}')

    if args.save_credentials and not args.credentials_file:
        creds_path = CREDS_FILE
        with open(creds_path, 'w') as f:
            json.dump({'username': username, 'password': password}, f)
        os.chmod(creds_path, 0o600)
        print(f'Credentials saved to {creds_path}')

    print('\nDiscovering heating zones...')
    _test_api(tokens['IdToken'], args.token_file)


if __name__ == '__main__':
    main()
