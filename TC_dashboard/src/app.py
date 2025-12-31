"""Flask application for health dashboard."""

import logging
import os
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from src.bigquery_client import BigQueryClient
from src.health_checker import HealthChecker
from src.oc_email_generator import OCEmailGenerator
from src.requirements_report import RequirementsReport
from src.trading_dashboard import TradingDashboard

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


@app.route("/api/oc-assignment/generate", methods=["POST"])
def generate_oc_email():
    """Generate OC assignment email."""
    if not oc_email_generator:
        return jsonify({"error": "BigQuery client not available"}), 500
    
    try:
        data = request.get_json() or {}
        instructions = data.get("instructions")
        max_members = data.get("max_members_per_oc", 1)
        
        email_text = oc_email_generator.generate_email(
            instructions=instructions,
            max_members_per_oc=max_members
        )
        
        return jsonify({"email_text": email_text})
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
            difficulty = record.get('difficulty') or record.get('oc_level')
            checkpoint_rate = record.get('checkpoint_pass_rate', 0)
            
            if not member_name or not difficulty:
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
        
        # Second pass: find highest difficulty with checkpoint_pass_rate between 80-90
        for record in performance:
            member_name = record.get('member_name')
            difficulty = record.get('difficulty') or record.get('oc_level')
            checkpoint_rate = record.get('checkpoint_pass_rate', 0)
            
            if not member_name or not difficulty:
                continue
            
            # Check if checkpoint_rate is in the valid range (80-90)
            if 80 <= checkpoint_rate <= 90:
                if member_name not in member_max_oc:
                    member_max_oc[member_name] = difficulty
                else:
                    # Keep the highest difficulty
                    if difficulty > member_max_oc[member_name]:
                        member_max_oc[member_name] = difficulty
        
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
            # Priority: 1) 90+ at highest level (recommend multiple levels based on rate), 2) 80-90 range, 3) Level 1 if no 80+
            if (member_name in member_highest_level_rate and 
                member_highest_level_rate[member_name] >= 90):
                # Member has 90+ at their highest level, recommend levels higher based on rate
                # 90-92%: +1 level (likely to have 80%+ at next level)
                # 93-94%: +2 levels (very likely to jump multiple levels)
                # 95%+: +3 levels (exceptional performance, can jump even more)
                highest_rate = member_highest_level_rate[member_name]
                highest_level = member_highest_level[member_name]
                
                if highest_rate >= 95:
                    # 95%+ can jump 3+ levels
                    record['max_recommended_oc'] = highest_level + 3
                elif highest_rate >= 93:
                    # 93-94% can jump 2 levels
                    record['max_recommended_oc'] = highest_level + 2
                else:
                    # 90-92% can jump 1 level
                    record['max_recommended_oc'] = highest_level + 1
            elif member_name in member_max_oc:
                # Member has position in 80-90 range, use that
                record['max_recommended_oc'] = member_max_oc[member_name]
            elif member_name in member_has_80_plus:
                # Member has >= 80 but not in 80-90 range and not 90+ at highest level
                # No recommendation
                record['max_recommended_oc'] = None
            else:
                # Member has no position with >= 80, automatically set to Level 1
                record['max_recommended_oc'] = 1
        
        return jsonify({"performance": performance})
    except Exception as e:
        logger.error(f"Error getting OC performance: {e}", exc_info=True)
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
        
        if grouped:
            trades = trading_dashboard.get_pending_trades_by_member(
                days_back=days_back
            )
        else:
            trades = trading_dashboard.get_pending_trades(
                days_back=days_back,
                member_filter=member_filter
            )
        
        return jsonify({"trades": trades, "grouped": grouped})
    except Exception as e:
        logger.error(f"Error getting pending trades: {e}", exc_info=True)
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


if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT", "8080"))
    debug = os.getenv("DASHBOARD_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)

