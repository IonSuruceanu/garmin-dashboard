"""Encrypted state for the GitHub job: the Garmin login and saved history.

GitHub keeps nothing between runs except its cache, and on a public repository a
pull request's workflow can read that cache. So everything worth keeping is packed
into one archive and encrypted (AES-256-GCM) with a key derived from a secret
(STATE_SECRET, which the workflow sets from the Telegram bot token secret) before
it goes into the cache. Pull requests from forks never get secrets.

    python state.py pack     # ~/.garminconnect + site/data/store -> .state.enc
    python state.py unpack   # the reverse; a missing or unreadable file starts fresh
"""

import hashlib
import io
import os
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / ".state.enc"
PARTS = {"login": Path(os.getenv("GARMINTOKENS", "~/.garminconnect")).expanduser(),
         "store": ROOT / "site" / "data" / "store"}


def _key():
    secret = os.getenv("STATE_SECRET", "")
    if not secret:
        sys.exit("STATE_SECRET is not set.")
    return hashlib.sha256(b"garmin-dashboard-state:" + secret.encode()).digest()


def pack():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, path in PARTS.items():
            if path.exists():
                tar.add(path, arcname=name)
    nonce = os.urandom(12)
    STATE_FILE.write_bytes(nonce + AESGCM(_key()).encrypt(nonce, buf.getvalue(), None))
    print(f"State saved ({STATE_FILE.stat().st_size // 1024} KB, encrypted).")


def unpack():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not STATE_FILE.exists():
        print("No saved state yet: starting fresh.")
        return
    blob = STATE_FILE.read_bytes()
    try:
        raw = AESGCM(_key()).decrypt(blob[:12], blob[12:], None)
    except Exception:  # noqa: BLE001 - wrong key (secret changed) or damaged file
        print("Saved state couldn't be decrypted (the secret changed?): starting fresh.")
        return
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        for member in tar.getmembers():
            top, _, rest = member.name.partition("/")
            if top not in PARTS or ".." in Path(member.name).parts:
                continue  # only our own two folders
            target = PARTS[top] / rest if rest else PARTS[top]
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(tar.extractfile(member).read())
                target.chmod(0o600)
    print("State restored.")


if __name__ == "__main__":
    {"pack": pack, "unpack": unpack}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: sys.exit(__doc__))()
