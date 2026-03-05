"""Feishu (Lark) API client — tenant token management, message sending, event decryption."""

import base64
import hashlib
import time
from typing import Any

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.utils.config import get_settings
from app.utils.logger import logger

# Feishu Open API base URL
_FEISHU_HOST = "https://open.feishu.cn/open-apis"

# Cached tenant access token
_token_cache: dict[str, Any] = {"token": "", "expires_at": 0.0}


async def get_tenant_access_token() -> str:
    """Obtain (and cache) a tenant_access_token from Feishu."""
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["token"]

    settings = get_settings()
    url = f"{_FEISHU_HOST}/auth/v3/tenant_access_token/internal"
    payload = {
        "app_id": settings.feishu_app_id,
        "app_secret": settings.feishu_app_secret,
    }

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()

    if data.get("code") != 0:
        raise RuntimeError(f"Failed to get tenant_access_token: {data}")

    token = data["tenant_access_token"]
    expire = data.get("expire", 7200)
    _token_cache["token"] = token
    _token_cache["expires_at"] = now + expire

    logger.info("Feishu tenant_access_token refreshed (expires in %ds)", expire)
    return token


async def send_text_message(receive_id: str, text: str, receive_id_type: str = "open_id") -> dict:
    """Send a plain-text message to a Feishu user or chat.

    Args:
        receive_id: The open_id, user_id, or chat_id of the recipient.
        text: The text content to send.
        receive_id_type: One of "open_id", "user_id", "chat_id".
    """
    import json as _json

    token = await get_tenant_access_token()
    url = f"{_FEISHU_HOST}/im/v1/messages"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"receive_id_type": receive_id_type}
    body = {
        "receive_id": receive_id,
        "msg_type": "text",
        "content": _json.dumps({"text": text}),
    }

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, headers=headers, params=params, json=body)
        resp.raise_for_status()
        data = resp.json()

    if data.get("code") != 0:
        logger.error("Failed to send message: %s", data)
    else:
        logger.debug("Message sent to %s", receive_id)

    return data


def decrypt_event(encrypt_key: str, encrypted_data: str) -> str:
    """Decrypt a Feishu event callback body (AES-256-CBC, custom key derivation).

    Feishu uses SHA-256 of the encrypt_key as the AES key and the first 16 bytes
    of the SHA-256 hash as the IV.  The encrypted data is base64-encoded.
    """
    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    iv = key[:16]

    encrypted_bytes = base64.b64decode(encrypted_data)
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    decrypted = decryptor.update(encrypted_bytes) + decryptor.finalize()

    # Remove PKCS7 padding
    pad_len = decrypted[-1]
    return decrypted[:-pad_len].decode("utf-8")
