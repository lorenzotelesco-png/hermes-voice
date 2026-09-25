"""Web Push: encryption against the RFC's own example, VAPID, subscriptions, sending."""
import hashlib
import hmac
import json

import _setup
import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from hub import push, webpush
from hub.webpush import b64u, unb64u

# ── RFC 8291 Appendix A, byte for byte ─────────────────────────────
RFC = {
    "plaintext": "V2hlbiBJIGdyb3cgdXAsIEkgd2FudCB0byBiZSBhIHdhdGVybWVsb24",
    "as_private": "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw",
    "ua_public": "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
    "auth": "BTBZMqHH6r4Tts7J_aSIgg",
    "salt": "DGv6ra1nlYgDCS1FRnbzlw",
    "body": "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN",
}
sender = ec.derive_private_key(int.from_bytes(unb64u(RFC["as_private"]), "big"), ec.SECP256R1())
body = webpush.encrypt(unb64u(RFC["plaintext"]), RFC["ua_public"], RFC["auth"],
                       salt=unb64u(RFC["salt"]), sender_key=sender)
assert b64u(body) == RFC["body"], b64u(body)
print("cifratura identica all'esempio della RFC 8291  OK")


# ── what a browser does on the other end ───────────────────────────
def decrypt(body, ua_private, auth):
    salt, keyid = body[:16], body[21:21 + body[20]]
    ua_public = webpush.public_bytes(ua_private)
    shared = ua_private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), keyid))
    h = lambda k, d: hmac.new(k, d, hashlib.sha256).digest()  # noqa: E731
    ikm = h(h(auth, shared), b"WebPush: info\x00" + ua_public + keyid + b"\x01")
    prk = h(salt, ikm)
    plain = AESGCM(h(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]).decrypt(
        h(prk, b"Content-Encoding: nonce\x00\x01")[:12], body[21 + body[20]:], None)
    assert plain.endswith(b"\x02")
    return plain[:-1]


phone_key = ec.generate_private_key(ec.SECP256R1())
phone_auth = b"0123456789abcdef"
SUB = {"endpoint": "https://web.push.apple.com/QGuQyavXutnMdL",
       "keys": {"p256dh": b64u(webpush.public_bytes(phone_key)), "auth": b64u(phone_auth)}}

# ── VAPID ──────────────────────────────────────────────────────────
hdr = webpush.vapid_headers(SUB["endpoint"], push.key(), "https://hub.example")
token, k = hdr["Authorization"].removeprefix("vapid t=").split(", k=")
signing_input, sig = token.rsplit(".", 1)
raw = unb64u(sig)
push.key().public_key().verify(
    encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
    signing_input.encode(), ec.ECDSA(hashes.SHA256()))
claims = json.loads(unb64u(signing_input.split(".")[1]))
assert claims["aud"] == "https://web.push.apple.com" and claims["sub"] == "https://hub.example", claims
assert k == push.public_key()
print("VAPID: firma valida, aud = servizio push, sub = origine dell'app  OK")

# ── subscriptions and sending ──────────────────────────────────────
sent_to = []


def push_service(req):
    sent_to.append(req)
    if "gone" in str(req.url):
        return httpx.Response(410)
    return httpx.Response(201)


_setup.mock(push_service)
with _setup.signed_in_client() as c:
    assert c.get("/api/push/key").json()["key"] == push.public_key()
    for bad in ("https://evil.example/push", "http://web.push.apple.com/x", ""):
        r = c.post("/api/push/subscribe", json={"subscription": {**SUB, "endpoint": bad}})
        assert r.status_code == 400, (bad, r.status_code)
    print("endpoint fuori dai servizi push, o non https: rifiutato  OK")

    origin = "https://testserver"   # the app's own origin: anything else is cross-site
    assert c.post("/api/push/subscribe", json={"subscription": SUB}, headers={"Origin": origin}).json()["devices"] == 1
    gone = {**SUB, "endpoint": "https://fcm.googleapis.com/fcm/send/gone"}
    assert c.post("/api/push/subscribe", json={"subscription": gone}, headers={"Origin": origin}).json()["devices"] == 2

    r = c.post("/api/push/test", headers={"Origin": origin})
    assert r.json() == {"sent": 1}, r.text
    req = next(q for q in sent_to if "apple" in str(q.url))
    assert req.headers["content-encoding"] == "aes128gcm" and req.headers["ttl"] == "3600"
    assert req.headers["authorization"].startswith("vapid t=")
    note = json.loads(decrypt(req.content, phone_key, phone_auth))
    assert note["title"] == "Notifiche attive" and note["url"] == "/#/server", note
    print("invio: il telefono decifra titolo e testo  OK")

    assert [s["endpoint"] for s in push.subscriptions()] == [SUB["endpoint"]]
    print("iscrizione rifiutata dal servizio (410): rimossa  OK")

    assert c.post("/api/push/unsubscribe", json={"endpoint": SUB["endpoint"]}).json()["devices"] == 0

print()
print("OK: Web Push cifrato come da RFC, VAPID, iscrizioni e invio")
