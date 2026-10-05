"""HTTPS helpers shared by the Telegram and weather code.

Uses the computer's own trusted certificates (macOS Keychain via truststore), so
requests work behind antivirus web protection, VPNs and inspecting networks.
"""

import json
import ssl
import urllib.parse
import urllib.request


def _ssl_context():
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        pass
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


SSL_CONTEXT = _ssl_context()


def get_json(url, params=None, timeout=20):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "garmin-dashboard"})
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as r:
        return json.load(r)
