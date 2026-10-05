"""Build the password-protected dashboard for GitHub Pages.

Encrypts your data (garmin.json plus per-run details) with a password, and copies
the page next to it. The published site contains no readable data: the page asks
for the password and decrypts in your browser.

    DASHBOARD_PASSWORD='a long passphrase' python publish.py --out _site

Encryption: PBKDF2-SHA256 (600,000 iterations) derives a 256-bit key from the
password and a random salt; the gzip-compressed JSON is encrypted with AES-GCM.
Anyone can download the encrypted file and try passwords offline, so use a long
passphrase (four or more random words).
"""

import argparse
import base64
import gzip
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SITE = ROOT / "site"
DATA_FILE = SITE / "data" / "garmin.json"
ITERATIONS = 600_000
PAGE_FILES = ["index.html", "app.js", "style.css"]


def encrypt(obj, password, salt=None):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    salt, iv = salt or os.urandom(16), os.urandom(12)  # a fresh IV every time (required for AES-GCM)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERATIONS).derive(password.encode())
    plain = gzip.compress(json.dumps(obj, separators=(",", ":")).encode())
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iterations": ITERATIONS, "salt": b64(salt), "iv": b64(iv),
            "cipher": "AES-GCM", "compression": "gzip", "data": b64(AESGCM(key).encrypt(iv, plain, None))}


def decrypt(blob, password):
    """Inverse of encrypt(), used for testing."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    d = base64.b64decode
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=d(blob["salt"]), iterations=blob["iterations"]).derive(password.encode())
    return json.loads(gzip.decompress(AESGCM(key).decrypt(d(blob["iv"]), d(blob["data"]), None)))


def current_salt(url, password):
    """The salt of the version already online, if it opens with this password. Reusing
    it keeps "Remember on this device" working after the daily update."""
    if not url:
        return None
    try:
        import net
        blob = net.get_json(url)
        decrypt(blob, password)  # only reuse if the password still matches
        return base64.b64decode(blob["salt"])
    except Exception:  # noqa: BLE001 - first publish, or password changed: new salt
        return None


def bundle():
    """garmin.json plus the per-run details it links to, in one object."""
    data = json.loads(DATA_FILE.read_text())
    details = {}
    for a in data.get("activities") or []:
        path = a.get("detailPath")
        if path:
            f = SITE / "data" / path
            if f.exists():
                details[str(a["id"])] = json.loads(f.read_text())
    return {"data": data, "details": details, "publishedAt": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default="_site", help="folder to write the site to (default _site)")
    p.add_argument("--live-url", default=os.getenv("DASHBOARD_URL", ""),
                   help="URL of the published garmin.enc.json, to keep its salt (devices stay unlocked)")
    args = p.parse_args()
    password = os.getenv("DASHBOARD_PASSWORD", "")
    if len(password) < 12:
        sys.exit("Set DASHBOARD_PASSWORD to a passphrase of at least 12 characters (four random words is good).")
    if not DATA_FILE.exists():
        sys.exit("No data to publish. Run fetch_garmin.py first.")

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    (out / "data").mkdir(parents=True)
    for name in PAGE_FILES:
        shutil.copy2(SITE / name, out / name)
    (out / ".nojekyll").write_text("")  # serve files as-is
    b = bundle()
    salt = current_salt(args.live_url, password)
    (out / "data" / "garmin.enc.json").write_text(json.dumps(encrypt(b, password, salt)))
    size = (out / "data" / "garmin.enc.json").stat().st_size
    print(f"Built {out}/ with encrypted data ({size // 1024} KB, {len(b['details'])} run details"
          f"{', same salt as the live site' if salt else ', new salt'}).")


if __name__ == "__main__":
    main()
