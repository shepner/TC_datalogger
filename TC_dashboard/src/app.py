"""Flask application for health dashboard."""

import json
import logging
import os
from datetime import timedelta
from pathlib import Path
from typing import List

from google.cloud.bigquery import ScalarQueryParameter

from flask import Flask, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from flask_login import LoginManager, current_user, login_required, login_user, logout_user

from src.auth import add_user, delete_user, get_user, list_users, user_count, verify_password
from src.bigquery_client import BigQueryClient
from src.docker_client import DockerClient
from src.health_checker import HealthChecker
from src.oc_email_generator import DEFAULT_EMAIL_PREFACE, OCEmailGenerator
from src.requirements_report import RequirementsReport
from src.trading_dashboard import TradingDashboard

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Server-shared OC assignment configuration
OC_ASSIGNMENT_CONFIG_FILE = Path("/app/logs/oc_assignment_config.json")
OC_ASSIGNMENT_SECTION_STATES_FILE = Path("/app/logs/oc_assignment_section_states.json")


def _default_oc_assignment_config() -> dict:
    return {
        "excluded_oc_names": [],
        "email_preface": DEFAULT_EMAIL_PREFACE,
    }


def load_oc_assignment_config() -> dict:
    """Load saved OC assignment config; fall back to defaults."""
    default_cfg = _default_oc_assignment_config()
    try:
        if OC_ASSIGNMENT_CONFIG_FILE.exists():
            with open(OC_ASSIGNMENT_CONFIG_FILE, "r") as f:
                cfg = json.load(f)
            if isinstance(cfg, dict):
                excluded = cfg.get("excluded_oc_names", default_cfg["excluded_oc_names"])
                preface = cfg.get("email_preface", default_cfg["email_preface"])
                if not isinstance(excluded, list):
                    excluded = default_cfg["excluded_oc_names"]
                excluded = [str(x) for x in excluded if str(x).strip()]
                if not isinstance(preface, str):
                    preface = default_cfg["email_preface"]
                return {
                    "excluded_oc_names": excluded,
                    "email_preface": preface,
                    "is_default": False,
                }
    except Exception as e:
        logger.warning(f"Error reading OC assignment config: {e}", exc_info=True)

    return {
        **default_cfg,
        "is_default": True,
    }


def save_oc_assignment_config(excluded_oc_names: List[str], email_preface: str) -> dict:
    """Validate and persist OC assignment config to disk."""
    if not isinstance(excluded_oc_names, list):
        raise ValueError("excluded_oc_names must be a list")
    excluded = [str(x).strip() for x in excluded_oc_names if str(x).strip()]

    if not isinstance(email_preface, str):
        raise ValueError("email_preface must be a string")

    cfg = {
        "excluded_oc_names": excluded,
        "email_preface": email_preface,
    }
    OC_ASSIGNMENT_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OC_ASSIGNMENT_CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
    return cfg


# Default expanded state for each collapsible section (True = expanded)
DEFAULT_SECTION_STATES = {
    "oc_insights": False,
    "oc_performance": True,
    "assignment_email": True,
    "assignment_email_preface": True,
    "assignment_email_exclude_ocs": True,
    "assignment_email_generated": True,
    "assignment_email_needed_ocs": True,
    "assignment_email_reasons": True,
}


def load_oc_assignment_section_states() -> dict:
    """Load saved section expanded states; fall back to defaults. Persisted under /app/logs (outside container)."""
    try:
        if OC_ASSIGNMENT_SECTION_STATES_FILE.exists():
            with open(OC_ASSIGNMENT_SECTION_STATES_FILE, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                out = dict(DEFAULT_SECTION_STATES)
                for k in DEFAULT_SECTION_STATES:
                    if k in data and isinstance(data[k], bool):
                        out[k] = data[k]
                return out
    except Exception as e:
        logger.warning(f"Error reading section states: {e}", exc_info=True)
    return dict(DEFAULT_SECTION_STATES)


def save_oc_assignment_section_states(states: dict) -> dict:
    """Validate and persist section states to disk (outside container via /app/logs mount)."""
    if not isinstance(states, dict):
        raise ValueError("states must be a dict")
    out = {}
    for k in DEFAULT_SECTION_STATES:
        out[k] = bool(states[k]) if k in states and isinstance(states.get(k), bool) else DEFAULT_SECTION_STATES[k]
    OC_ASSIGNMENT_SECTION_STATES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OC_ASSIGNMENT_SECTION_STATES_FILE, "w") as f:
        json.dump(out, f, indent=2)
    return out


# Set template and static folders relative to /app (where we run from)
# Set template and static folders - Flask looks relative to where app is defined
# Since app.py is in /app/src/, we need to go up one level to /app/
import os
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app = Flask(__name__,
            template_folder=os.path.join(base_dir, 'templates'),
            static_folder=os.path.join(base_dir, 'static'))

# Auth: secret key required for sessions; use env in production
app.config["SECRET_KEY"] = os.getenv("DASHBOARD_SECRET_KEY") or "dev-secret-change-in-production"
app.config["SESSION_PERMANENT"] = True
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=365 * 10)  # ~10 years (indefinite)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = None


@login_manager.user_loader
def load_user(user_id: str):
    return get_user(user_id)


@app.before_request
def make_session_permanent_and_require_auth():
    session.permanent = True
    if request.endpoint == "login":
        return
    if request.path.startswith("/static") or request.path == "/favicon.ico":
        return
    if not current_user.is_authenticated:
        # API calls: return 401 JSON so fetch() can redirect the browser to login
        if request.path.startswith("/api/"):
            return jsonify(login_required=True, login_url=url_for("login", next=request.url)), 401
        return redirect(url_for("login", next=request.url))


@app.route("/login", methods=["GET", "POST"])
def login():
    """Login page; POST with username/password to authenticate. Session is kept indefinitely.
    If no users exist, shows account creation form for first-time setup."""
    if current_user.is_authenticated:
        return redirect(request.args.get("next") or url_for("index"))
    
    # First-time setup: if no users exist, show account creation form
    if user_count() == 0:
        setup_error = None
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            password_confirm = request.form.get("password_confirm") or ""
            if not username or not password:
                setup_error = "Username and password are required."
            elif password != password_confirm:
                setup_error = "Passwords do not match."
            elif len(password) < 6:
                setup_error = "Password must be at least 6 characters."
            else:
                if add_user(username, password):
                    # Auto-login the newly created user
                    user = get_user(username)
                    if user:
                        login_user(user, remember=True)
                        return redirect(url_for("index"))
                else:
                    setup_error = "Failed to create account. Please try again."
        return render_template("login.html", setup_mode=True, error=setup_error)
    
    # Normal login flow
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if not username or not password:
            error = "Username and password are required."
        elif verify_password(username, password):
            user = get_user(username)
            if user:
                login_user(user, remember=True)
                next_url = request.args.get("next") or url_for("index")
                return redirect(next_url)
        if error is None:
            error = "Invalid username or password."
    return render_template("login.html", error=error, next=request.args.get("next"))


@app.route("/logout", methods=["GET", "POST"])
@login_required
def logout():
    """Log out the current user."""
    logout_user()
    return redirect(url_for("login"))


@app.route("/account-management")
@login_required
def account_management():
    """Account management page."""
    return render_template("account_management.html")


@app.route("/api/users", methods=["GET"])
@login_required
def get_users():
    """Get list of all users."""
    users = list_users()
    return jsonify({"users": users})


@app.route("/api/users", methods=["POST"])
@login_required
def create_user():
    """Create a new user."""
    try:
        data = request.get_json() or {}
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        password_confirm = data.get("password_confirm") or ""
        
        if not username:
            return jsonify({"error": "Username is required"}), 400
        if not password:
            return jsonify({"error": "Password is required"}), 400
        if password != password_confirm:
            return jsonify({"error": "Passwords do not match"}), 400
        if len(password) < 6:
            return jsonify({"error": "Password must be at least 6 characters"}), 400
        
        if add_user(username, password):
            return jsonify({"success": True, "message": f"User {username!r} created successfully"})
        else:
            return jsonify({"error": "Failed to create user"}), 500
    except Exception as e:
        logger.error(f"Error creating user: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/users/<username>", methods=["DELETE"])
@login_required
def delete_user_route(username: str):
    """Delete a user."""
    try:
        username = username.strip()
        if not username:
            return jsonify({"error": "Username is required"}), 400
        
        # Prevent deleting the last user
        if user_count() <= 1:
            return jsonify({"error": "Cannot delete the last user"}), 400
        
        if delete_user(username):
            return jsonify({"success": True, "message": f"User {username!r} deleted successfully"})
        else:
            return jsonify({"error": "User not found"}), 404
    except Exception as e:
        logger.error(f"Error deleting user: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/users/<username>/password", methods=["POST"])
@login_required
def change_password(username: str):
    """Change a user's password."""
    try:
        data = request.get_json() or {}
        password = data.get("password") or ""
        password_confirm = data.get("password_confirm") or ""
        
        username = username.strip()
        if not username:
            return jsonify({"error": "Username is required"}), 400
        if not password:
            return jsonify({"error": "Password is required"}), 400
        if password != password_confirm:
            return jsonify({"error": "Passwords do not match"}), 400
        if len(password) < 6:
            return jsonify({"error": "Password must be at least 6 characters"}), 400
        
        # Check if user exists
        if get_user(username) is None:
            return jsonify({"error": "User not found"}), 404
        
        if add_user(username, password):  # add_user updates existing users
            return jsonify({"success": True, "message": f"Password updated for {username!r}"})
        else:
            return jsonify({"error": "Failed to update password"}), 500
    except Exception as e:
        logger.error(f"Error changing password: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# Initialize health checker
# Get base path from environment or use default
base_path = os.getenv("DASHBOARD_BASE_PATH", str(Path(__file__).parent.parent.parent))
health_checker = HealthChecker(base_path=base_path)

# Initialize Docker client
docker_client = DockerClient()

# Initialize BigQuery client and feature modules
try:
    bigquery_client = BigQueryClient()
    oc_email_generator = OCEmailGenerator(bigquery_client)
    trading_dashboard = TradingDashboard(bigquery_client)
    requirements_report = RequirementsReport(bigquery_client)
except Exception as e:
    logger.warning(f"Could not initialize BigQuery client: {e}. Some features may not be available.")
    bigquery_client = None
    oc_email_generator = None
    trading_dashboard = None
    requirements_report = None


@app.route("/")
def index():
    """Serve the dashboard HTML page."""
    return render_template("index.html")


@app.route("/favicon.ico")
def favicon():
    """Serve the favicon file."""
    return send_from_directory(app.static_folder, 'favicon.ico', mimetype='image/vnd.microsoft.icon')


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


@app.route("/oc-assignment")
def oc_assignment():
    """OC assignment email generator page."""
    return render_template("oc_assignment.html")


@app.route("/api/oc-assignment/config", methods=["GET"])
def get_oc_assignment_config():
    """Get saved OC assignment configuration (server-shared)."""
    cfg = load_oc_assignment_config()
    return jsonify(cfg)


@app.route("/api/oc-assignment/config", methods=["POST"])
def set_oc_assignment_config():
    """Save OC assignment configuration (server-shared)."""
    try:
        data = request.get_json() or {}
        excluded_oc_names = data.get("excluded_oc_names", [])
        email_preface = data.get("email_preface", DEFAULT_EMAIL_PREFACE)
        cfg = save_oc_assignment_config(excluded_oc_names, email_preface)
        return jsonify({"success": True, **cfg})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error saving OC assignment config: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/oc-assignment/section-states", methods=["GET"])
def get_oc_assignment_section_states():
    """Get saved collapsible section expanded states (persisted under /app/logs, outside container)."""
    return jsonify(load_oc_assignment_section_states())


@app.route("/api/oc-assignment/section-states", methods=["POST"])
def set_oc_assignment_section_states():
    """Save collapsible section expanded states to disk (outside container)."""
    try:
        data = request.get_json() or {}
        out = save_oc_assignment_section_states(data)
        return jsonify(out)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error saving section states: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/oc-assignment/oc-names", methods=["GET"])
def get_oc_names():
    """Get list of OC names with their (max) difficulty level from historical data."""
    if not oc_email_generator:
        return jsonify({"error": "BigQuery client not available"}), 500
    
    try:
        # Query for all unique OC names with a representative level.
        # We use MAX(difficulty) per name so each name appears once, grouped by highest level seen.
        query = """
        SELECT
          name AS oc_name,
          MAX(SAFE_CAST(difficulty AS INT64)) AS difficulty
        FROM
          `torncity-402423.torn_data.v2_faction_40832_crimes-raw`
        WHERE
          name IS NOT NULL
          AND name != ''
          AND SAFE_CAST(difficulty AS INT64) IS NOT NULL
        GROUP BY
          name
        ORDER BY
          difficulty DESC,
          oc_name ASC
        """
        results = oc_email_generator.bq.execute_query(query)
        ocs = [
            {"oc_name": row.get("oc_name", ""), "difficulty": row.get("difficulty")}
            for row in results
            if row.get("oc_name")
        ]
        oc_names = [o["oc_name"] for o in ocs]
        # Keep backward compatibility for callers expecting `oc_names`.
        return jsonify({"oc_names": oc_names, "ocs": ocs})
    except Exception as e:
        logger.error(f"Error getting OC names: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route("/api/oc-assignment/generate", methods=["POST"])
def generate_oc_email():
    """Generate OC assignment email."""
    if not oc_email_generator:
        return jsonify({"error": "BigQuery client not available"}), 500
    
    try:
        data = request.get_json() or {}
        saved_cfg = load_oc_assignment_config()

        # Allow request overrides (preview without saving)
        excluded_oc_names = data.get("excluded_oc_names", saved_cfg.get("excluded_oc_names", []))
        email_preface = data.get("email_preface", saved_cfg.get("email_preface", DEFAULT_EMAIL_PREFACE))
        
        # Generate email using default form letter template
        result = oc_email_generator.generate_email(
            excluded_oc_names=excluded_oc_names,
            email_preface=email_preface,
        )
        
        # Handle both old format (string) and new format (dict)
        if isinstance(result, dict):
            return jsonify({
                "email_text": result.get('email_text', ''),
                "needed_ocs": result.get('needed_ocs', []),
                "assignment_reasons": result.get('assignment_reasons', {})
            })
        else:
            # Backward compatibility: if it returns a string, wrap it
            return jsonify({
                "email_text": result,
                "needed_ocs": [],
                "assignment_reasons": {}
            })
    except Exception as e:
        logger.error(f"Error generating OC email: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/oc-assignment/performance", methods=["GET"])
def get_oc_performance():
    """Get OC performance by role and level."""
    if not oc_email_generator:
        return jsonify({"error": "BigQuery client not available"}), 500
    
    try:
        days_back = int(request.args.get("days_back", 90))
        
        # Validate and clamp days_back to prevent BigQuery overflow errors
        # BigQuery has limits on TIMESTAMP_SUB, so we cap at 365 days
        if days_back < 7:
            days_back = 7
        elif days_back > 365:
            days_back = 365
        
        performance = oc_email_generator.get_oc_performance_by_role(days_back=days_back)
        
        # Get all faction members to ensure all members appear in the list
        all_members = oc_email_generator.get_all_faction_members()
        all_members_map = {m['member_id']: m for m in all_members}
        
        # Get OC participation counts (30d and 7d)
        oc_counts_30d = oc_email_generator.get_oc_participation_counts_30d()
        oc_counts_7d = oc_email_generator.get_oc_participation_counts_7d()
        
        # Create a map of member_id to counts
        counts_map = {}
        for row in oc_counts_30d:
            member_id = row.get('member_id')
            if member_id:
                counts_map[member_id] = {
                    'oc_count_30d': row.get('oc_count_30d', 0),
                    'oc_count_7d': 0
                }
        
        for row in oc_counts_7d:
            member_id = row.get('member_id')
            if member_id:
                if member_id not in counts_map:
                    counts_map[member_id] = {'oc_count_30d': 0, 'oc_count_7d': 0}
                counts_map[member_id]['oc_count_7d'] = row.get('oc_count_7d', 0)
        
        # Create a set of member_ids that have performance data
        members_with_performance = {record.get('member_id') for record in performance if record.get('member_id')}
        
        # Add placeholder records for members without OC history so they appear in the list
        for member_id, member_info in all_members_map.items():
            if member_id not in members_with_performance:
                # Create a placeholder record so the member appears in the list
                placeholder_record = {
                    'member_id': member_id,
                    'member_name': member_info.get('member_name', 'Unknown'),
                    'is_in_oc': member_info.get('is_in_oc', False),
                    'days_in_faction': member_info.get('days_in_faction', 0),
                    'difficulty': None,
                    'oc_name': None,
                    'position': None,
                    'position_id': None,
                    'checkpoint_pass_rate': None,
                    'status': None,
                    'crime_id': None,
                    'executed_at': None,
                    'oc_count_30d': counts_map.get(member_id, {}).get('oc_count_30d', 0),
                    'oc_count_7d': counts_map.get(member_id, {}).get('oc_count_7d', 0),
                    'max_recommended_oc': 1  # Default to Level 1 for members with no OC history
                }
                performance.append(placeholder_record)
        
        # Calculate max recommended OC level for each member
        # Max recommended = highest difficulty where member has checkpoint_pass_rate between 80-90
        # If member has 90+ at their highest level, recommend next level higher
        # If member has no position with checkpoint_pass_rate >= 80, set to Level 1
        member_max_oc = {}  # member_name -> max_difficulty (80-90 range)
        member_has_80_plus = {}  # member_name -> bool (has any position with >= 80)
        member_highest_level = {}  # member_name -> highest difficulty they've attempted
        member_highest_level_rate = {}  # member_name -> checkpoint_pass_rate at highest level
        
        # First pass: track highest level and rates for each member
        for record in performance:
            member_name = record.get('member_name')
            difficulty_raw = record.get('difficulty') or record.get('oc_level')
            checkpoint_rate = record.get('checkpoint_pass_rate', 0)
            
            if not member_name or difficulty_raw is None:
                continue
            
            # Ensure difficulty is an integer
            try:
                difficulty = int(difficulty_raw)
            except (ValueError, TypeError):
                continue
            
            # Track highest level attempted
            if member_name not in member_highest_level:
                member_highest_level[member_name] = difficulty
                member_highest_level_rate[member_name] = checkpoint_rate
            else:
                if difficulty > member_highest_level[member_name]:
                    member_highest_level[member_name] = difficulty
                    member_highest_level_rate[member_name] = checkpoint_rate
                elif difficulty == member_highest_level[member_name]:
                    # Keep the highest checkpoint_rate for this level
                    if checkpoint_rate > member_highest_level_rate[member_name]:
                        member_highest_level_rate[member_name] = checkpoint_rate
            
            # Check if member has any position with >= 80
            if checkpoint_rate >= 80:
                member_has_80_plus[member_name] = True
        
        # Second pass: track best rates per level for each member
        member_level_rates = {}  # member_name -> {level: best_rate}
        
        for record in performance:
            member_name = record.get('member_name')
            difficulty_raw = record.get('difficulty') or record.get('oc_level')
            checkpoint_rate_raw = record.get('checkpoint_pass_rate')
            
            if not member_name or difficulty_raw is None or checkpoint_rate_raw is None:
                continue
            
            # Ensure difficulty is an integer
            try:
                difficulty = int(difficulty_raw)
            except (ValueError, TypeError):
                continue
            
            # Ensure checkpoint_rate is a number (handle both 0-100 and 0.0-1.0 formats)
            try:
                checkpoint_rate = float(checkpoint_rate_raw)
                # If rate is between 0 and 1, assume it's a decimal and convert to percentage
                if 0 <= checkpoint_rate <= 1:
                    checkpoint_rate = checkpoint_rate * 100
            except (ValueError, TypeError):
                continue
            
            # Track best rate per level
            if member_name not in member_level_rates:
                member_level_rates[member_name] = {}
            if difficulty not in member_level_rates[member_name]:
                member_level_rates[member_name][difficulty] = checkpoint_rate
            else:
                # Keep the best (highest) rate for this level
                if checkpoint_rate > member_level_rates[member_name][difficulty]:
                    member_level_rates[member_name][difficulty] = checkpoint_rate
        
        # Third pass: determine max OC based on best rates per level
        # Prefer highest level with 80-90 range, but if higher level has borderline rate (80-82)
        # and lower level has good rate (85+), prefer the lower level
        for member_name, level_rates in member_level_rates.items():
            # Debug: Log for specific members
            if member_name in ['Acnar', 'makers_mark_man', 'Raptor_RSA', 'Ti12', 'Zero_pl']:
                logger.info(f"Processing {member_name} with level_rates: {level_rates}")
            # Find all levels with rates in 80-90 range, sorted by level (descending)
            valid_levels = []
            for level, rate in level_rates.items():
                # Ensure rate is a number and in percentage format (0-100)
                try:
                    rate_num = float(rate)
                    # If rate is between 0 and 1, assume it's a decimal and convert to percentage
                    if 0 <= rate_num <= 1:
                        rate_num = rate_num * 100
                    
                    if 80 <= rate_num <= 90:
                        valid_levels.append((level, rate_num))
                except (ValueError, TypeError):
                    continue
            
            if not valid_levels:
                continue
            
            # Sort by level descending
            valid_levels.sort(key=lambda x: x[0], reverse=True)
            
            # If highest level has borderline rate (80-82) and we have a good rate (85+) at a lower level, use lower
            highest_level, highest_rate = valid_levels[0]
            
            if highest_rate <= 82:
                # Check if there's a lower level with a good rate (85+)
                for level, rate in valid_levels[1:]:
                    if rate >= 85:
                        # Prefer this lower level with good rate
                        member_max_oc[member_name] = level
                        break
                else:
                    # No better lower level, use highest
                    member_max_oc[member_name] = highest_level
            else:
                # Highest level has good rate (83+), use it
                # This ensures members with 83-89% at Level 6 get Max OC = 6
                member_max_oc[member_name] = highest_level
                # Debug: Log for specific members
                if member_name in ['Acnar', 'makers_mark_man', 'Raptor_RSA', 'Ti12', 'Zero_pl']:
                    logger.info(f"  -> Added {member_name} to member_max_oc with value {highest_level}")
        
        # Debug: Log member_max_oc for troubleshooting
        logger.info(f"member_max_oc calculated for {len(member_max_oc)} members")
        # Log specific members from screenshot to debug
        test_members = ['Acnar', 'makers_mark_man', 'Raptor_RSA', 'Ti12', 'Zero_pl']
        for member in test_members:
            if member in member_max_oc:
                logger.info(f"{member} -> member_max_oc = {member_max_oc[member]}")
            elif member in member_level_rates:
                logger.info(f"{member} -> level_rates = {member_level_rates[member]}, but NOT in member_max_oc")
            else:
                logger.info(f"{member} -> NOT in member_level_rates at all")
        
        # Add counts and max recommended OC to performance data
        for record in performance:
            member_id = record.get('member_id')
            member_name = record.get('member_name')
            
            if member_id in counts_map:
                record['oc_count_30d'] = counts_map[member_id]['oc_count_30d']
                record['oc_count_7d'] = counts_map[member_id]['oc_count_7d']
            else:
                record['oc_count_30d'] = 0
                record['oc_count_7d'] = 0
            
            # Add max recommended OC
            # Priority: 1) 90+ at highest level (recommend levels based on rate), 2) 80-90 range, 3) Level 1 if no 80+
            if (member_name in member_highest_level_rate and 
                member_highest_level_rate[member_name] >= 90):
                # Member has 90+ at their highest level, recommend levels higher based on rate
                # 90-93%: +1 level (likely to have 80%+ at next level)
                # 94%+: +2 levels (very likely to jump multiple levels)
                highest_rate = member_highest_level_rate[member_name]
                highest_level = member_highest_level[member_name]
                
                if highest_rate >= 94:
                    # 94%+ can jump 2 levels
                    record['max_recommended_oc'] = highest_level + 2
                else:
                    # 90-93% can jump 1 level
                    record['max_recommended_oc'] = highest_level + 1
            elif member_name in member_max_oc:
                # Member has position in 80-90 range, use that
                # This should include members with 83-89% at Level 6, giving them Max OC = 6
                record['max_recommended_oc'] = member_max_oc[member_name]
                # Debug: Log for specific members
                if member_name in ['Acnar', 'makers_mark_man', 'Raptor_RSA', 'Ti12', 'Zero_pl']:
                    logger.info(f"Record for {member_name}: Set max_recommended_oc = {member_max_oc[member_name]}")
            elif member_name in member_has_80_plus:
                # Member has >= 80 but not in 80-90 range and not 90+ at highest level
                # This means they have > 90% somewhere, but not at their highest level
                # Check if they have 90+ at a level below their highest
                # If so, recommend next level from that point
                if member_name in member_level_rates:
                    # Find highest level with 90+ rate
                    levels_with_90_plus = [(l, r) for l, r in member_level_rates[member_name].items() if r >= 90]
                    if levels_with_90_plus:
                        levels_with_90_plus.sort(key=lambda x: x[0], reverse=True)
                        best_level, best_rate = levels_with_90_plus[0]
                        if best_rate >= 94:
                            record['max_recommended_oc'] = best_level + 2
                        else:
                            record['max_recommended_oc'] = best_level + 1
                    else:
                        # Member has >= 80 but > 90, and not 90+ at any level
                        # This shouldn't happen, but if it does, set to Level 1
                        record['max_recommended_oc'] = 1
                else:
                    # Member has >= 80 but no level_rates data (shouldn't happen)
                    record['max_recommended_oc'] = 1
            else:
                # Member has no position with >= 80, automatically set to Level 1
                record['max_recommended_oc'] = 1
        
        # Debug: Log first few records to verify crime_id is present
        if performance and len(performance) > 0:
            first_record = performance[0]
            logger.info(f"First performance record keys: {list(first_record.keys())}")
            logger.info(f"First performance record crime_id: {first_record.get('crime_id')}")
            logger.info(f"First performance record sample: {dict(list(first_record.items())[:10])}")
            # Check a few more records to see if crime_id is consistently null
            crime_id_count = sum(1 for r in performance[:10] if r.get('crime_id') is not None)
            logger.info(f"crime_id present in {crime_id_count} of first 10 records")
        
        # Pre-generate aggregated data for performance
        # Group by member -> difficulty -> oc_name -> position_id
        aggregated_data = {}  # member_name -> {level_ranges: {}, oc_ranges: {}}
        
        for record in performance:
            member_name = record.get('member_name')
            difficulty = record.get('difficulty')
            oc_name = record.get('oc_name')
            position_id = record.get('position_id')
            checkpoint_rate = record.get('checkpoint_pass_rate', 0)
            
            if not member_name or difficulty is None or not oc_name:
                continue
            
            try:
                difficulty = int(difficulty)
                checkpoint_rate = float(checkpoint_rate)
                # Convert 0-1 to 0-100 if needed
                if 0 <= checkpoint_rate <= 1:
                    checkpoint_rate = checkpoint_rate * 100
            except (ValueError, TypeError):
                continue
            
            if member_name not in aggregated_data:
                aggregated_data[member_name] = {
                    'level_ranges': {},  # difficulty -> {min, max, values: []}
                    'oc_ranges': {}  # "difficulty_oc_name" -> {min, max, values: []}
                }
            
            member_agg = aggregated_data[member_name]
            
            # Track level ranges
            if difficulty not in member_agg['level_ranges']:
                member_agg['level_ranges'][difficulty] = {'values': []}
            if checkpoint_rate is not None:
                member_agg['level_ranges'][difficulty]['values'].append(checkpoint_rate)
            
            # Track OC name ranges
            oc_key = f"{difficulty}_{oc_name}"
            if oc_key not in member_agg['oc_ranges']:
                member_agg['oc_ranges'][oc_key] = {'values': []}
            if checkpoint_rate is not None:
                member_agg['oc_ranges'][oc_key]['values'].append(checkpoint_rate)
        
        # Calculate min/max for all ranges
        for member_name, member_agg in aggregated_data.items():
            for difficulty, level_data in member_agg['level_ranges'].items():
                if level_data['values']:
                    level_data['min'] = min(level_data['values'])
                    level_data['max'] = max(level_data['values'])
                    level_data['range'] = f"{int(level_data['min'])}-{int(level_data['max'])}" if level_data['min'] != level_data['max'] else str(int(level_data['min']))
                else:
                    level_data['range'] = None
            
            for oc_key, oc_data in member_agg['oc_ranges'].items():
                if oc_data['values']:
                    oc_data['min'] = min(oc_data['values'])
                    oc_data['max'] = max(oc_data['values'])
                    oc_data['range'] = f"{int(oc_data['min'])}-{int(oc_data['max'])}" if oc_data['min'] != oc_data['max'] else str(int(oc_data['min']))
                else:
                    oc_data['range'] = None
        
        return jsonify({
            "performance": performance,
            "aggregated": aggregated_data
        })
    except Exception as e:
        logger.error(f"Error getting OC performance: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


def _enrich_oc_insights_member_names(rows: list, bq: BigQueryClient) -> None:
    """Add member_name to each latest_by_member entry using faction_members. Modifies rows in place."""
    member_ids = set()
    for row in rows:
        for p in row.get("positions") or []:
            for m in p.get("latest_by_member") or []:
                mid = m.get("member_id")
                if mid is not None:
                    member_ids.add(mid)
    valid_ids = []
    for x in member_ids:
        try:
            valid_ids.append(int(x))
        except (TypeError, ValueError):
            pass
    if not valid_ids:
        return
    proj, ds = bq.project_id, bq.dataset_id
    table = f"`{proj}.{ds}.v2_faction_40832_members-raw`"
    in_clause = ",".join(str(i) for i in valid_ids)
    try:
        member_rows = bq.execute_query(f"SELECT id, name FROM {table} WHERE id IN ({in_clause})")
    except Exception as e:
        logger.warning(f"Could not resolve member names for OC insights: {e}")
        return
    id_to_name = {r["id"]: (r.get("name") or str(r["id"])) for r in member_rows}
    for row in rows:
        pos_list = row.get("positions") or []
        new_pos = []
        for p in pos_list:
            p_dict = dict(p) if not isinstance(p, dict) else p.copy()
            lbm = p_dict.get("latest_by_member") or []
            new_lbm = []
            for m in lbm:
                m_dict = dict(m) if not isinstance(m, dict) else m.copy()
                mid = m_dict.get("member_id")
                m_dict["member_name"] = id_to_name.get(
                    mid, str(mid) if mid is not None else "?"
                )
                new_lbm.append(m_dict)
            p_dict["latest_by_member"] = new_lbm
            new_pos.append(p_dict)
        row["positions"] = new_pos


@app.route("/api/oc-insights", methods=["GET"])
def get_oc_insights():
    """
    GET /api/oc-insights?days_back=<int|empty>
    - days_back missing/empty: read from oc_historical_insights_snapshot.
    - days_back provided: run the same aggregation with ready_at filter for last N days (on-the-fly).
    """
    if not bigquery_client:
        return jsonify({"error": "BigQuery client not available"}), 500
    raw = request.args.get("days_back", "").strip()
    if raw == "":
        try:
            table = f"`{bigquery_client.project_id}.{bigquery_client.dataset_id}.oc_historical_insights_snapshot`"
            rows = bigquery_client.execute_query(f"SELECT * FROM {table} ORDER BY oc_rank, oc_name")
            _enrich_oc_insights_member_names(rows, bigquery_client)
            return jsonify({"rows": rows, "window_days": None})
        except Exception as e:
            logger.warning(f"OC insights snapshot read failed: {e}")
            return jsonify({"error": str(e), "rows": []}), 200
    try:
        days_back = int(raw)
        if days_back < 1:
            days_back = 1
        elif days_back > 365:
            days_back = 365
    except ValueError:
        return jsonify({"error": "days_back must be an integer"}), 400
    for base in [Path("/app/sql_queries"), Path(__file__).resolve().parent.parent.parent / "sql_queries"]:
        p = base / "oc_historical_insights_snapshot.sql"
        if p.exists():
            sql = p.read_text().strip().rstrip(";")
            break
    else:
        return jsonify({"error": "oc_historical_insights_snapshot.sql not found"}), 500
    try:
        rows = bigquery_client.execute_query(
            sql,
            query_parameters=[ScalarQueryParameter("window_days_back", "INT64", days_back)],
        )
        _enrich_oc_insights_member_names(rows, bigquery_client)
        return jsonify({"rows": rows, "window_days": days_back})
    except Exception as e:
        logger.error(f"OC insights compute failed: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


def _find_sql_path(filename: str) -> Path | None:
    for base in [Path("/app/sql_queries"), Path(__file__).resolve().parent.parent.parent / "sql_queries"]:
        p = base / filename
        if p.exists():
            return p
    return None


@app.route("/api/oc-insights/refresh", methods=["POST"])
def refresh_oc_insights():
    """Force rebuild of oc_historical_insights_snapshot and oc_difficulty_rankings from source
    tables (v2_faction_40832_crimes-raw, v2_torn_items-raw) in BigQuery."""
    if not bigquery_client:
        return jsonify({"error": "BigQuery client not available"}), 500
    
    # Prevent destructive operations in test/local mode to avoid disrupting production
    dashboard_mode = os.getenv("DASHBOARD_MODE", "production").lower()
    if dashboard_mode in ("test", "local"):
        return jsonify({
            "error": f"OC insights refresh disabled in {dashboard_mode} mode to prevent disrupting production tables. Set DASHBOARD_MODE=production to enable."
        }), 403
    
    snap_path = _find_sql_path("oc_historical_insights_snapshot.sql")
    if not snap_path:
        return jsonify({"error": "oc_historical_insights_snapshot.sql not found"}), 500
    rank_path = _find_sql_path("oc_difficulty_rankings.sql")
    if not rank_path:
        return jsonify({"error": "oc_difficulty_rankings.sql not found"}), 500
    proj = bigquery_client.project_id
    ds = bigquery_client.dataset_id
    try:
        snap_sql = snap_path.read_text().strip().rstrip(";")
        create_snap = f"CREATE OR REPLACE TABLE `{proj}.{ds}.oc_historical_insights_snapshot` AS (\n{snap_sql}\n)"
        bigquery_client.execute_query(
            create_snap,
            query_parameters=[ScalarQueryParameter("window_days_back", "INT64", None)],
        )
        rank_sql = rank_path.read_text().replace(
            "torncity-402423.torn_data.oc_historical_insights_snapshot",
            f"{proj}.{ds}.oc_historical_insights_snapshot",
        )
        create_rank = f"CREATE OR REPLACE TABLE `{proj}.{ds}.oc_difficulty_rankings` AS\n{rank_sql}"
        bigquery_client.execute_query(create_rank)
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"OC insights refresh failed: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/trading")
def trading():
    """Trading items dashboard page."""
    return render_template("trading.html")


@app.route("/api/trading/pending", methods=["GET"])
def get_pending_trades():
    """Get pending trades."""
    if not trading_dashboard:
        return jsonify({"error": "BigQuery client not available"}), 500
    
    try:
        days_back = int(request.args.get("days_back", 30))
        member_filter = request.args.get("member")
        grouped = request.args.get("grouped", "true").lower() == "true"
        show_paid = request.args.get("show_paid", "false").lower() == "true"
        
        if show_paid:
            if grouped:
                trades = trading_dashboard.get_paid_trades_by_member(
                    days_back=days_back,
                    member_filter=member_filter
                )
            else:
                trades = trading_dashboard.get_paid_trades(
                    days_back=days_back,
                    member_filter=member_filter
                )
        else:
            if grouped:
                trades = trading_dashboard.get_pending_trades_by_member(
                    days_back=days_back,
                    member_filter=member_filter
                )
            else:
                trades = trading_dashboard.get_pending_trades(
                    days_back=days_back,
                    member_filter=member_filter
                )
        
        return jsonify({"trades": trades, "grouped": grouped, "paid": show_paid})
    except Exception as e:
        logger.error(f"Error getting trades: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/trading/mark-paid", methods=["POST"])
def mark_trade_paid():
    """Mark a trade as paid."""
    if not trading_dashboard:
        return jsonify({"error": "BigQuery client not available"}), 500
    
    try:
        data = request.get_json()
        event_id = data.get("event_id")
        trade = data.get("trade")  # Optional trade data
        
        if not event_id:
            return jsonify({"error": "event_id required"}), 400
        
        trading_dashboard.mark_as_paid(event_id, trade)
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error marking trade as paid: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/trading/unmark-paid", methods=["POST"])
def unmark_trade_paid():
    """Unmark a trade as paid."""
    if not trading_dashboard:
        return jsonify({"error": "BigQuery client not available"}), 500
    
    try:
        data = request.get_json()
        event_id = data.get("event_id")
        
        if not event_id:
            return jsonify({"error": "event_id required"}), 400
        
        trading_dashboard.unmark_as_paid(event_id)
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error unmarking trade as paid: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/trading/validate-paid", methods=["POST"])
def validate_paid_trades():
    """Validate that trades were marked as paid in the database."""
    if not trading_dashboard:
        return jsonify({"error": "BigQuery client not available"}), 500
    
    try:
        data = request.get_json()
        event_ids = data.get("event_ids", [])
        
        if not event_ids:
            return jsonify({"error": "event_ids array required"}), 400
        
        if not isinstance(event_ids, list):
            return jsonify({"error": "event_ids must be an array"}), 400
        
        validation_result = trading_dashboard.validate_paid_trades(event_ids)
        return jsonify(validation_result)
    except Exception as e:
        logger.error(f"Error validating paid trades: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/trading/chat-message", methods=["POST"])
def get_chat_message():
    """Get formatted chat message for a trade."""
    if not trading_dashboard:
        return jsonify({"error": "BigQuery client not available"}), 500
    
    try:
        data = request.get_json()
        trade = data.get("trade")
        
        if not trade:
            return jsonify({"error": "trade data required"}), 400
        
        message = trading_dashboard.format_chat_message(trade)
        return jsonify({"message": message})
    except Exception as e:
        logger.error(f"Error formatting chat message: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/trading/raw-events", methods=["GET"])
def get_raw_events():
    """Get raw event logs for a user."""
    if not trading_dashboard:
        return jsonify({"error": "BigQuery client not available"}), 500
    
    try:
        user_name = request.args.get("user_name")
        days_back = int(request.args.get("days_back", 30))
        
        if not user_name:
            return jsonify({"error": "user_name required"}), 400
        
        events = trading_dashboard.get_raw_events_for_user(
            user_name=user_name,
            days_back=days_back
        )
        
        return jsonify({"events": events})
    except Exception as e:
        logger.error(f"Error getting raw events: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/trading/max-days-back", methods=["GET"])
def get_max_days_back():
    """Get maximum days back available in the events table."""
    if not trading_dashboard:
        return jsonify({"error": "BigQuery client not available"}), 500
    
    try:
        max_days = trading_dashboard.get_max_days_back()
        return jsonify({"max_days_back": max_days})
    except Exception as e:
        logger.error(f"Error getting max days back: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/trading/member-names", methods=["GET"])
def get_member_names():
    """Get list of unique member names who have trades available for viewing."""
    if not trading_dashboard:
        return jsonify({"error": "BigQuery client not available"}), 500
    
    try:
        days_back = int(request.args.get("days_back", 30))
        show_paid = request.args.get("show_paid", "false").lower() == "true"
        member_names = trading_dashboard.get_member_names(days_back=days_back, show_paid=show_paid)
        return jsonify({"member_names": member_names})
    except Exception as e:
        logger.error(f"Error getting member names: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/requirements")
def requirements():
    """Faction requirements report page."""
    return render_template("requirements.html")


@app.route("/api/requirements/report", methods=["GET"])
def get_requirements_report():
    """Get faction requirements report."""
    if not requirements_report:
        return jsonify({"error": "BigQuery client not available"}), 500
    
    try:
        report = requirements_report.get_requirements_report()
        actions = requirements_report.generate_action_summary()
        
        return jsonify({
            "report": report,
            "actions": actions
        })
    except Exception as e:
        logger.error(f"Error getting requirements report: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/requirements/role-hierarchy", methods=["GET"])
def get_role_hierarchy():
    """Get saved role hierarchy configuration."""
    ROLE_HIERARCHY_FILE = Path("/app/logs/role_hierarchy.json")
    DEFAULT_HIERARCHY = [
        'Member 1',
        'Member 2',
        'Member 3',
        'Lucky',
        'Mgt 1',
        'Mgt 2',
        'Mgt 3',
        'Co-Leader',
        'Leader'
    ]
    
    # Convert default to new format (all promotable by default)
    DEFAULT_HIERARCHY_OBJ = [{"role": r, "promotable": True} for r in DEFAULT_HIERARCHY]
    
    try:
        if ROLE_HIERARCHY_FILE.exists():
            with open(ROLE_HIERARCHY_FILE, 'r') as f:
                hierarchy = json.load(f)
                if isinstance(hierarchy, list) and len(hierarchy) > 0:
                    # Handle both old format (array of strings) and new format (array of objects)
                    if isinstance(hierarchy[0], str):
                        # Old format - convert to new format
                        hierarchy = [{"role": r, "promotable": True} for r in hierarchy]
                    elif isinstance(hierarchy[0], dict) and 'role' in hierarchy[0]:
                        # New format - ensure all have promotable flag
                        hierarchy = [{"role": r.get("role", r), "promotable": r.get("promotable", True)} for r in hierarchy]
                    return jsonify({
                        "hierarchy": hierarchy,
                        "is_default": False
                    })
        
        # Return default if file doesn't exist or is invalid
        return jsonify({
            "hierarchy": DEFAULT_HIERARCHY_OBJ,
            "is_default": True
        })
    except Exception as e:
        logger.error(f"Error reading role hierarchy: {e}", exc_info=True)
        return jsonify({
            "hierarchy": DEFAULT_HIERARCHY_OBJ,
            "is_default": True,
            "error": str(e)
        })


@app.route("/api/requirements/role-hierarchy", methods=["POST"])
def save_role_hierarchy():
    """Save role hierarchy configuration."""
    ROLE_HIERARCHY_FILE = Path("/app/logs/role_hierarchy.json")
    
    try:
        data = request.get_json()
        if not data or 'hierarchy' not in data:
            return jsonify({"error": "Missing 'hierarchy' in request body"}), 400
        
        hierarchy = data['hierarchy']
        if not isinstance(hierarchy, list):
            return jsonify({"error": "Hierarchy must be a list"}), 400
        
        if len(hierarchy) == 0:
            return jsonify({"error": "Hierarchy cannot be empty"}), 400
        
        # Validate format - can be array of strings (old format) or array of objects (new format)
        validated_hierarchy = []
        for item in hierarchy:
            if isinstance(item, str):
                # Old format - convert to new format
                validated_hierarchy.append({"role": item.strip(), "promotable": True})
            elif isinstance(item, dict) and 'role' in item:
                # New format
                role = item.get('role', '').strip()
                if not role:
                    return jsonify({"error": "All hierarchy items must have non-empty role names"}), 400
                validated_hierarchy.append({
                    "role": role,
                    "promotable": item.get("promotable", True)
                })
            else:
                return jsonify({"error": "Invalid hierarchy format - items must be strings or objects with 'role' field"}), 400
        
        # Save to file
        ROLE_HIERARCHY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ROLE_HIERARCHY_FILE, 'w') as f:
            json.dump(validated_hierarchy, f, indent=2)
        
        logger.info(f"Role hierarchy saved: {validated_hierarchy}")
        return jsonify({
            "success": True,
            "hierarchy": validated_hierarchy
        })
    except Exception as e:
        logger.error(f"Error saving role hierarchy: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/data-pull/trigger", methods=["POST"])
def trigger_data_pull():
    """Trigger data pull from TC API for specified services."""
    if not docker_client.is_available():
        return jsonify({"error": "Docker client not available"}), 500
    
    from datetime import datetime, timedelta
    from pathlib import Path
    import json
    
    # Get requested services from request body, default to OC services for backward compatibility
    request_data = request.get_json() or {}
    requested_services = request_data.get("services", ["faction_crimes", "faction_members"])
    
    # Rate limiting: faction_crimes can only be pulled once every 30 minutes
    LAST_PULL_FILE = Path("/app/logs/last_data_pull.json")
    COOLDOWN_MINUTES = 30
    
    # Load last pull timestamps
    last_pulls = {}
    if LAST_PULL_FILE.exists():
        try:
            with open(LAST_PULL_FILE, 'r') as f:
                last_pulls = json.load(f)
        except Exception as e:
            logger.warning(f"Could not read last pull file: {e}")
    
    # Check if faction_crimes was pulled recently (only if it's in the requested services)
    now = datetime.now()
    last_crimes_pull_str = last_pulls.get("faction_crimes")
    skip_crimes = False
    time_until_next = None
    
    if "faction_crimes" in requested_services and last_crimes_pull_str:
        try:
            last_crimes_pull = datetime.fromisoformat(last_crimes_pull_str)
            time_since_pull = now - last_crimes_pull
            if time_since_pull < timedelta(minutes=COOLDOWN_MINUTES):
                skip_crimes = True
                remaining_seconds = (timedelta(minutes=COOLDOWN_MINUTES) - time_since_pull).total_seconds()
                time_until_next = int(remaining_seconds / 60)  # minutes
        except Exception as e:
            logger.warning(f"Could not parse last crimes pull time: {e}")
    
    # Service definitions
    SERVICE_DEFINITIONS = {
        "faction_crimes": {
            "name": "faction_crimes",
            "container": "tc-faction-crimes-pipeline",
            "command": ["python", "-m", "src.main"]
        },
        "faction_members": {
            "name": "faction_members",
            "container": "tc-faction-members-pipeline",
            "command": ["python", "-m", "src.main"]
        },
        "user_events": {
            "name": "user_events",
            "container": "tc-user-events-pipeline",
            "command": ["python", "-m", "src.main"]
        },
        "items": {
            "name": "items",
            "container": "tc-items-pipeline",
            "command": ["python", "-m", "src.main"]
        }
    }
    
    # Build list of services to trigger based on requested services
    services = []
    for service_key in requested_services:
        if service_key in SERVICE_DEFINITIONS:
            # Skip faction_crimes if rate limited
            if service_key == "faction_crimes" and skip_crimes:
                logger.info(f"Skipping faction_crimes pull - last pull was less than {COOLDOWN_MINUTES} minutes ago")
                continue
            services.append(SERVICE_DEFINITIONS[service_key])
        else:
            logger.warning(f"Unknown service requested: {service_key}")
    
    results = {}
    all_success = True
    skipped_services = []
    
    if skip_crimes:
        skipped_services.append({
            "name": "faction_crimes",
            "reason": f"Rate limited - can only pull once every {COOLDOWN_MINUTES} minutes",
            "time_until_next": time_until_next
        })
        results["faction_crimes"] = {
            "success": False,
            "exit_code": -1,
            "output": "",
            "error": f"Rate limited - please wait {time_until_next} more minute(s) before pulling again"
        }
    
    for service in services:
        logger.info(f"Triggering data pull for {service['name']}...")
        try:
            result = docker_client.execute_in_container(
                service["container"],
                service["command"],
                timeout=600  # 10 minute timeout per service
            )
            # Include more output for debugging, especially if it failed
            output_preview = result["output"] or ""
            if not result["success"]:
                # For failures, include more context (last 2000 chars)
                output_preview = output_preview[-2000:] if len(output_preview) > 2000 else output_preview
            else:
                # For success, just last 500 chars
                output_preview = output_preview[-500:] if len(output_preview) > 500 else output_preview
            
            results[service["name"]] = {
                "success": result["success"],
                "exit_code": result["exit_code"],
                "output": output_preview,
                "error": result["error"]
            }
            if not result["success"]:
                all_success = False
                logger.error(f"Data pull failed for {service['name']}: {result['error']}")
            else:
                logger.info(f"Data pull completed for {service['name']}")
                # Update last pull timestamp for successful pulls
                last_pulls[service["name"]] = now.isoformat()
        except Exception as e:
            logger.error(f"Error triggering data pull for {service['name']}: {e}", exc_info=True)
            results[service["name"]] = {
                "success": False,
                "exit_code": -1,
                "output": "",
                "error": str(e)
            }
            all_success = False
    
    # Save last pull timestamps
    try:
        LAST_PULL_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LAST_PULL_FILE, 'w') as f:
            json.dump(last_pulls, f)
    except Exception as e:
        logger.warning(f"Could not save last pull timestamps: {e}")
    
    return jsonify({
        "success": all_success,
        "results": results,
        "skipped": skipped_services if skipped_services else None
    }), 200 if all_success else 500


if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT", "8080"))
    debug = os.getenv("DASHBOARD_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)

