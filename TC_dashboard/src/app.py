"""Flask application for health dashboard."""

import logging
import os
from pathlib import Path

from flask import Flask, jsonify, render_template

from src.health_checker import HealthChecker

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Set template and static folders relative to /app (where we run from)
# Set template and static folders - Flask looks relative to where app is defined
# Since app.py is in /app/src/, we need to go up one level to /app/
import os
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app = Flask(__name__, 
            template_folder=os.path.join(base_dir, 'templates'),
            static_folder=os.path.join(base_dir, 'static'))

# Initialize health checker
# Get base path from environment or use default
base_path = os.getenv("DASHBOARD_BASE_PATH", str(Path(__file__).parent.parent.parent))
health_checker = HealthChecker(base_path=base_path)


@app.route("/")
def index():
    """Serve the dashboard HTML page."""
    return render_template("index.html")


@app.route("/api/health")
def get_all_health():
    """Get health status for all services."""
    try:
        health_data = health_checker.check_all_services()
        return jsonify(health_data)
    except Exception as e:
        logger.error(f"Error getting health data: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/health/<service_key>")
def get_service_health(service_key: str):
    """Get health status for a specific service."""
    try:
        health_data = health_checker.check_service_health(service_key)
        return jsonify(health_data)
    except Exception as e:
        logger.error(f"Error getting health for {service_key}: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT", "8080"))
    debug = os.getenv("DASHBOARD_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)

