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

    def get_available_ocs(self) -> List[Dict[str, Any]]:
        """
        Get list of available OCs (status = "Recruiting" or "Planning").

        Returns:
            List of OC dictionaries with id, name, difficulty, status, expiration info
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
            return self.bq.execute_query(query_int64)
        except Exception as e:
            error_str = str(e)
            # If error is about TIMESTAMP casting, return empty list
            # This allows email generation to work without OC prioritization
            if 'TIMESTAMP' in error_str and 'INT64' in error_str:
                logger.warning(f"OC query failed due to mixed timestamp types. Returning empty OC list. Error: {error_str[:200]}")
                return []
            else:
                raise

    def generate_email(
        self,
        instructions: Optional[str] = None,
        max_members_per_oc: int = 1,
    ) -> str:
        """
        Generate OC assignment email text.

        Args:
            instructions: Custom instructions to include (uses default if None)
            max_members_per_oc: Maximum members to assign per OC

        Returns:
            Email text ready for copy/paste
        """
        # Get data
        members = self.get_members_not_in_oc()
        participation = self.get_oc_participation_counts()
        ocs = self.get_available_ocs()

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

        # Assign members
        assignments = {}  # oc_id -> [member_names]
        active_idx = 0
        inactive_idx = 0

        # Assign active members first
        for member in active_members:
            if active_idx < len(ocs_for_active):
                oc = ocs_for_active[active_idx]
                oc_id = oc['oc_id']
                if oc_id not in assignments:
                    assignments[oc_id] = []
                if len(assignments[oc_id]) < max_members_per_oc:
                    assignments[oc_id].append(member['member_name'])
                    active_idx += 1
                    if active_idx >= len(ocs_for_active):
                        active_idx = 0

        # Assign inactive members
        for member in inactive_members:
            if inactive_idx < len(ocs_for_inactive):
                oc = ocs_for_inactive[inactive_idx]
                oc_id = oc['oc_id']
                if oc_id not in assignments:
                    assignments[oc_id] = []
                if len(assignments[oc_id]) < max_members_per_oc:
                    assignments[oc_id].append(member['member_name'])
                    inactive_idx += 1
                    if inactive_idx >= len(ocs_for_inactive):
                        inactive_idx = 0

        # Generate email text
        if instructions is None:
            instructions = """Please join the OC assigned to you below. Make sure you have the required items if needed."""

        email_lines = [instructions, ""]

        # Group assignments by OC
        for oc in ocs:
            oc_id = oc['oc_id']
            if oc_id in assignments and assignments[oc_id]:
                oc_name = oc['oc_name']
                difficulty = oc.get('difficulty', '?')
                oc_url = f"https://www.torn.com/factions.php?step=your#/war/oc/{oc_id}"
                
                email_lines.append(f"{oc_name} (Difficulty {difficulty})")
                email_lines.append(f"URL: {oc_url}")
                email_lines.append(f"Members: {', '.join(assignments[oc_id])}")
                email_lines.append("")

        if not assignments:
            email_lines.append("No members assigned to OCs (all OCs may be full or no suitable assignments found).")

        return "\n".join(email_lines)

    def get_oc_performance_by_role(self, days_back: int = 90) -> List[Dict[str, Any]]:
        """
        Get OC performance data by member, role, and OC level.

        Args:
            days_back: Number of days to look back (default 90)

        Returns:
            List of performance dictionaries
        """
        query_file = "sql_queries/oc_performance_by_role.sql"
        
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
                crime.difficulty AS oc_level,
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
                AND TIMESTAMP_SECONDS(SAFE_CAST(crime.executed_at AS INT64)) >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days_back DAY)
                AND slot.user.progress IS NOT NULL
                AND slot.user.id IN (SELECT member_id FROM current_members)
            )
            SELECT
              os.member_id,
              COALESCE(m.name, CAST(os.member_id AS STRING)) AS member_name,
              os.oc_level,
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
              os.oc_level DESC,
              os.oc_name ASC,
              os.position ASC,
              os.executed_at DESC
            """
            query = query.replace("@days_back", str(days_back))
            return self.bq.execute_query(query)

