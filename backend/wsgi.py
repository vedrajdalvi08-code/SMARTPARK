"""
SMARTPARK - Production WSGI Entry Point
Uses Gunicorn as the production WSGI server.

Run with:
    gunicorn -w 4 -b 0.0.0.0:5000 wsgi:app
"""

import os
import sys
from pathlib import Path

# Ensure backend directory is in Python path
backend_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(backend_dir))

# Set production environment
os.environ.setdefault("FLASK_ENV", "production")
os.environ.setdefault("FLASK_DEBUG", "0")

# Import and create Flask app
from app import create_app, start_barrier_auto_reset_daemon

app = create_app()
start_barrier_auto_reset_daemon()

if __name__ == "__main__":
    # This block is only reached when running via `python wsgi.py` directly
    # Gunicorn uses `wsgi:app` so this path is skipped in production
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)