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
        exclude_no_reserve = data.get('exclude_no_reserve', True)  # Default to True (exclude)
        
        # Generate email using default form letter template
        email_text = oc_email_generator.generate_email(exclude_no_reserve=exclude_no_reserve)
        
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

