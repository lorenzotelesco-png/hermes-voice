"""Web Push, sender side: payload encryption (RFC 8291) and VAPID (RFC 8292).

Written out rather than taken from pywebpush, which would pull in requests and
three more packages for what is ~60 lines on top of `cryptography`. The test
suite decrypts what this produces the way a browser does.
"""
import base64
import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

RECORD_SIZE = 4096


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _hmac(key, data):
    return hmac.new(key, data, hashlib.sha256).digest()


def public_bytes(key: ec.EllipticCurvePrivateKey) -> bytes:
    """The uncompressed P-256 point, as browsers want it."""
    return key.public_key().public_bytes(serialization.Encoding.X962,
                                         serialization.PublicFormat.UncompressedPoint)


def load_or_create_key(path):
    """The server's VAPID key, made once and kept: rotating it orphans every subscription."""
    if os.path.exists(path):
        with open(path, "rb") as f:
            return serialization.load_pem_private_key(f.read(), password=None)
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    return key


def encrypt(payload: bytes, p256dh: str, auth: str, *, salt=None, sender_key=None) -> bytes:
    """aes128gcm body for one subscription (RFC 8291 §3, one record)."""
    ua_public = unb64u(p256dh)
    auth_secret = unb64u(auth)
    sender_key = sender_key or ec.generate_private_key(ec.SECP256R1())
    as_public = public_bytes(sender_key)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    shared = sender_key.exchange(ec.ECDH(), ua_key)

    # HKDF (extract, then one block of expand) as spelled out in the RFC.
    prk_key = _hmac(auth_secret, shared)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    salt = salt or os.urandom(16)
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]

    # A single record: the payload, then the 0x02 "last record" delimiter.
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    header = salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(as_public)]) + as_public
    return header + ciphertext


def vapid_headers(endpoint: str, key: ec.EllipticCurvePrivateKey, subject: str, ttl_s=12 * 3600):
    """Authorization for one push service: a JWT signed with the server key."""
    parts = urlsplit(endpoint)
    claims = {"aud": f"{parts.scheme}://{parts.netloc}", "exp": int(time.time()) + ttl_s, "sub": subject}
    signing_input = (b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
                     + "." + b64u(json.dumps(claims, separators=(",", ":")).encode()))
    r, s = decode_dss_signature(key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256())))
    token = signing_input + "." + b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return {"Authorization": f"vapid t={token}, k={b64u(public_bytes(key))}"}
