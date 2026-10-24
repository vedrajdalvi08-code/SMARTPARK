"""
SMARTPARK - Main Application Entrypoint
Flask REST API server and static frontend host.
"""

import os
import sys
import logging
from pathlib import Path
from flask import Flask, send_from_directory, jsonify, redirect, session
from flask_cors import CORS

from config import Config
from database import init_db
from routes import api

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("smartpark.app")

# Path to frontend assets
BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"


def _session_cookie_secure():
    """Keep production cookies secure while allowing explicit local HTTP use."""
    configured = os.getenv("SESSION_COOKIE_SECURE")
    if configured is None:
        return os.getenv("FLASK_ENV", "development").lower() == "production"

    value = configured.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError("SESSION_COOKIE_SECURE must be a boolean value.")


def create_app():
    """Application factory for SMARTPARK."""
    Config.validate()
    app = Flask(__name__, static_folder=None)
    app.config.from_object(Config)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = _session_cookie_secure()

    # Enable Cross-Origin Resource Sharing
    # In production the Vercel frontend normally reaches the API through the
    # same-origin /api proxy. Keep direct backend access configurable for
    # local development and ESP32/browser testing.
    allowed_origins = os.getenv("CORS_ORIGINS", "http://localhost:5000,http://127.0.0.1:5000")
    origins = [o.strip() for o in allowed_origins.split(",") if o.strip()]
    CORS(app, resources={r"/api/*": {"origins": origins, "supports_credentials": True}})

    @app.after_request
    def expose_csrf_token(response):
        if (session.get("admin_user") or session.get("customer_user")) and session.get("csrf_token"):
            response.headers["X-CSRF-Token"] = session["csrf_token"]
        return response

    # Initialize Database Connection
    with app.app_context():
        init_db(app)

    # Register API Blueprints
    app.register_blueprint(api)

    # Error Handlers
    @app.errorhandler(404)
    def handle_not_found(e):
        return jsonify({"success": False, "error": "Endpoint or resource not found"}), 404

    # --------------------------------------------------------
    # Frontend Routes (Single-Origin Serving)
    # --------------------------------------------------------
    @app.route("/")
    def index():
        return redirect("/portals")

    @app.route("/parking")
    def customer_parking_page():
        return send_from_directory(str(FRONTEND_DIR), "index.html")

    @app.route("/admin")
    @app.route("/admin/")
    def admin_page():
        if not session.get("admin_user"):
            if session.get("customer_user"):
                return redirect("/customer")
            return redirect("/admin/login")
        return send_from_directory(str(FRONTEND_DIR), "admin.html")

    @app.route("/admin/login")
    def admin_login_page():
        if session.get("admin_user"):
            return redirect("/admin")
        if session.get("customer_user"):
            return redirect("/customer")
        return send_from_directory(str(FRONTEND_DIR), "admin-login.html")

    @app.route("/admin.html")
    def legacy_admin_page():
        if not session.get("admin_user"):
            if session.get("customer_user"):
                return redirect("/customer")
            return redirect("/admin/login")
        return redirect("/admin")

    @app.route("/admin/dashboard")
    @app.route("/admin/dashboard/")
    @app.route("/admin/<path:admin_path>")
    def protected_admin_page(admin_path="dashboard"):
        """Keep every admin HTML path behind the Flask session guard."""
        if not session.get("admin_user"):
            if session.get("customer_user"):
                return redirect("/customer")
            return redirect("/admin/login")
        return send_from_directory(str(FRONTEND_DIR), "admin.html")

    @app.route("/admin/scanner")
    @app.route("/admin/scanner/")
    def admin_scanner_page():
        if not session.get("admin_user"):
            if session.get("customer_user"):
                return redirect("/customer")
            return redirect("/admin/login")
        return send_from_directory(str(FRONTEND_DIR), "admin-scanner.html")

    @app.route("/customer")
    @app.route("/customer/")
    def customer_page():
        if session.get("admin_user"):
            return redirect("/admin")
        return send_from_directory(str(FRONTEND_DIR), "customer.html")

    @app.route("/customer/login")
    def customer_login_page():
        if session.get("customer_user"):
            return redirect("/customer")
        if session.get("admin_user"):
            return redirect("/admin")
        return send_from_directory(str(FRONTEND_DIR), "customer-login.html")

    @app.route("/customer/register")
    def customer_register_page():
        if session.get("customer_user"):
            return redirect("/customer")
        if session.get("admin_user"):
            return redirect("/admin")
        return send_from_directory(str(FRONTEND_DIR), "customer-register.html")

    @app.route("/portals")
    @app.route("/portal")
    def portal_selector_page():
        if session.get("admin_user"):
            return redirect("/admin")
        if session.get("customer_user"):
            return redirect("/customer")
        return send_from_directory(str(FRONTEND_DIR), "portals.html")

    @app.route("/<path:filename>")
    def serve_frontend_files(filename):
        target = FRONTEND_DIR / filename
        if target.exists() and target.is_file():
            return send_from_directory(str(FRONTEND_DIR), filename)
        # Try appending .html (e.g. /booking -> booking.html)
        html_target = FRONTEND_DIR / f"{filename}.html"
        if html_target.exists() and html_target.is_file():
            return send_from_directory(str(FRONTEND_DIR), f"{filename}.html")
        return send_from_directory(str(FRONTEND_DIR), "404.html"), 404

    @app.errorhandler(500)
    def handle_server_error(e):
        return jsonify({"success": False, "error": "Internal server error"}), 500

    return app


if __name__ == "__main__":
    app = create_app()
    logger.info("=" * 60)
    logger.info("SMARTPARK - Smart Parking Management System Server Starting")
    logger.info("Target Database: Supabase PostgreSQL")
    logger.info(f"Frontend URL: http://localhost:{Config.PORT}")
    logger.info("=" * 60)

    app.run(
        host="0.0.0.0",
        port=Config.PORT,
        debug=Config.DEBUG,
        use_reloader=False
    )
