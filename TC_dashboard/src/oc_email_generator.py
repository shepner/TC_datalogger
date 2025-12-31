"""OC Assignment Email Generator for Torn City faction management."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from src.bigquery_client import BigQueryClient

logger = logging.getLogger(__name__)


class OCEmailGenerator:
    """Generates OC assignment emails with member prioritization."""

    def __init__(self, bigquery_client: BigQueryClient):
        """
        Initialize OC email generator.

        Args:
            bigquery_client: BigQuery client for querying data
        """
        self.bq = bigquery_client

    def get_members_not_in_oc(self) -> List[Dict[str, Any]]:
        """
        Get list of members who are not currently in an OC.

        Returns:
            List of member dictionaries with id, name, level, last_action_timestamp
        """
        query = """
        SELECT
          id AS member_id,
          name AS member_name,
          level,
          TIMESTAMP_SECONDS(SAFE_CAST(last_action.timestamp AS INT64)) AS last_action_timestamp,
          DATE_DIFF(
            CURRENT_DATE(), 
            DATE(TIMESTAMP_SECONDS(SAFE_CAST(last_action.timestamp AS INT64))), 
            DAY
          ) AS days_inactive
        FROM
          `torncity-402423.torn_data.v2_faction_40832_members-raw`
        WHERE
          is_in_oc = FALSE
          AND last_action.timestamp IS NOT NULL
        ORDER BY
          name ASC
        """
        return self.bq.execute_query(query)

    def get_oc_participation_counts(self) -> Dict[int, Dict[str, Any]]:
        """
        Get OC participation counts for all members (30-day and 7-day).

        Returns:
            Dictionary mapping member_id to participation data
        """
        # Query for OC participation - handle INT64 timestamp fields
        # If there are TIMESTAMP fields, they will cause an error which we'll catch
        query_30d = """
        WITH oc_participations AS (
          SELECT DISTINCT
            slot.user.id AS member_id,
            crime.id AS crime_id
          FROM
            `torncity-402423.torn_data.v2_faction_40832_crimes-raw` AS crime,
            UNNEST(crime.slots) AS slot
          WHERE
            slot.user.id IS NOT NULL
            AND crime.executed_at IS NOT NULL
            AND TIMESTAMP_SECONDS(SAFE_CAST(crime.executed_at AS INT64)) >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
        )
        SELECT
          oc.member_id,
          COUNT(DISTINCT oc.crime_id) AS oc_count_30d
        FROM
          oc_participations AS oc
        GROUP BY
          oc.member_id
        """
        
        try:
            results_30d = self.bq.execute_query(query_30d)
        except Exception as e:
            error_str = str(e)
            if 'TIMESTAMP' in error_str and 'INT64' in error_str:
                logger.warning("OC participation query failed due to mixed timestamp types. Returning empty results.")
                # Return empty results if we can't handle the mixed types
                results_30d = []
            else:
                raise
        
        # Convert to dictionary
        participation = {}
        for row in results_30d:
            participation[row['member_id']] = {
                'oc_count_30d': row.get('oc_count_30d', 0),
                'oc_count_7d': 0  # Will update if we query 7d
            }
        
        return participation

    def get_oc_participation_counts_30d(self) -> List[Dict[str, Any]]:
        """
        Get OC participation counts for all members in the last 30 days.

        Returns:
            List of dictionaries with member_id and oc_count_30d
        """
        query = """
        WITH oc_participations AS (
          SELECT DISTINCT
            slot.user.id AS member_id,
            crime.id AS crime_id
          FROM
            `torncity-402423.torn_data.v2_faction_40832_crimes-raw` AS crime,
            UNNEST(crime.slots) AS slot
          WHERE
            slot.user.id IS NOT NULL
            AND crime.executed_at IS NOT NULL
            AND TIMESTAMP_SECONDS(SAFE_CAST(crime.executed_at AS INT64)) >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
        )
        SELECT
          oc.member_id,
          COUNT(DISTINCT oc.crime_id) AS oc_count_30d
        FROM
          oc_participations AS oc
        GROUP BY
          oc.member_id
        """
        try:
            return self.bq.execute_query(query)
        except Exception as e:
            error_str = str(e)
            if 'TIMESTAMP' in error_str and 'INT64' in error_str:
                logger.warning("OC participation 30d query failed due to mixed timestamp types. Returning empty results.")
                return []
            else:
                raise

    def get_oc_participation_counts_7d(self) -> List[Dict[str, Any]]:
        """
        Get OC participation counts for all members in the last 7 days.

        Returns:
            List of dictionaries with member_id and oc_count_7d
        """
        query = """
        WITH oc_participations AS (
          SELECT DISTINCT
            slot.user.id AS member_id,
            crime.id AS crime_id
          FROM
            `torncity-402423.torn_data.v2_faction_40832_crimes-raw` AS crime,
            UNNEST(crime.slots) AS slot
          WHERE
            slot.user.id IS NOT NULL
            AND crime.executed_at IS NOT NULL
            AND TIMESTAMP_SECONDS(SAFE_CAST(crime.executed_at AS INT64)) >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
        )
        SELECT
          oc.member_id,
          COUNT(DISTINCT oc.crime_id) AS oc_count_7d
        FROM
          oc_participations AS oc
        GROUP BY
          oc.member_id
        """
        try:
            return self.bq.execute_query(query)
        except Exception as e:
            error_str = str(e)
            if 'TIMESTAMP' in error_str and 'INT64' in error_str:
                logger.warning("OC participation 7d query failed due to mixed timestamp types. Returning empty results.")
                return []
            else:
                raise

    def get_member_checkpoint_rates(self, days_back: int = 90) -> Dict[str, Dict[str, float]]:
        """
        Get MAX checkpoint_pass_rate for each member by OC name and position_id.
        
        Args:
            days_back: Number of days to look back (default 90)
            
        Returns:
            Dictionary mapping "member_name" -> "oc_name_position_id" -> max_checkpoint_pass_rate
        """
        query_file = "sql_queries/oc_performance_pivot.sql"
        
        from pathlib import Path
        base_path = Path(__file__).parent.parent.parent
        query_path = base_path / query_file
        
        if query_path.exists():
            query = query_path.read_text()
            query = query.replace("INTERVAL 90 DAY", f"INTERVAL {days_back} DAY")
            results = self.bq.execute_query(query)
        else:
            return {}
        
        # Build dictionary: member_name -> oc_name_position_id -> max_checkpoint_pass_rate
        member_rates = {}
        for record in results:
            member_name = record.get('member_name')
            oc_name = record.get('oc_name')
            position_id = record.get('position_id')
            checkpoint_rate = record.get('checkpoint_pass_rate', 0)
            
            if not member_name or not oc_name or not position_id:
                continue
                
            key = f"{oc_name}_{position_id}"
            
            if member_name not in member_rates:
                member_rates[member_name] = {}
            
            # Keep MAX checkpoint_pass_rate
            if key not in member_rates[member_name] or checkpoint_rate > member_rates[member_name][key]:
                member_rates[member_name][key] = checkpoint_rate
        
        return member_rates

    def get_available_ocs(self) -> List[Dict[str, Any]]:
        """
        Get list of available OCs (status = "Recruiting" or "Planning") with slot details.

        Returns:
            List of OC dictionaries with id, name, difficulty, status, expiration info, and slots
        """
        # Try INT64 query first (most common case)
        query_int64 = """
        SELECT
          id AS oc_id,
          name AS oc_name,
          difficulty,
          status,
          TIMESTAMP_SECONDS(SAFE_CAST(created_at AS INT64)) AS created_at,
          TIMESTAMP_SECONDS(SAFE_CAST(planning_at AS INT64)) AS planning_at,
          TIMESTAMP_SECONDS(SAFE_CAST(expired_at AS INT64)) AS expired_at,
          TIMESTAMP_SECONDS(SAFE_CAST(ready_at AS INT64)) AS ready_at,
          ARRAY_LENGTH(slots) AS total_slots,
          (SELECT COUNT(*) FROM UNNEST(slots) WHERE user.id IS NOT NULL) AS filled_slots,
          ARRAY(
            SELECT STRUCT(
              slot.position_id AS position_id,
              slot.position AS position
            )
            FROM UNNEST(slots) AS slot
          ) AS slot_details
        FROM
          `torncity-402423.torn_data.v2_faction_40832_crimes-raw`
        WHERE
          status IN ('Recruiting', 'Planning')
          AND expired_at IS NOT NULL
          AND TIMESTAMP_SECONDS(SAFE_CAST(expired_at AS INT64)) > CURRENT_TIMESTAMP()
        ORDER BY
          CASE status
            WHEN 'Planning' THEN 1
            WHEN 'Recruiting' THEN 2
            ELSE 3
          END,
          TIMESTAMP_SECONDS(SAFE_CAST(expired_at AS INT64)) ASC
        """
        
        try:
            results = self.bq.execute_query(query_int64)
            # Process slot_details if it's a JSON string
            for oc in results:
                if 'slot_details' in oc and isinstance(oc['slot_details'], str):
                    import json
                    try:
                        oc['slot_details'] = json.loads(oc['slot_details'])
                    except:
                        oc['slot_details'] = []
            return results
        except Exception as e:
            error_str = str(e)
            # If error is about TIMESTAMP casting or slot_details, try simpler query
            if 'TIMESTAMP' in error_str and 'INT64' in error_str or 'slot_details' in error_str:
                logger.warning(f"OC query with slots failed. Trying simpler query. Error: {error_str[:200]}")
                # Fallback to query without slot_details
                query_simple = """
                SELECT
                  id AS oc_id,
                  name AS oc_name,
                  difficulty,
                  status,
                  TIMESTAMP_SECONDS(SAFE_CAST(created_at AS INT64)) AS created_at,
                  TIMESTAMP_SECONDS(SAFE_CAST(planning_at AS INT64)) AS planning_at,
                  TIMESTAMP_SECONDS(SAFE_CAST(expired_at AS INT64)) AS expired_at,
                  TIMESTAMP_SECONDS(SAFE_CAST(ready_at AS INT64)) AS ready_at,
                  ARRAY_LENGTH(slots) AS total_slots,
                  (SELECT COUNT(*) FROM UNNEST(slots) WHERE user.id IS NOT NULL) AS filled_slots
                FROM
                  `torncity-402423.torn_data.v2_faction_40832_crimes-raw`
                WHERE
                  status IN ('Recruiting', 'Planning')
                  AND expired_at IS NOT NULL
                  AND TIMESTAMP_SECONDS(SAFE_CAST(expired_at AS INT64)) > CURRENT_TIMESTAMP()
                ORDER BY
                  CASE status
                    WHEN 'Planning' THEN 1
                    WHEN 'Recruiting' THEN 2
                    ELSE 3
                  END,
                  TIMESTAMP_SECONDS(SAFE_CAST(expired_at AS INT64)) ASC
                """
                try:
                    return self.bq.execute_query(query_simple)
                except Exception as e2:
                    logger.warning(f"Fallback OC query also failed: {e2}")
                    return []
            else:
                raise

    def generate_email(self) -> str:
        """
        Generate OC assignment email text using the form letter template.

        Returns:
            Email text ready for copy/paste
        """
        # Get data
        members = self.get_members_not_in_oc()
        participation = self.get_oc_participation_counts()
        ocs = self.get_available_ocs()
        checkpoint_rates = self.get_member_checkpoint_rates()  # member_name -> oc_name_position_id -> rate

        if not members:
            return "No members available for OC assignment (all members are already in OCs)."

        if not ocs:
            # If no OCs found, check if it's due to a query error
            # Try a simpler query without timestamp filtering as fallback
            try:
                simple_query = """
                SELECT
                  id AS oc_id,
                  name AS oc_name,
                  difficulty,
                  status,
                  TIMESTAMP_SECONDS(SAFE_CAST(created_at AS INT64)) AS created_at,
                  TIMESTAMP_SECONDS(SAFE_CAST(planning_at AS INT64)) AS planning_at,
                  TIMESTAMP_SECONDS(SAFE_CAST(expired_at AS INT64)) AS expired_at,
                  TIMESTAMP_SECONDS(SAFE_CAST(ready_at AS INT64)) AS ready_at,
                  ARRAY_LENGTH(slots) AS total_slots,
                  (SELECT COUNT(*) FROM UNNEST(slots) WHERE user.id IS NOT NULL) AS filled_slots
                FROM
                  `torncity-402423.torn_data.v2_faction_40832_crimes-raw`
                WHERE
                  status IN ('Recruiting', 'Planning')
                  AND expired_at IS NOT NULL
                ORDER BY
                  CASE status
                    WHEN 'Planning' THEN 1
                    WHEN 'Recruiting' THEN 2
                    ELSE 3
                  END
                LIMIT 50
                """
                ocs = self.bq.execute_query(simple_query)
                # Filter in Python for expired_at > now
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc)
                ocs = [oc for oc in ocs if oc.get('expired_at') and oc['expired_at'] > now]
            except Exception as e2:
                logger.warning(f"Fallback OC query also failed: {e2}")
            
            if not ocs:
                return "No available OCs found. Please create new OCs first."

        # Enrich members with participation data and activity status
        for member in members:
            member_id = member['member_id']
            member['oc_count_30d'] = participation.get(member_id, {}).get('oc_count_30d', 0)
            
            # Determine if active (within 24 hours)
            last_action = member.get('last_action_timestamp')
            if last_action:
                try:
                    if isinstance(last_action, str):
                        # Handle ISO format strings
                        if last_action.endswith('Z'):
                            last_action = datetime.fromisoformat(last_action.replace('Z', '+00:00'))
                        else:
                            last_action = datetime.fromisoformat(last_action)
                    elif not isinstance(last_action, datetime):
                        # If it's a datetime-like object from BigQuery
                        last_action = datetime.fromisoformat(str(last_action))
                    
                    # Ensure timezone-aware
                    if last_action.tzinfo is None:
                        last_action = last_action.replace(tzinfo=timezone.utc)
                    
                    now = datetime.now(timezone.utc)
                    hours_since_action = (now - last_action).total_seconds() / 3600
                    member['is_active'] = hours_since_action <= 24
                except Exception as e:
                    logger.warning(f"Could not parse last_action_timestamp: {e}")
                    member['is_active'] = False
            else:
                member['is_active'] = False

        # Sort members by priority:
        # 1. Lower 30-day OC count = higher priority
        # 2. Active members first
        members_sorted = sorted(
            members,
            key=lambda m: (
                m['oc_count_30d'],  # Lower count first
                not m['is_active'],  # Active members first
            )
        )

        # Assign members to OCs
        # Active members → OCs starting soonest (if already has members) or OCs that won't expire for >1 day
        # Inactive members → OCs with longer delay
        active_members = [m for m in members_sorted if m['is_active']]
        inactive_members = [m for m in members_sorted if not m['is_active']]

        # Sort OCs by priority for active members
        # Priority: OCs that already have members and are starting soon, or OCs expiring >1 day away
        now = datetime.now(timezone.utc)
        ocs_for_active = []
        ocs_for_inactive = []

        for oc in ocs:
            expired_at = oc.get('expired_at')
            if expired_at:
                try:
                    if isinstance(expired_at, str):
                        # Handle ISO format strings
                        if expired_at.endswith('Z'):
                            expired_at = datetime.fromisoformat(expired_at.replace('Z', '+00:00'))
                        else:
                            expired_at = datetime.fromisoformat(expired_at)
                    elif not isinstance(expired_at, datetime):
                        # If it's a datetime-like object from BigQuery
                        expired_at = datetime.fromisoformat(str(expired_at))
                    
                    # Ensure timezone-aware
                    if expired_at.tzinfo is None:
                        expired_at = expired_at.replace(tzinfo=timezone.utc)
                    
                    hours_until_expiry = (expired_at - now).total_seconds() / 3600
                except Exception as e:
                    logger.warning(f"Could not parse expired_at: {e}")
                    hours_until_expiry = 999  # Default to far future
                
                # OCs with members already and expiring soon, or OCs expiring >24 hours away
                if (oc.get('filled_slots', 0) > 0 and hours_until_expiry <= 24) or hours_until_expiry > 24:
                    ocs_for_active.append(oc)
                else:
                    ocs_for_inactive.append(oc)
            else:
                ocs_for_active.append(oc)

        # Assign members based on checkpoint_pass_rate (80-90 range)
        # Structure: oc_id -> [{member_name, qualified_positions: [position_id]}]
        assignments = {}  # oc_id -> list of assignment dicts
        members_needing_alternatives = []  # Members with partial qualifications
        members_needing_spawn = []  # Members with no valid OCs
        
        # For each member, find qualified positions (checkpoint_pass_rate 80-90)
        for member in members_sorted:
            member_name = member['member_name']
            member_rates = checkpoint_rates.get(member_name, {})
            
            # Find all qualified OC+position combinations for this member
            qualified_combos = []  # List of {oc_name, position_id, position, difficulty}
            for oc_name_position_key, rate in member_rates.items():
                if 80 <= rate <= 90:  # Valid range
                    parts = oc_name_position_key.rsplit('_', 1)
                    if len(parts) == 2:
                        oc_name, position_id = parts
                        qualified_combos.append({
                            'oc_name': oc_name,
                            'position_id': position_id,
                            'rate': rate
                        })
            
            # Find available OCs that match qualified combinations
            qualified_ocs = []
            for oc in ocs:
                oc_name = oc['oc_name']
                # Check if this OC matches any qualified combo
                matching_combos = [c for c in qualified_combos if c['oc_name'] == oc_name]
                if matching_combos:
                    qualified_ocs.append({
                        'oc': oc,
                        'qualified_positions': [c['position_id'] for c in matching_combos]
                    })
            
            # Assign member to best available OC
            assigned = False
            oc_list = ocs_for_active if member['is_active'] else ocs_for_inactive
            
            for qualified_oc_info in qualified_ocs:
                oc = qualified_oc_info['oc']
                oc_id = oc['oc_id']
                qualified_positions = qualified_oc_info['qualified_positions']
                
                # Check if OC has space and member hasn't been assigned
                if oc_id not in assignments:
                    assignments[oc_id] = []
                
                # Count current assignments for this OC
                current_count = sum(len(a.get('qualified_positions', [])) for a in assignments[oc_id])
                
                if current_count < max_members_per_oc * oc.get('total_slots', 1):
                    assignments[oc_id].append({
                        'member_name': member_name,
                        'qualified_positions': qualified_positions
                    })
                    assigned = True
                    break
            
            # Track members who need alternatives or spawn recommendations
            if not assigned:
                if qualified_combos:
                    # Has qualifications but no available OC
                    members_needing_alternatives.append({
                        'member_name': member_name,
                        'qualified_combos': qualified_combos
                    })
                else:
                    # No valid qualifications at all
                    members_needing_spawn.append(member_name)

        # Generate email text using form letter format
        email_lines = []
        
        # Form letter guidelines - match exact spacing from screenshot
        email_lines.append("OC quick guidelines:")
        email_lines.append("")
        email_lines.append("")
        email_lines.append("If you get bumped from your OC, it is probably because of these 2 reasons:")
        email_lines.append("")
        email_lines.append("- It is expected that all members in Level 2+ OCs will have an 80 to 90 success rate.  Only with Level 1 OCs, participants may have a 0 to 90 success rate.")
        email_lines.append("")
        email_lines.append("- Do not join the Level 5 No Reserve OC.  We cant (yet) complete the 2nd part so its all just a waste of time.")
        email_lines.append("")
        email_lines.append("")
        email_lines.append("If you can do a higher level OC than what was assigned, then go ahead and take it!  (just make sure you are within the 80 to 90 range).  If there arent any OCs of the level you need available, just wait a few hours, Ill spawn more.")
        email_lines.append("")
        email_lines.append("")
        email_lines.append("Finally, dont forget to login daily, quickly join your OC, and be available when your OC is ready!")
        email_lines.append("")
        email_lines.append("")
        email_lines.append("Here are today's OC assignments:")
        email_lines.append("")
        email_lines.append("")

        # Group assignments by level (6, 5, 4, 3, 2, 1), then by OC
        # Structure: level -> [oc_id] -> [assignments]
        assignments_by_level = {}  # level -> {oc_id: [assignments]}
        
        for oc in ocs:
            oc_id = oc['oc_id']
            if oc_id in assignments and assignments[oc_id]:
                difficulty = oc.get('difficulty')
                if difficulty is None:
                    continue
                
                level = int(difficulty)
                if level not in assignments_by_level:
                    assignments_by_level[level] = {}
                
                assignments_by_level[level][oc_id] = assignments[oc_id]

        # Output by level in descending order (6, 5, 4, 3, 2, 1)
        # Only show levels that have assignments
        for level in sorted(assignments_by_level.keys(), reverse=True):
            level_assignments = assignments_by_level[level]
            
            # For each OC at this level
            for oc in ocs:
                oc_id = oc['oc_id']
                if oc_id not in level_assignments:
                    continue
                
                oc_name = oc['oc_name']
                # Use the correct URL format: #/tab=crimes&crimeId=
                oc_url = f"https://www.torn.com/factions.php?step=your#/tab=crimes&crimeId={oc_id}"
                
                # Format: Lv <number> - <OC Name> - <OC URL>
                email_lines.append(f"Lv {level} - {oc_name} - {oc_url}")
                
                # Member list (one per line, no bullets)
                for assignment in level_assignments[oc_id]:
                    member_name = assignment['member_name']
                    # Don't show positions in the email (per user's example)
                    email_lines.append(member_name)
                
                email_lines.append("")

        # Add alternative OC recommendations
        if members_needing_alternatives:
            email_lines.append("")
            email_lines.append("=== MEMBERS NEEDING ALTERNATIVE OCs ===")
            for member_info in members_needing_alternatives:
                member_name = member_info['member_name']
                combos = member_info['qualified_combos']
                oc_names = list(set([c['oc_name'] for c in combos]))
                email_lines.append(f"{member_name}: Needs OC(s) - {', '.join(oc_names)}")
            email_lines.append("")

        # Add spawn recommendations
        if members_needing_spawn:
            email_lines.append("")
            email_lines.append("=== MEMBERS NEEDING NEW OCs TO BE SPAWNED ===")
            email_lines.append("The following members have no valid OC assignments (checkpoint_pass_rate < 80 or > 90 for all positions):")
            email_lines.append(", ".join(members_needing_spawn))
            email_lines.append("")
            email_lines.append("ACTION REQUIRED: Please spawn new OCs for these members.")
            email_lines.append("")

        return "\n".join(email_lines)

    def get_oc_performance_by_role(self, days_back: int = 90) -> List[Dict[str, Any]]:
        """
        Get OC performance data by member, role, and OC level.

        Args:
            days_back: Number of days to look back (default 90)

        Returns:
            List of performance dictionaries
        """
        query_file = "sql_queries/oc_performance_pivot.sql"
        
        # Read query from file
        from pathlib import Path
        base_path = Path(__file__).parent.parent.parent
        query_path = base_path / query_file
        
        if query_path.exists():
            query = query_path.read_text()
            # Replace the days_back parameter
            query = query.replace("INTERVAL 90 DAY", f"INTERVAL {days_back} DAY")
            return self.bq.execute_query(query)
        else:
            # Fallback: inline query
            query = """
            WITH current_members AS (
              SELECT DISTINCT id AS member_id
              FROM
                `torncity-402423.torn_data.v2_faction_40832_members-raw`
            ),
            oc_slots AS (
              SELECT
                crime.id AS crime_id,
                crime.name AS oc_name,
                crime.difficulty,
                TIMESTAMP_SECONDS(SAFE_CAST(crime.executed_at AS INT64)) AS executed_at,
                DATE(TIMESTAMP_SECONDS(SAFE_CAST(crime.executed_at AS INT64))) AS executed_date,
                slot.position AS position,
                slot.position_id,
                slot.user.id AS member_id,
                slot.user.progress AS progress,
                slot.user.outcome AS outcome,
                slot.checkpoint_pass_rate AS checkpoint_pass_rate
              FROM
                `torncity-402423.torn_data.v2_faction_40832_crimes-raw` AS crime,
                UNNEST(crime.slots) AS slot
              WHERE
                slot.user.id IS NOT NULL
                AND crime.executed_at IS NOT NULL
                AND crime.difficulty IS NOT NULL
                AND TIMESTAMP_SECONDS(SAFE_CAST(crime.executed_at AS INT64)) >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days_back DAY)
                AND slot.checkpoint_pass_rate IS NOT NULL
                AND slot.user.id IN (SELECT member_id FROM current_members)
            )
            SELECT
              os.member_id,
              COALESCE(m.name, CAST(os.member_id AS STRING)) AS member_name,
              COALESCE(m.is_in_oc, FALSE) AS is_in_oc,
              m.days_in_faction,
              os.difficulty,
              os.oc_name,
              os.position,
              os.position_id,
              os.executed_at,
              os.executed_date,
              os.progress,
              os.outcome,
              os.checkpoint_pass_rate
            FROM
              oc_slots AS os
            INNER JOIN
              `torncity-402423.torn_data.v2_faction_40832_members-raw` AS m
            ON
              os.member_id = m.id
            ORDER BY
              member_name ASC,
              os.difficulty DESC,
              os.oc_name ASC,
              os.position ASC,
              os.executed_at DESC
            """
            query = query.replace("@days_back", str(days_back))
            return self.bq.execute_query(query)

