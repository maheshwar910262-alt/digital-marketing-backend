
import os
import hmac
import time
import hashlib
import secrets
import base64
import json
from functools import wraps
from office_auth import office_admin_required
from flask import Blueprint, request, jsonify

office_auth_bp = Blueprint("office_auth", __name__)

TOKEN_TTL = 8 * 60 * 60  # 8 hours


def get_secret():
    return os.environ.get("AI_TOOLS_SECRET_KEY", "")


def create_token(username):
    secret = get_secret()
    if len(secret) < 32:
        raise RuntimeError(
            "AI_TOOLS_SECRET_KEY must be at least 32 characters"
        )

    payload = {
        "sub": username,
        "exp": int(time.time()) + TOKEN_TTL,
        "nonce": secrets.token_hex(8),
    }

    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode().rstrip("=")

    signature = hmac.new(
        secret.encode(), encoded.encode(), hashlib.sha256
    ).hexdigest()

    return f"{encoded}.{signature}"


def verify_token(token):
    try:
        encoded, signature = token.split(".", 1)
        secret = get_secret()

        expected = hmac.new(
            secret.encode(), encoded.encode(), hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(signature, expected):
            return None

        payload = json.loads(
            base64.urlsafe_b64decode(
                encoded + "=" * (-len(encoded) % 4)
            )
        )

        if payload.get("exp", 0) < int(time.time()):
            return None

        expected_username = os.environ.get(
            "AI_TOOLS_ADMIN_USERNAME", ""
        )

        if payload.get("sub") != expected_username:
            return None

        return payload.get("sub")
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def office_admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Office admin login required"}), 401

        username = verify_token(auth_header[7:].strip())
        if not username:
            return jsonify({"error": "Invalid or expired login"}), 401

        return fn(*args, **kwargs)
    return wrapper


@office_auth_bp.post("/api/office-login")
def office_login():
    configured_user = os.environ.get("AI_TOOLS_ADMIN_USERNAME", "")
    configured_password = os.environ.get("AI_TOOLS_ADMIN_PASSWORD", "")

    if not configured_user or not configured_password or len(get_secret()) < 32:
        return jsonify({
            "error": "Office admin login is not configured on the server"
        }), 503

    data = request.get_json(silent=True) or {}
    username = str(data.get("username", ""))
    password = str(data.get("password", ""))

    if (
        hmac.compare_digest(username, configured_user)
        and hmac.compare_digest(password, configured_password)
    ):
        return jsonify({
            "token": create_token(configured_user),
            "expires_in": TOKEN_TTL
        })

    return jsonify({"error": "Invalid username or password"}), 401
