"""NetEase request envelopes, using the platform's existing cryptography dependency.

Protocol reference: NeteaseCloudMusicApiEnhanced/api-enhanced v4.41.1,
util/crypto.js and util/request.js. These public protocol constants are not user keys.
"""

import base64
import hashlib
import json
import secrets
import string

from cryptography.hazmat.primitives import padding, serialization
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

PUBLIC_KEY = (
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDgtQn2JZ34ZC28NWYpAUd98iZ37BUrX/aKzmFbt7clFSs6"
    "sXqHauqKWqdtLkF2KexO40H1YTX8z2lSgBBOAxLsvaklV8k4cBFK9snQXE9/DDaFt6Rr7iVZMldczhC0JNg"
    "Tz+SHXT6CBHuX3e9SdB1Ua44oncaTWz7OBGLbCiK45wIDAQAB"
)


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def encrypt(value, key, mode):
    pad = padding.PKCS7(128).padder()
    data = pad.update(value) + pad.finalize()
    cipher = Cipher(algorithms.AES(key), mode).encryptor()
    return cipher.update(data) + cipher.finalize()


def weapi(data):
    key = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
    first = base64.b64encode(
        encrypt(compact(data).encode(), b"0CoJUm6Qyw8W8jud", modes.CBC(b"0102030405060708"))
    )
    second = encrypt(first, key.encode(), modes.CBC(b"0102030405060708"))
    numbers = serialization.load_der_public_key(base64.b64decode(PUBLIC_KEY)).public_numbers()
    encoded = pow(int.from_bytes(key[::-1].encode(), "big"), numbers.e, numbers.n)
    return {"params": base64.b64encode(second).decode(), "encSecKey": f"{encoded:0256x}"}


def eapi(path, data):
    body = compact(data)
    digest = hashlib.md5(f"nobody{path}use{body}md5forencrypt".encode()).hexdigest()
    payload = f"{path}-36cd479b6b5-{body}-36cd479b6b5-{digest}".encode()
    return {"params": encrypt(payload, b"e82ckenh8dichen8", modes.ECB()).hex().upper()}
