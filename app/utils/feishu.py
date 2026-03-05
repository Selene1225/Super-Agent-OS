"""Feishu (Lark) API client — tenant token, messaging, Bitable CRUD, event decryption."""

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


# ─── Bitable (多维表格) CRUD ─────────────────────────────────────────────

async def _bitable_headers() -> dict[str, str]:
    """Return headers with Authorization for Bitable API calls."""
    token = await get_tenant_access_token()
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def bitable_create_record(
    app_token: str,
    table_id: str,
    fields: dict[str, Any],
) -> dict:
    """Create a single record in a Bitable table.

    Args:
        app_token: Bitable app token (from the URL).
        table_id: Table ID within the Bitable.
        fields: Field name → value mapping.

    Returns:
        The API response data including the created record_id.
    """
    url = f"{_FEISHU_HOST}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    headers = await _bitable_headers()
    body = {"fields": fields}

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

    if data.get("code") != 0:
        logger.error("Bitable create_record failed: %s", data)
    else:
        record_id = data.get("data", {}).get("record", {}).get("record_id", "?")
        logger.debug("Bitable record created: %s", record_id)

    return data


async def bitable_update_record(
    app_token: str,
    table_id: str,
    record_id: str,
    fields: dict[str, Any],
) -> dict:
    """Update a single record in a Bitable table."""
    url = f"{_FEISHU_HOST}/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"
    headers = await _bitable_headers()
    body = {"fields": fields}

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.put(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

    if data.get("code") != 0:
        logger.error("Bitable update_record failed: %s", data)
    else:
        logger.debug("Bitable record updated: %s", record_id)

    return data


async def bitable_list_records(
    app_token: str,
    table_id: str,
    *,
    filter_expr: str = "",
    sort: list[dict] | None = None,
    page_size: int = 100,
    page_token: str = "",
) -> dict:
    """List records from a Bitable table with optional filter and sort.

    Args:
        app_token: Bitable app token.
        table_id: Table ID.
        filter_expr: Bitable filter expression, e.g.
            'AND(CurrentValue.[状态]="待执行")'.
        sort: List of sort specs, e.g.
            [{"field_name": "提醒时间", "desc": false}].
        page_size: Number of records per page (max 500).
        page_token: Pagination token for fetching next page.

    Returns:
        API response containing items, page_token, has_more, total.
    """
    url = f"{_FEISHU_HOST}/bitable/v1/apps/{app_token}/tables/{table_id}/records/search"
    headers = await _bitable_headers()

    body: dict[str, Any] = {"page_size": page_size}
    if filter_expr:
        body["filter"] = {"conjunction": "and", "conditions": []}
        # Use the raw filter string approach if complex; for simple cases
        # we'll build conditions in the caller
    if sort:
        body["sort"] = sort
    if page_token:
        body["page_token"] = page_token

    # Use the simpler query params approach for filter
    params: dict[str, Any] = {}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, headers=headers, json=body, params=params)
        resp.raise_for_status()
        data = resp.json()

    if data.get("code") != 0:
        logger.error("Bitable list_records failed: %s", data)

    return data


async def bitable_delete_record(
    app_token: str,
    table_id: str,
    record_id: str,
) -> dict:
    """Delete a single record from a Bitable table."""
    url = f"{_FEISHU_HOST}/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"
    headers = await _bitable_headers()

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.delete(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    if data.get("code") != 0:
        logger.error("Bitable delete_record failed: %s", data)
    else:
        logger.debug("Bitable record deleted: %s", record_id)

    return data
