"""Shared paths, identity signing and checked CLI calls for personal managers."""
import base64
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen

REGISTRY = Path('/etc/buzz-managers')
USER = re.compile(r'[a-z][a-z0-9_-]{0,30}\Z')
SLUG = re.compile(r'[a-z0-9][a-z0-9-]{0,63}\Z')
HEX = re.compile(r'[a-f0-9]{64}\Z')
INVITE_CODE = re.compile(r'[A-Za-z0-9._~-]{8,512}\Z')
COORDINATION_CHANNEL_NAME = 'agent-managers'


def atomic(path, text, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.new')
    # Never follow a pre-existing symlink when writing configuration or a key.
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'w') as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(mode)
    temporary.replace(path)


def save_json(path, value, mode=0o600):
    atomic(path, json.dumps(value, indent=2) + '\n', mode)


def load_json(path):
    return json.loads(Path(path).read_text())


def saved_manager_session_id(root):
    """Return the one canonical manager UUID that a supervisor is allowed to resume."""
    identity = Path(root) / '.session-id'
    try:
        value = identity.read_text().strip()
        if str(uuid.UUID(value)) != value:
            raise ValueError
    except (OSError, ValueError, AttributeError) as error:
        raise ValueError('Manager session ID is missing or is not a full canonical UUID') from error
    return value


def exact_channel_id(value, name=COORDINATION_CHANNEL_NAME):
    rows = value.get('channels', []) if isinstance(value, dict) else value
    if not isinstance(rows, list):
        raise ValueError(f'Cannot inspect required #{name} channel')
    matches = []
    for row in rows:
        if (not isinstance(row, dict)
                or str(row.get('name', '')).casefold() != name.casefold()
                or row.get('visibility') not in ('open', 'public')):
            continue
        channel = row.get('channel_id') or row.get('id')
        if channel:
            matches.append(str(channel))
    if len(matches) != 1:
        raise ValueError(f'Required active open #{name} channel must exist exactly once; found {len(matches)}')
    return matches[0]


def account_name(value):
    if not USER.fullmatch(value):
        raise ValueError('Use a simple Linux account name, beginning with a letter')
    return value


def task_slug(value):
    if not SLUG.fullmatch(value):
        raise ValueError('Task slug must contain only lowercase letters, digits and hyphens')
    return value


def paths(user):
    account = pwd.getpwnam(account_name(user))
    return account, Path(account.pw_dir) / 'mrfix'


def services(user):
    account_name(user)
    return [f'{kind}@{user}.service' for kind in (
        'buzz-manager-watch', 'buzz-worker-supervisor', 'buzz-manager',
        'buzz-workspace-reporter')]


def worker_unit(user, slug):
    return f'buzz-worker-{account_name(user)}@{task_slug(slug)}.service'


def relay_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in ('https', 'http', 'wss', 'ws') or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ('', '/')):
        raise ValueError('Relay must be an HTTP(S) or WS(S) origin without credentials or a path')
    scheme = {'https': 'wss', 'http': 'ws'}.get(parsed.scheme, parsed.scheme)
    return f'{scheme}://{parsed.netloc}'


def http_relay(value):
    return value.replace('wss://', 'https://', 1).replace('ws://', 'http://', 1)


def relay_invite_code(value, relay):
    """Accept a relay invite code or an invite URL for this exact relay."""
    value = value.strip()
    if '://' in value:
        parsed = urlsplit(value)
        expected = urlsplit(http_relay(relay_url(relay)))
        if (parsed.scheme not in ('http', 'https') or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.netloc.casefold() != expected.netloc.casefold()):
            raise ValueError('Invite link must belong to the configured relay origin')
        match = re.fullmatch(r'/invite/([^/]+)/?', parsed.path)
        if not match:
            raise ValueError('Invite link must use the relay /invite/CODE path')
        value = unquote(match.group(1))
    if not INVITE_CODE.fullmatch(value):
        raise ValueError('Invite code is malformed')
    return value


def claim_relay_invite(key, relay, invite, timeout=20):
    """Claim direct relay membership with a NIP-98 request signed by `key`."""
    code = relay_invite_code(invite, relay)
    url = http_relay(relay_url(relay)).rstrip('/') + '/api/invites/claim'
    body = json.dumps({'code': code}, separators=(',', ':'))
    created_at = int(time.time())
    tags = [['u', url], ['method', 'POST'],
            ['payload', hashlib.sha256(body.encode()).hexdigest()],
            ['nonce', str(uuid.uuid4())]]
    public = pubkey(key)
    serialized = json.dumps([0, public, created_at, 27235, tags, ''],
                            separators=(',', ':'), ensure_ascii=False)
    event_id = hashlib.sha256(serialized.encode()).hexdigest()
    event = {'id': event_id, 'pubkey': public, 'created_at': created_at, 'kind': 27235,
             'tags': tags, 'content': '', 'sig': key.sign_schnorr(bytes.fromhex(event_id)).hex()}
    authorization = 'Nostr ' + base64.b64encode(
        json.dumps(event, separators=(',', ':')).encode()).decode()
    request = Request(url, data=body.encode(), method='POST', headers={
        'Authorization': authorization, 'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read())
    except HTTPError as error:
        raise RuntimeError(f'Relay invite was rejected (HTTP {error.code})') from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RuntimeError('Relay invite claim failed') from error
    if result.get('status') not in ('joined', 'already_member') or result.get('role') != 'member':
        raise RuntimeError('Relay returned an invalid invite-claim response')
    return result


def human_pubkey(config):
    """Human channel participant; legacy owned-manager configs use owner_pubkey."""
    value = config.get('human_pubkey') or config.get('owner_pubkey')
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise ValueError('Human public key is missing or invalid')
    return value


def clean_env(user):
    account, root = paths(user)
    env = {'HOME': account.pw_dir, 'USER': user, 'LOGNAME': user,
           'PATH': f'{root}/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
           'LANG': 'C.UTF-8'}
    return env


def as_user(user, argv, timeout=30):
    account, _ = paths(user)
    if os.geteuid() == 0:
        argv = ['runuser', '-u', user, '--', *map(str, argv)]
    elif os.geteuid() != account.pw_uid:
        raise ValueError('Operate as that account or as root')
    return subprocess.run(argv, env=clean_env(user), capture_output=True, text=True,
                          timeout=timeout, check=False)


def secret_key(raw):
    """Accept a 32-byte hex secret or checksum-validated NIP-19 nsec."""
    raw = raw.strip()
    if raw.startswith('nsec1'):
        alphabet = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l'
        if raw != raw.lower():
            raise ValueError('Invalid nsec encoding')
        try:
            values = [alphabet.index(c) for c in raw[5:]]
        except ValueError as error:
            raise ValueError('Invalid nsec encoding') from error
        expanded = [ord(c) >> 5 for c in 'nsec'] + [0] + [ord(c) & 31 for c in 'nsec']
        check = 1
        for value in expanded + values:
            top = check >> 25
            check = ((check & 0x1ffffff) << 5) ^ value
            for i, generator in enumerate((0x3b6a57b2, 0x26508e6d, 0x1ea119fa,
                                          0x3d4233dd, 0x2a1462b3)):
                if (top >> i) & 1:
                    check ^= generator
        if check != 1 or len(values) != 58:
            raise ValueError('Invalid nsec checksum or length')
        acc = bits = 0
        result = bytearray()
        for value in values[:-6]:
            acc = (acc << 5) | value
            bits += 5
            if bits >= 8:
                bits -= 8
                result.append((acc >> bits) & 255)
        if bits and (acc & ((1 << bits) - 1)):
            raise ValueError('Invalid nsec padding')
        raw = result.hex()
    if not HEX.fullmatch(raw):
        raise ValueError('Expected a 32-byte hex secret or valid nsec')
    import coincurve
    try:
        return coincurve.PrivateKey(bytes.fromhex(raw))
    except ValueError as error:
        raise ValueError('Invalid signing key') from error


def pubkey(key):
    return key.public_key.format(compressed=True)[1:].hex()


def mint_pair(root, stem):
    import coincurve
    keyfile, pubfile = root / f'{stem}-key', root / f'{stem}-pub'
    if keyfile.exists():
        key = secret_key(keyfile.read_text())
        if pubfile.exists() and pubfile.read_text().strip() != pubkey(key):
            raise ValueError('Saved public and private identities disagree')
    else:
        key = coincurve.PrivateKey()
        atomic(keyfile, key.secret.hex() + '\n')
    atomic(pubfile, pubkey(key) + '\n')
    return key


def auth_tag(key, agent):
    if not HEX.fullmatch(agent) or pubkey(key) == agent:
        raise ValueError('Owner and agent must be distinct valid public identities')
    digest = hashlib.sha256(f'nostr:agent-auth:{agent}:'.encode()).digest()
    return ['auth', pubkey(key), '', key.sign_schnorr(digest).hex()]


def config_for(user):
    _, root = paths(user)
    return load_json(root / 'manager.json')


def runtime_env(config, worker=None):
    root = Path(config['root'])
    env = clean_env(config['user'])
    env.update({'MRFIX_ROOT': str(root), 'MRFIX_MANAGER_CWD': str(root / 'manager'),
                'BUZZ_RELAY_URL': config['relay'], 'BUZZ_CLI': config['tools']['buzz']})
    if worker:
        keyfile = root / 'workers' / f"{worker['slug']}.key"
        tag = worker['auth_tag']
        channel = worker['channel']
    else:
        keyfile = root / '.buzz-key'
        tag = config.get('auth_tag', [])
        channel = config.get('channel', '')
    env.update({'BUZZ_PRIVATE_KEY': keyfile.read_text().strip(),
                'BUZZ_CHANNEL': channel})
    if tag:
        env['BUZZ_AUTH_TAG'] = json.dumps(tag, separators=(',', ':'))
    return env


def buzz(config, args, worker=None):
    result = subprocess.run([config['tools']['buzz'], *map(str, args)],
                            env=runtime_env(config, worker), capture_output=True,
                            text=True, timeout=45)
    # Do not echo a tool's stderr: it may include signed payloads or credentials.
    if result.returncode:
        raise RuntimeError(f'Buzz command {args[0]} {args[1]} failed (exit {result.returncode})')
    try:
        value = json.loads(result.stdout)
    except ValueError as error:
        raise RuntimeError('Buzz returned an invalid JSON response') from error
    if isinstance(value, dict) and (value.get('error') or value.get('accepted') is False):
        raise RuntimeError('Buzz rejected the request')
    return value
