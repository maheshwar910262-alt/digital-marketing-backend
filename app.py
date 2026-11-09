
from flask import Flask, jsonify, request
from flask_cors import CORS
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

from ai_tools_api import (
    ai_tools_bp,
    init_ai_tools_db,
    admin_required,
)

app = Flask(__name__)

CORS(
    app,
    resources={r"/api/*": {"origins": "*"}},
    allow_headers=["Content-Type", "Authorization"],
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
)

app.register_blueprint(ai_tools_bp)

DB_PATH = Path(__file__).with_name("digital_marketing.db")


def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def valid_url(url):
    try:
        parsed = urlparse(url)
        return (
            parsed.scheme in ("http", "https")
            and bool(parsed.netloc)
            and " " not in url
        )
    except Exception:
        return False


def normalize_url(url):
    return url.strip().rstrip("/").lower()


def url_exists(db, url):
    normalized = normalize_url(url)

    existing = db.execute(
        "SELECT url FROM backlinks"
    ).fetchall()

    for row in existing:
        if normalize_url(row["url"]) == normalized:
            return True

    existing = db.execute(
        "SELECT url FROM backlink_submissions"
    ).fetchall()

    for row in existing:
        if normalize_url(row["url"]) == normalized:
            return True

    return False


# Database initialization
def init_db():
    with get_db() as db:

        db.execute("""
            CREATE TABLE IF NOT EXISTS backlinks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                completed INTEGER NOT NULL DEFAULT 0,
                best INTEGER NOT NULL DEFAULT 0
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS backlink_status (
                url TEXT PRIMARY KEY,
                completed_at INTEGER NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS backlink_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                reviewed_at TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS social_platforms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                icon TEXT DEFAULT '',
                title TEXT NOT NULL UNIQUE,
                description TEXT DEFAULT '',
                link TEXT NOT NULL,
                pdf_link TEXT DEFAULT '',
                video_link TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_submission_url_unique
            ON backlink_submissions(url COLLATE NOCASE)
        """)


# Initialize tables for both local and Gunicorn deployment
init_db()
init_ai_tools_db()


@app.route("/")
def home():
    return jsonify({
        "message": "Digital Marketing Backend is running"
    })


@app.route("/api/health")
def health():
    return jsonify({
        "status": "success",
        "backend": "Flask",
        "database": "SQLite"
    })


@app.route("/api/hello", methods=["POST"])
def hello():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "Student")).strip() or "Student"

    return jsonify({
        "message": f"Hello, {name}! Flask received your data."
    })


# PUBLIC: Get approved backlinks
@app.route("/api/backlinks", methods=["GET"])
def get_backlinks():
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM backlinks ORDER BY id DESC"
        ).fetchall()

    return jsonify([dict(row) for row in rows])


# PUBLIC: Submit a backlink for admin review
@app.route("/api/backlink-submissions", methods=["POST"])
def submit_backlink():
    data = request.get_json(silent=True) or {}

    name = str(data.get("name", "")).strip()
    url = str(data.get("url", "")).strip()

    if not name or not url:
        return jsonify({
            "error": "Website name and URL are required."
        }), 400

    if not valid_url(url):
        return jsonify({
            "error": "Enter a valid HTTP or HTTPS URL."
        }), 400

    try:
        with get_db() as db:

            if url_exists(db, url):
                return jsonify({
                    "error": "This backlink URL is already available or submitted."
                }), 409

            cursor = db.execute("""
                INSERT INTO backlink_submissions (name, url)
                VALUES (?, ?)
            """, (name, url))

            submission_id = cursor.lastrowid

        return jsonify({
            "message": "Submitted successfully. Your backlink will be reviewed within 24 hours.",
            "id": submission_id,
            "status": "pending"
        }), 201

    except sqlite3.IntegrityError:
        return jsonify({
            "error": "This URL has already been submitted."
        }), 409

    except sqlite3.Error:
        app.logger.exception("Backlink submission failed")
        return jsonify({
            "error": "Unable to submit backlink."
        }), 500


# ADMIN: View all submissions
@app.route("/api/backlink-submissions", methods=["GET"])
@admin_required
def get_submissions():
    with get_db() as db:
       rows = db.execute("""
    SELECT *
    FROM backlink_submissions
    WHERE status = 'pending'
    ORDER BY id DESC
""").fetchall()

    return jsonify([dict(row) for row in rows])


# ADMIN: Approve a submission
@app.route(
    "/api/backlink-submissions/<int:submission_id>/approve",
    methods=["POST"]
)
@admin_required
def approve_submission(submission_id):

    try:
        with get_db() as db:

            submission = db.execute("""
                SELECT *
                FROM backlink_submissions
                WHERE id = ? AND status = 'pending'
            """, (submission_id,)).fetchone()

            if submission is None:
                return jsonify({
                    "error": "Pending submission not found."
                }), 404

            db.execute("""
                INSERT INTO backlinks (name, url)
                VALUES (?, ?)
            """, (submission["name"], submission["url"]))

            db.execute("""
                UPDATE backlink_submissions
                SET status = 'approved',
                    reviewed_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (submission_id,))

        return jsonify({
            "message": "Backlink approved and published."
        })

    except sqlite3.IntegrityError:
        return jsonify({
            "error": "This backlink URL already exists."
        }), 409

    except sqlite3.Error:
        app.logger.exception("Backlink approval failed")
        return jsonify({
            "error": "Unable to approve backlink."
        }), 500


# ADMIN: Reject a submission
@app.route(
    "/api/backlink-submissions/<int:submission_id>/reject",
    methods=["POST"]
)
@admin_required
def reject_submission(submission_id):

    with get_db() as db:
        cursor = db.execute("""
            UPDATE backlink_submissions
            SET status = 'rejected',
                reviewed_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'
        """, (submission_id,))

        if cursor.rowcount == 0:
            return jsonify({
                "error": "Pending submission not found."
            }), 404

    return jsonify({
        "message": "Backlink submission rejected."
    })


# ADMIN: Add backlink manually
@app.route("/api/backlinks", methods=["POST"])
@admin_required
def add_backlink():
    data = request.get_json(silent=True) or {}

    name = str(data.get("name", "")).strip()
    url = str(data.get("url", "")).strip()

    if not name or not url:
        return jsonify({
            "error": "Name and URL are required"
        }), 400

    if not valid_url(url):
        return jsonify({
            "error": "Enter a valid HTTP or HTTPS URL"
        }), 400

    try:
        with get_db() as db:

            if url_exists(db, url):
                return jsonify({
                    "error": "This URL already exists or is submitted."
                }), 409

            cursor = db.execute(
                "INSERT INTO backlinks (name, url) VALUES (?, ?)",
                (name, url)
            )

            saved_id = cursor.lastrowid

        return jsonify({
            "message": "Backlink saved successfully",
            "id": saved_id,
            "name": name,
            "url": url,
            "completed": 0,
            "best": 0
        }), 201

    except sqlite3.Error:
        app.logger.exception("Could not add backlink")
        return jsonify({
            "error": "Unable to save backlink."
        }), 500


# PUBLIC: View backlink status
@app.route("/api/backlink-status", methods=["GET"])
def get_backlink_status():
    with get_db() as db:
        rows = db.execute(
            "SELECT url, completed_at FROM backlink_status"
        ).fetchall()

    return jsonify({
        row["url"]: row["completed_at"]
        for row in rows
    })


# ADMIN: Update backlink status
@app.route("/api/backlink-status", methods=["POST"])
@admin_required
def save_backlink_status():
    data = request.get_json(silent=True) or {}

    url = str(data.get("url", "")).strip()
    completed_at = data.get("completed_at")

    if not url:
        return jsonify({"error": "URL is required"}), 400

    try:
        with get_db() as db:
            if completed_at is None:
                db.execute(
                    "DELETE FROM backlink_status WHERE url = ?",
                    (url,)
                )
            else:
                completed_at = int(completed_at)

                db.execute("""
                    INSERT INTO backlink_status (url, completed_at)
                    VALUES (?, ?)
                    ON CONFLICT(url) DO UPDATE SET
                        completed_at = excluded.completed_at
                """, (url, completed_at))

        return jsonify({
            "message": "Status saved successfully"
        })

    except (ValueError, TypeError):
        return jsonify({
            "error": "Invalid completion timestamp"
        }), 400


# ADMIN: Delete backlink
@app.route(
    "/api/backlinks/<int:backlink_id>",
    methods=["DELETE"]
)
@admin_required
def delete_backlink(backlink_id):
    try:
        with get_db() as db:

            row = db.execute(
                "SELECT url FROM backlinks WHERE id = ?",
                (backlink_id,)
            ).fetchone()

            if row is None:
                return jsonify({
                    "error": "Backlink not found"
                }), 404

            db.execute(
                "DELETE FROM backlink_status WHERE url = ?",
                (row["url"],)
            )

            db.execute(
                "DELETE FROM backlinks WHERE id = ?",
                (backlink_id,)
            )

        return jsonify({
            "message": "Backlink deleted successfully"
        })

    except sqlite3.Error:
        app.logger.exception("Could not delete backlink")
        return jsonify({
            "error": "Unable to delete backlink"
        }), 500


# ==============================
# SOCIAL MEDIA PLATFORM APIs
# ==============================

def format_social_platform(row):
    return {
        "id": row["id"],
        "icon": row["icon"],
        "title": row["title"],
        "description": row["description"],
        "link": row["link"],
        "pdfLink": row["pdf_link"],
        "videoLink": row["video_link"]
    }


# PUBLIC: Get all social platforms
@app.route("/api/social-platforms", methods=["GET"])
def get_social_platforms():
    with get_db() as db:
        rows = db.execute("""
            SELECT * FROM social_platforms
            ORDER BY id ASC
        """).fetchall()

    return jsonify([format_social_platform(row) for row in rows])


# ADMIN: Add social platform
@app.route("/api/social-platforms", methods=["POST"])
@admin_required
def add_social_platform():
    data = request.get_json(silent=True) or {}

    icon = str(data.get("icon", "")).strip()
    title = str(data.get("title", "")).strip()
    description = str(data.get("description", "")).strip()
    link = str(data.get("link", "")).strip()
    pdf_link = str(data.get("pdfLink", "")).strip()
    video_link = str(data.get("videoLink", "")).strip()

    if not title or not link:
        return jsonify({
            "error": "Platform title and link are required."
        }), 400

    if not valid_url(link):
        return jsonify({"error": "Enter a valid platform URL."}), 400

    try:
        with get_db() as db:
            cursor = db.execute("""
                INSERT INTO social_platforms
                (icon, title, description, link, pdf_link, video_link)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                icon, title, description, link, pdf_link, video_link
            ))

            platform_id = cursor.lastrowid

        return jsonify({
            "message": "Social platform added successfully.",
            "id": platform_id
        }), 201

    except sqlite3.IntegrityError:
        return jsonify({
            "error": "This platform already exists."
        }), 409


# ADMIN: Update social platform
@app.route("/api/social-platforms/<int:platform_id>", methods=["PUT"])
@admin_required
def update_social_platform(platform_id):
    data = request.get_json(silent=True) or {}

    title = str(data.get("title", "")).strip()
    link = str(data.get("link", "")).strip()

    if not title or not link or not valid_url(link):
        return jsonify({
            "error": "Valid platform title and URL are required."
        }), 400

    try:
        with get_db() as db:
            cursor = db.execute("""
                UPDATE social_platforms
                SET icon = ?,
                    title = ?,
                    description = ?,
                    link = ?,
                    pdf_link = ?,
                    video_link = ?
                WHERE id = ?
            """, (
                str(data.get("icon", "")).strip(),
                title,
                str(data.get("description", "")).strip(),
                link,
                str(data.get("pdfLink", "")).strip(),
                str(data.get("videoLink", "")).strip(),
                platform_id
            ))

            if cursor.rowcount == 0:
                return jsonify({
                    "error": "Social platform not found."
                }), 404

        return jsonify({
            "message": "Social platform updated successfully."
        })

    except sqlite3.IntegrityError:
        return jsonify({
            "error": "This platform title already exists."
        }), 409


# ADMIN: Delete social platform
@app.route("/api/social-platforms/<int:platform_id>", methods=["DELETE"])
@admin_required
def delete_social_platform(platform_id):
    with get_db() as db:
        cursor = db.execute("""
            DELETE FROM social_platforms WHERE id = ?
        """, (platform_id,))

        if cursor.rowcount == 0:
            return jsonify({
                "error": "Social platform not found."
            }), 404

    return jsonify({
        "message": "Social platform deleted successfully."
    })

if __name__ == "__main__":
    app.run(debug=True, port=5000)
