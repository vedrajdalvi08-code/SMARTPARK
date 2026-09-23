"""
SMARTPARK - Main Application Entrypoint
Flask REST API server and static frontend host.
"""

import os
import sys
import logging
import threading
import time
from pathlib import Path
from flask import Flask, send_from_directory, jsonify, redirect, session
from flask_cors import CORS

from config import Config
from database import init_db
from routes import api
from services import IoTGatewayService

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("smartpark.app")

# Path to frontend assets
BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"

def create_app():
    """Application factory for SMARTPARK."""
    app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")
    app.config.from_object(Config)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = os.getenv("FLASK_ENV", "development").lower() == "production"

    # Enable Cross-Origin Resource Sharing
    # In production the Vercel frontend normally reaches the API through the
    # same-origin /api proxy. Keep direct backend access configurable for
    # local development and ESP32/browser testing.
    allowed_origins = os.getenv("CORS_ORIGINS", "*")
    origins = "*" if allowed_origins.strip() == "*" else [o.strip() for o in allowed_origins.split(",") if o.strip()]
    CORS(app, resources={r"/api/*": {"origins": origins, "supports_credentials": True}})

    # Initialize Database Connection
    with app.app_context():
        init_db(app)

    # Register API Blueprints
    app.register_blueprint(api)

    # --------------------------------------------------------
    # Frontend Routes (Single-Origin Serving)
    # --------------------------------------------------------
    @app.route("/")
    def index():
        return send_from_directory(str(FRONTEND_DIR), "index.html")

    @app.route("/admin")
    def admin_page():
        if not session.get("admin_user"):
            return redirect("/admin/login")
        return send_from_directory(str(FRONTEND_DIR), "admin.html")

    @app.route("/admin/login")
    def admin_login_page():
        if session.get("admin_user"):
            return redirect("/admin")
        return send_from_directory(str(FRONTEND_DIR), "admin-login.html")

    @app.route("/admin.html")
    def legacy_admin_page():
        return redirect("/admin")

    @app.route("/<path:filename>")
    def serve_frontend_files(filename):
        target = FRONTEND_DIR / filename
        if target.exists() and target.is_file():
            return send_from_directory(str(FRONTEND_DIR), filename)
        return send_from_directory(str(FRONTEND_DIR), "index.html")

    # Error Handlers
    @app.errorhandler(404)
    def handle_not_found(e):
        return jsonify({"success": False, "error": "Endpoint or resource not found"}), 404

    @app.errorhandler(500)
    def handle_server_error(e):
        return jsonify({"success": False, "error": "Internal server error"}), 500

    return app


def start_barrier_auto_reset_daemon():
    """
    Background daemon thread to automatically reset opened barrier gates
    back to CLOSED (0 degrees) after 6 seconds, mirroring real-world servo behavior.
    """
    def reset_loop():
        while True:
            try:
                time.sleep(2)
                # Check Entry Barrier
                entry = IoTGatewayService.hardware_state["entry_barrier"]
                if entry["status"] == "OPEN":
                    time.sleep(4)
                    IoTGatewayService.trigger_entry_barrier("CLOSED")
                    IoTGatewayService.set_ir_sensor("IR_ENTRY", False)

                # Check Exit Barrier
                exit_barrier = IoTGatewayService.hardware_state["exit_barrier"]
                if exit_barrier["status"] == "OPEN":
                    time.sleep(4)
                    IoTGatewayService.trigger_exit_barrier("CLOSED")
                    IoTGatewayService.set_ir_sensor("IR_EXIT", False)
            except Exception as e:
                pass

    daemon = threading.Thread(target=reset_loop, daemon=True)
    daemon.start()


if __name__ == "__main__":
    app = create_app()
    logger.info("=" * 60)
    logger.info("SMARTPARK - Smart Parking Management System Server Starting")
    logger.info("Target Database: Supabase PostgreSQL")
    logger.info(f"Frontend URL: http://localhost:{Config.PORT}")
    logger.info("=" * 60)

    # Launch barrier auto-reset background supervisor.
    start_barrier_auto_reset_daemon()

    app.run(
        host="0.0.0.0",
        port=Config.PORT,
        debug=Config.DEBUG,
        use_reloader=False
    )
