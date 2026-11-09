
import os
import sqlite3
import secrets
from pathlib import Path
from urllib.parse import urlparse
from functools import wraps

from flask import Blueprint, jsonify, request, current_app
from itsdangerous import (
    URLSafeTimedSerializer,
    BadSignature,
    SignatureExpired,
)

ai_tools_bp = Blueprint("ai_tools", __name__)

DB_PATH = Path(__file__).with_name("digital_marketing.db")
TOKEN_MAX_AGE_SECONDS = 8 * 60 * 60


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_ai_tools_db():
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_tools (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE COLLATE NOCASE,
                category TEXT NOT NULL DEFAULT 'General',
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)


def _serializer():
    secret = os.environ.get("AI_TOOLS_SECRET_KEY") or os.environ.get("SECRET_KEY")

    if not secret or len(secret) < 32:
        return None

    return URLSafeTimedSerializer(
        secret_key=secret,
        salt="ai-tools-admin-v1"
    )


def _admin_credentials():
    return (
        os.environ.get("AI_TOOLS_ADMIN_USERNAME", ""),
        os.environ.get("AI_TOOLS_ADMIN_PASSWORD", ""),
    )


def _valid_url(value):
    try:
        parsed = urlparse(value)
        return (
            parsed.scheme in ("http", "https")
            and bool(parsed.netloc)
            and " " not in value
        )
    except Exception:
        return False


def _clean_text(value, max_len):
    if not isinstance(value, str):
        return ""
    return value.strip()[:max_len]


# Common admin authentication for AI Tools and Backlinks
def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        auth = request.headers.get("Authorization", "")

        if not auth.startswith("Bearer "):
            return jsonify({
                "error": "Office admin login required."
            }), 401

        serializer = _serializer()

        if serializer is None:
            return jsonify({
                "error": "Server admin security is not configured."
            }), 503

        token = auth[7:].strip()

        try:
            payload = serializer.loads(
                token,
                max_age=TOKEN_MAX_AGE_SECONDS
            )
        except SignatureExpired:
            return jsonify({
                "error": "Admin session expired. Please log in again."
            }), 401
        except BadSignature:
            return jsonify({
                "error": "Invalid admin session."
            }), 401

        configured_user, configured_password = _admin_credentials()

        if (
            not configured_user
            or not configured_password
            or payload.get("role") != "admin"
            or payload.get("username") != configured_user
        ):
            return jsonify({
                "error": "Office admin access required."
            }), 403

        return fn(*args, **kwargs)

    return wrapped


# Common login endpoint
@ai_tools_bp.post("/api/ai-tools/login")
def ai_tools_login():
    configured_user, configured_password = _admin_credentials()
    serializer = _serializer()

    if not configured_user or not configured_password or serializer is None:
        return jsonify({
            "error": (
                "Admin login is not configured. Set "
                "AI_TOOLS_ADMIN_USERNAME, AI_TOOLS_ADMIN_PASSWORD "
                "and AI_TOOLS_SECRET_KEY."
            )
        }), 503

    data = request.get_json(silent=True) or {}

    username = _clean_text(data.get("username"), 120)
    password = (
        data.get("password")
        if isinstance(data.get("password"), str)
        else ""
    )

    if not (
        secrets.compare_digest(username, configured_user)
        and secrets.compare_digest(password, configured_password)
    ):
        return jsonify({
            "error": "Incorrect admin username or password."
        }), 401

    token = serializer.dumps({
        "role": "admin",
        "username": configured_user,
    })

    return jsonify({
        "token": token,
        "expires_in": TOKEN_MAX_AGE_SECONDS,
        "username": configured_user,
    })


# Public: list and search AI Tools
@ai_tools_bp.get("/api/ai-tools")
def list_ai_tools():
    search = _clean_text(request.args.get("q", ""), 150)

    try:
        with _db() as conn:
            if search:
                term = f"%{search}%"
                rows = conn.execute("""
                    SELECT id, name, url, category, description,
                           created_at, updated_at
                    FROM ai_tools
                    WHERE name LIKE ?
                       OR url LIKE ?
                       OR category LIKE ?
                       OR description LIKE ?
                    ORDER BY name COLLATE NOCASE
                """, (term, term, term, term)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT id, name, url, category, description,
                           created_at, updated_at
                    FROM ai_tools
                    ORDER BY name COLLATE NOCASE
                """).fetchall()

        return jsonify([dict(row) for row in rows])

    except sqlite3.Error:
        current_app.logger.exception("Could not list AI tools")
        return jsonify({
            "error": "Unable to load AI tools."
        }), 500


# Admin only: add AI Tool
@ai_tools_bp.post("/api/ai-tools")
@admin_required
def create_ai_tool():
    data = request.get_json(silent=True) or {}

    name = _clean_text(data.get("name"), 120)
    url = _clean_text(data.get("url"), 2048)
    category = _clean_text(data.get("category"), 80) or "General"
    description = _clean_text(data.get("description"), 1000)

    if not name or not url:
        return jsonify({
            "error": "Tool name and website URL are required."
        }), 400

    if not _valid_url(url):
        return jsonify({
            "error": "Enter a valid HTTP or HTTPS URL."
        }), 400

    try:
        with _db() as conn:
            cur = conn.execute("""
                INSERT INTO ai_tools (name, url, category, description)
                VALUES (?, ?, ?, ?)
            """, (name, url, category, description))

            row = conn.execute("""
                SELECT id, name, url, category, description,
                       created_at, updated_at
                FROM ai_tools WHERE id = ?
            """, (cur.lastrowid,)).fetchone()

        return jsonify(dict(row)), 201

    except sqlite3.IntegrityError:
        return jsonify({
            "error": "This AI tool URL already exists."
        }), 409
    except sqlite3.Error:
        current_app.logger.exception("Could not create AI tool")
        return jsonify({
            "error": "Unable to save AI tool."
        }), 500


# Admin only: edit AI Tool
@ai_tools_bp.put("/api/ai-tools/<int:tool_id>")
@admin_required
def update_ai_tool(tool_id):
    data = request.get_json(silent=True) or {}

    name = _clean_text(data.get("name"), 120)
    url = _clean_text(data.get("url"), 2048)
    category = _clean_text(data.get("category"), 80) or "General"
    description = _clean_text(data.get("description"), 1000)

    if not name or not url:
        return jsonify({
            "error": "Tool name and website URL are required."
        }), 400

    if not _valid_url(url):
        return jsonify({
            "error": "Enter a valid HTTP or HTTPS URL."
        }), 400

    try:
        with _db() as conn:
            cur = conn.execute("""
                UPDATE ai_tools
                SET name = ?, url = ?, category = ?,
                    description = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (name, url, category, description, tool_id))

            if cur.rowcount == 0:
                return jsonify({
                    "error": "AI tool not found."
                }), 404

            row = conn.execute("""
                SELECT id, name, url, category, description,
                       created_at, updated_at
                FROM ai_tools WHERE id = ?
            """, (tool_id,)).fetchone()

        return jsonify(dict(row))

    except sqlite3.IntegrityError:
        return jsonify({
            "error": "Another AI tool already uses this URL."
        }), 409
    except sqlite3.Error:
        current_app.logger.exception("Could not update AI tool")
        return jsonify({
            "error": "Unable to update AI tool."
        }), 500


# Admin only: delete AI Tool
@ai_tools_bp.delete("/api/ai-tools/<int:tool_id>")
@admin_required
def delete_ai_tool(tool_id):
    try:
        with _db() as conn:
            cur = conn.execute(
                "DELETE FROM ai_tools WHERE id = ?",
                (tool_id,)
            )

            if cur.rowcount == 0:
                return jsonify({
                    "error": "AI tool not found."
                }), 404

        return jsonify({
            "message": "AI tool deleted."
        })

    except sqlite3.Error:
        current_app.logger.exception("Could not delete AI tool")
        return jsonify({
            "error": "Unable to delete AI tool."
        }), 500
