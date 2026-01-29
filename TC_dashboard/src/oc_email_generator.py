"""OC Assignment Email Generator for Torn City faction management."""

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from src.bigquery_client import BigQueryClient

logger = logging.getLogger(__name__)

# Default email preface (guidelines + header), preserved from prior hard-coded output.
# This is used unless an explicit `email_preface` is provided.
DEFAULT_EMAIL_PREFACE = (
    "OC quick guidelines:\n"
    "\n"
    "\n"
    "If you get bumped from your OC, it is probably because of these 2 reasons:\n"
    "\n"
    "- It is expected that all members in Level 2+ OCs will have an 80 to 90 success rate.  Only with Level 1 OCs, participants may have a 0 to 90 success rate.\n"
    "\n"
    "- Do not join the Level 5 No Reserve OC.  We cant (yet) complete the 2nd part so its all just a waste of time.\n"
    "\n"
    "\n"
    "If you can do a higher level OC than what was assigned, then go ahead and take it!  (just make sure you are within the 80 to 90 range).  If there arent any OCs of the level you need available, just wait a few hours, Ill spawn more.\n"
    "\n"
    "\n"
    "Finally, dont forget to login daily, quickly join your OC, and be available when your OC is ready!\n"
    "\n"
    "\n"
    "Here are today's OC assignments:\n"
    "\n"
    "\n"
)


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
        Excludes members who have been in the faction for less than 72 hours.

        Returns:
            List of member dictionaries with id, name, level, last_action_timestamp, days_in_faction
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
          ) AS days_inactive,
          days_in_faction
        FROM
          `torncity-402423.torn_data.v2_faction_40832_members-raw`
        WHERE
          is_in_oc = FALSE
          AND last_action.timestamp IS NOT NULL
          AND days_in_faction >= 3
        ORDER BY
          name ASC
        """
        return self.bq.execute_query(query)

    def get_oc_participation_counts(self) -> Dict[int, Dict[str, Any]]:
        """
        Get OC participation counts for all members (30-day and 7-day) in a single query.

        Returns:
            Dictionary mapping member_id to participation data with oc_count_30d and oc_count_7d
        """
        # Combined query for both 30d and 7d participation counts - more efficient than two separate queries
        query = """
        WITH oc_participations AS (
          SELECT DISTINCT
            slot.user.id AS member_id,
            crime.id AS crime_id,
            TIMESTAMP_SECONDS(SAFE_CAST(crime.executed_at AS INT64)) AS executed_at
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
          COUNT(DISTINCT CASE 
            WHEN oc.executed_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
            THEN oc.crime_id
          END) AS oc_count_30d,
          COUNT(DISTINCT CASE 
            WHEN oc.executed_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
            THEN oc.crime_id
          END) AS oc_count_7d
        FROM
          oc_participations AS oc
        GROUP BY
          oc.member_id
        """
        
        try:
            results = self.bq.execute_query(query)
        except Exception as e:
            error_str = str(e)
            if 'TIMESTAMP' in error_str and 'INT64' in error_str:
                logger.warning("OC participation query failed due to mixed timestamp types. Returning empty results.")
                # Return empty results if we can't handle the mixed types
                results = []
            else:
                raise
        
        # Convert to dictionary
        participation = {}
        for row in results:
            participation[row['member_id']] = {
                'oc_count_30d': row.get('oc_count_30d', 0),
                'oc_count_7d': row.get('oc_count_7d', 0)
            }
        
        return participation

    def get_members_with_oc_history(self) -> set:
        """
        Get set of member IDs who have participated in at least one OC (ever).
        
        Returns:
            Set of member_id integers
        """
        query = """
        SELECT DISTINCT
          slot.user.id AS member_id
        FROM
          `torncity-402423.torn_data.v2_faction_40832_crimes-raw` AS crime,
          UNNEST(crime.slots) AS slot
        WHERE
          slot.user.id IS NOT NULL
          AND crime.executed_at IS NOT NULL
        """
        try:
            results = self.bq.execute_query(query)
            return {row['member_id'] for row in results if row.get('member_id')}
        except Exception as e:
            logger.warning(f"Error getting members with OC history: {e}")
            return set()

    def get_oc_historical_for_assignment(self) -> List[Dict[str, Any]]:
        """
        Fetch oc_name, difficulty, oc_rank, oc_checkpoint_rate_score, drop_from_prev from
        oc_historical_insights_snapshot for drop calculation (overcome-the-drop rule).
        On failure returns [] so oc_drop_map stays empty and the rule is not used.
        """
        try:
            table = f"`{self.bq.project_id}.{self.bq.dataset_id}.oc_historical_insights_snapshot`"
            query = (
                f"SELECT oc_name, difficulty, oc_rank, oc_checkpoint_rate_score, drop_from_prev "
                f"FROM {table} ORDER BY oc_rank, oc_name"
            )
            return self.bq.execute_query(query)
        except Exception as e:
            logger.warning(f"get_oc_historical_for_assignment failed: {e}")
            return []

    def get_member_highest_historical_oc(self, member_id_to_name: Dict[int, str]) -> Dict[str, Dict[str, Any]]:
        """
        Get each member's highest checkpoint rate from their highest difficulty rank OC in historical DB.
        
        Returns:
            Dict[member_name -> {
                'oc_name': str,
                'difficulty': int,
                'oc_rank': int,
                'position': str,
                'checkpoint_rate': float
            }]
        """
        try:
            table = f"`{self.bq.project_id}.{self.bq.dataset_id}.oc_historical_insights_snapshot`"
            # Query to get member's highest checkpoint rate from highest difficulty rank OC
            query = f"""
            WITH member_oc_rates AS (
              SELECT
                oc_name,
                oc_rank,
                difficulty,
                position.position,
                member.member_id,
                member.checkpoint_pass_rate
              FROM {table},
              UNNEST(positions) AS position,
              UNNEST(position.latest_by_member) AS member
              WHERE member.member_id IS NOT NULL
            ),
            member_max_rank AS (
              SELECT
                member_id,
                MAX(oc_rank) AS max_oc_rank
              FROM member_oc_rates
              GROUP BY member_id
            ),
            member_best_at_max_rank AS (
              SELECT
                mor.member_id,
                mor.oc_name,
                mor.oc_rank,
                mor.difficulty,
                mor.position,
                mor.checkpoint_pass_rate,
                ROW_NUMBER() OVER (PARTITION BY mor.member_id ORDER BY mor.checkpoint_pass_rate DESC) AS rn
              FROM member_oc_rates mor
              INNER JOIN member_max_rank mmr ON mor.member_id = mmr.member_id AND mor.oc_rank = mmr.max_oc_rank
            )
            SELECT
              member_id,
              oc_name,
              oc_rank,
              difficulty,
              position,
              checkpoint_pass_rate
            FROM member_best_at_max_rank
            WHERE rn = 1
            """
            results = self.bq.execute_query(query)
            
            # Map member_id to member_name
            member_data = {}
            for row in results:
                member_id = row.get('member_id')
                if member_id and member_id in member_id_to_name:
                    member_name = member_id_to_name[member_id]
                    member_data[member_name] = {
                        'oc_name': row.get('oc_name', ''),
                        'difficulty': row.get('difficulty'),
                        'oc_rank': row.get('oc_rank'),
                        'position': row.get('position', ''),
                        'checkpoint_rate': float(row.get('checkpoint_pass_rate', 0))
                    }
            
            return member_data
        except Exception as e:
            logger.warning(f"get_member_highest_historical_oc failed: {e}")
            return {}

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
        else:
            # Fallback: inline query (same as oc_performance_pivot.sql)
            logger.warning(f"OC performance pivot SQL file not found at {query_path}, using inline query")
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
                slot.position AS position,
                slot.position_id,
                slot.user.id AS member_id,
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
              os.oc_name,
              os.difficulty,
              os.position_id,
              os.position,
              COALESCE(m.name, CAST(os.member_id AS STRING)) AS member_name,
              os.member_id,
              COALESCE(m.is_in_oc, FALSE) AS is_in_oc,
              m.days_in_faction,
              os.checkpoint_pass_rate,
              CASE
                WHEN os.outcome = 'Successful' THEN 'Success'
                ELSE 'Failure'
              END AS status,
              os.crime_id,
              os.executed_at
            FROM
              oc_slots AS os
            INNER JOIN
              `torncity-402423.torn_data.v2_faction_40832_members-raw` AS m
            ON
              os.member_id = m.id
            ORDER BY
              os.oc_name ASC,
              os.difficulty DESC,
              os.position_id ASC,
              os.executed_at DESC,
              m.name ASC
            """
            query = query.replace("@days_back", str(days_back))
        
        try:
            results = self.bq.execute_query(query)
            logger.info(f"get_member_checkpoint_rates() query returned {len(results)} records")
        except Exception as e:
            logger.error(f"Error executing get_member_checkpoint_rates query: {e}", exc_info=True)
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
        
        # Log sample of data for debugging
        if member_rates:
            sample_member = list(member_rates.keys())[0]
            logger.info(f"Sample: {sample_member} has {len(member_rates[sample_member])} OC-specific rate entries")
            if "DubZzZ" in member_rates:
                logger.info(f"DubZzZ has {len(member_rates['DubZzZ'])} OC-specific rate entries: {list(member_rates['DubZzZ'].keys())[:5]}")
        
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

    def get_member_max_oc_and_rates(self, days_back: int = 90) -> Dict[str, Dict[str, Any]]:
        """
        Calculate max recommended OC and best rates per difficulty level for each member.
        
        Args:
            days_back: Number of days to look back (default 90)
            
        Returns:
            Dictionary mapping member_name -> {
                'max_recommended_oc': int or None,
                'level_rates': {difficulty: best_checkpoint_rate},
                'has_80_plus': bool,
                'highest_level': int or None,
                'highest_level_rate': float or None,
                'is_jump_recommendation': bool
            }
        """
        performance = self.get_oc_performance_by_role(days_back=days_back)
        
        member_data = {}  # member_name -> {max_recommended_oc, level_rates, has_80_plus}
        member_highest_level = {}  # member_name -> highest difficulty
        member_highest_level_rate = {}  # member_name -> rate at highest level
        
        # First pass: track highest level and rates
        for record in performance:
            member_name = record.get('member_name')
            difficulty_raw = record.get('difficulty') or record.get('oc_level')
            checkpoint_rate_raw = record.get('checkpoint_pass_rate', 0)
            
            if not member_name or difficulty_raw is None:
                continue
            
            try:
                difficulty = int(difficulty_raw)
                checkpoint_rate = float(checkpoint_rate_raw)
                if 0 <= checkpoint_rate <= 1:
                    checkpoint_rate = checkpoint_rate * 100
            except (ValueError, TypeError):
                continue
            
            if member_name not in member_data:
                member_data[member_name] = {
                    'level_rates': {},
                    'has_80_plus': False,
                    'max_recommended_oc': None,
                    'highest_level': None,
                    'highest_level_rate': None,
                    'is_jump_recommendation': False,
                }
            
            # Track best rate per level (but only if it's in the valid 80-90 range)
            # This ensures we only consider rates that meet requirements
            if difficulty not in member_data[member_name]['level_rates']:
                # Only store if in valid range, otherwise store None to indicate no valid rate
                if 80 <= checkpoint_rate <= 90:
                    member_data[member_name]['level_rates'][difficulty] = checkpoint_rate
                else:
                    member_data[member_name]['level_rates'][difficulty] = None
            else:
                current_rate = member_data[member_name]['level_rates'][difficulty]
                # Only update if new rate is better AND in valid range
                if 80 <= checkpoint_rate <= 90:
                    if current_rate is None or checkpoint_rate > current_rate:
                        member_data[member_name]['level_rates'][difficulty] = checkpoint_rate
            
            # Track highest level
            if member_name not in member_highest_level:
                member_highest_level[member_name] = difficulty
                member_highest_level_rate[member_name] = checkpoint_rate
            else:
                if difficulty > member_highest_level[member_name]:
                    member_highest_level[member_name] = difficulty
                    member_highest_level_rate[member_name] = checkpoint_rate
                elif difficulty == member_highest_level[member_name]:
                    if checkpoint_rate > member_highest_level_rate[member_name]:
                        member_highest_level_rate[member_name] = checkpoint_rate
            
            if checkpoint_rate >= 80:
                member_data[member_name]['has_80_plus'] = True
        
        # Second pass: calculate max_recommended_oc
        for member_name, data in member_data.items():
            level_rates = data['level_rates']
            
            # Find levels with rates in 80-90 range
            # Filter out None values (levels where member doesn't meet requirements)
            valid_levels = []
            for level, rate in level_rates.items():
                if rate is not None and 80 <= rate <= 90:
                    valid_levels.append((level, rate))
            
            if not valid_levels:
                # No 80-90 range, check if has 90+ at highest level
                if (member_name in member_highest_level_rate and 
                    member_highest_level_rate[member_name] >= 90):
                    highest_rate = member_highest_level_rate[member_name]
                    highest_level = member_highest_level[member_name]
                    if highest_rate >= 94:
                        data['max_recommended_oc'] = highest_level + 2
                    else:
                        data['max_recommended_oc'] = highest_level + 1
                elif not data['has_80_plus']:
                    # No 80+ at all, recommend Level 1
                    data['max_recommended_oc'] = 1
                continue
            
            # Sort by level descending
            valid_levels.sort(key=lambda x: x[0], reverse=True)
            highest_level, highest_rate = valid_levels[0]
            
            if highest_rate <= 82:
                # Check for lower level with good rate (85+)
                for level, rate in valid_levels[1:]:
                    if rate >= 85:
                        data['max_recommended_oc'] = level
                        break
                else:
                    data['max_recommended_oc'] = highest_level
            else:
                data['max_recommended_oc'] = highest_level

            # Populate metadata used by assignment logic (e.g. jump recommendations)
            highest_lvl = member_highest_level.get(member_name)
            highest_rate = member_highest_level_rate.get(member_name)
            data['highest_level'] = highest_lvl
            data['highest_level_rate'] = highest_rate
            try:
                data['is_jump_recommendation'] = (
                    data.get('max_recommended_oc') is not None
                    and highest_lvl is not None
                    and int(data['max_recommended_oc']) > int(highest_lvl)
                )
            except Exception:
                data['is_jump_recommendation'] = False
        
        return member_data

    def generate_email(
        self,
        excluded_oc_names: Optional[List[str]] = None,
        email_preface: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate OC assignment email text using the form letter template.

        Args:
            excluded_oc_names: List of OC names to exclude from assignments (default: [])
            email_preface: Preface text prepended before the generated assignments block.

        Returns:
            Email text ready for copy/paste
        """
        if excluded_oc_names is None:
            excluded_oc_names = []
        if email_preface is None:
            email_preface = DEFAULT_EMAIL_PREFACE
        
        # Run independent queries in parallel for better performance
        logger.info("Starting parallel query execution...")
        with ThreadPoolExecutor(max_workers=5) as executor:
            # Submit all independent queries
            future_members = executor.submit(self.get_members_not_in_oc)
            future_participation = executor.submit(self.get_oc_participation_counts)
            future_ocs = executor.submit(self.get_available_ocs)
            future_performance = executor.submit(self.get_member_max_oc_and_rates)
            future_oc_rates = executor.submit(self.get_member_checkpoint_rates)
            future_oc_history = executor.submit(self.get_members_with_oc_history)
            future_historical = executor.submit(self.get_oc_historical_for_assignment)
            
            # Wait for all queries to complete
            logger.info("Waiting for parallel queries to complete...")
            members = future_members.result()
            participation = future_participation.result()
            ocs = future_ocs.result()
            member_performance = future_performance.result()  # member_name -> {max_recommended_oc, level_rates, has_80_plus}
            member_oc_rates = future_oc_rates.result()  # member_name -> oc_name_position_id -> rate
            members_with_oc_history = future_oc_history.result()
            historical_rows = future_historical.result()
        
        logger.info("All parallel queries completed")
        
        # Build member_id_to_name mapping for historical OC lookup
        member_id_to_name = {}
        for member in members:
            member_id = member.get('member_id')
            member_name = member.get('member_name')
            if member_id and member_name:
                member_id_to_name[member_id] = member_name
        
        # Get member's highest historical OC performance (highest checkpoint rate from highest difficulty rank)
        member_highest_historical = self.get_member_highest_historical_oc(member_id_to_name)
        logger.info(f"Found highest historical OC data for {len(member_highest_historical)} members")
        
        # Build oc_name -> oc_rank map for displaying difficulty rank
        oc_rank_map = {}
        if historical_rows:
            for row in historical_rows:
                oc_name = (row.get("oc_name") or "").strip().lower()
                oc_rank = row.get("oc_rank")
                if oc_name and oc_rank is not None:
                    oc_rank_map[oc_name] = int(oc_rank)
        
        # Build oc_drop_map from historical insights for "overcome the drop" rule.
        # Use drop_from_prev directly from the database (now stored as a float in percentage points).
        oc_drop_map = {}
        # Also build rank -> drop mapping for cumulative drop calculations
        rank_to_drop_map = {}  # rank -> drop_from_prev for that rank
        if historical_rows:
            # Use drop_from_prev from the database - it's now a float representing percentage points
            for row in historical_rows:
                drop_from_prev = row.get("drop_from_prev")
                oc_name = (row.get("oc_name") or "").strip().lower()
                oc_rank = row.get("oc_rank")
                if oc_name and drop_from_prev is not None:
                    try:
                        drop_value = float(drop_from_prev)
                        # Validate drop is reasonable (0-50 percentage points)
                        # Store it if valid; NULL values (rank 1 OC) are skipped
                        if 0 <= drop_value <= 50:
                            oc_drop_map[oc_name] = drop_value
                            # Also store by rank for cumulative calculations
                            if oc_rank is not None:
                                try:
                                    rank_int = int(oc_rank)
                                    # If multiple OCs have same rank, use average (shouldn't happen, but handle it)
                                    if rank_int not in rank_to_drop_map:
                                        rank_to_drop_map[rank_int] = drop_value
                                    else:
                                        # Average if multiple OCs at same rank (unlikely)
                                        rank_to_drop_map[rank_int] = (rank_to_drop_map[rank_int] + drop_value) / 2.0
                                except (ValueError, TypeError):
                                    pass
                        else:
                            logger.warning(f"Invalid drop_from_prev value {drop_value} for OC {oc_name}, skipping")
                    except (ValueError, TypeError) as e:
                        logger.debug(f"Could not parse drop_from_prev for OC {oc_name}: {e}")
                        pass
            
            # If we didn't get drop_from_prev from DB (backward compatibility), calculate it
            if not oc_drop_map:
                rows_sorted = sorted(
                    historical_rows,
                    key=lambda r: (
                        0 if r.get("oc_rank") is not None else 1,
                        float(r.get("oc_rank") or 0),
                        str(r.get("oc_name") or ""),
                    ),
                )
                prev = None
                for row in rows_sorted:
                    if prev is not None and prev.get("difficulty") == row.get("difficulty"):
                        s_prev = float(prev.get("oc_checkpoint_rate_score") or 0)
                        s_curr = float(row.get("oc_checkpoint_rate_score") or 0)
                        drop = s_prev - s_curr
                        key = (row.get("oc_name") or "").strip().lower()
                        if key:
                            oc_drop_map[key] = drop
                    prev = row
        logger.info(f"oc_drop_map built with {len(oc_drop_map)} entries for overcome-the-drop")
        
        # Debug: Log if member_oc_rates is empty or missing data
        if not member_oc_rates:
            logger.warning("get_member_checkpoint_rates() returned empty dictionary - OC-specific rate checking will not work")
        else:
            logger.info(f"get_member_checkpoint_rates() returned data for {len(member_oc_rates)} members")
            # Check if DubZzZ has data
            if "DubZzZ" in member_oc_rates:
                logger.info(f"DubZzZ has {len(member_oc_rates['DubZzZ'])} OC-specific rate entries")
            else:
                logger.warning("DubZzZ not found in member_oc_rates - will fall back to level-based checking")
        
        # members_with_oc_history already fetched in parallel above
        
        logger.info(f"Found {len(members)} members not in OC (after 72-hour filter)")
        logger.info(f"Found {len(ocs)} available OCs")
        logger.info(f"Found {len(members_with_oc_history)} members with OC history (fetched in parallel)")

        if not members:
            return "No members available for OC assignment (all members are already in OCs or have been in faction for less than 72 hours)."

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

        # Only consider OCs that are Recruiting (accepting members); exclude Planning
        ocs = [o for o in ocs if (o.get("status") or "").strip().lower() == "recruiting"]
        if not ocs:
            return "No available OCs found. Only Planning OCs exist; Recruiting OCs are required for assignment."
        logger.info(f"Using {len(ocs)} Recruiting OCs for assignment (Planning excluded)")

        # Helper function to calculate cumulative drop by summing actual drop values for each rank step
        def calculate_cumulative_drop(from_rank, to_rank):
            """
            Calculate cumulative drop by summing drop_from_prev for each rank step.
            Returns the total cumulative drop, or None if calculation not possible.
            """
            if from_rank is None or to_rank is None or from_rank >= to_rank:
                return None
            
            cumulative = 0.0
            for rank in range(from_rank + 1, to_rank + 1):
                if rank in rank_to_drop_map:
                    cumulative += rank_to_drop_map[rank]
                else:
                    # Missing drop data for a rank - can't calculate cumulative
                    logger.debug(f"Missing drop data for rank {rank}, cannot calculate cumulative drop from rank {from_rank} to {to_rank}")
                    return None
            
            return cumulative

        # Enrich members with participation data and activity status
        # Participation data now includes both 30d and 7d counts from a single query
        for member in members:
            member_id = member['member_id']
            member['oc_count_30d'] = participation.get(member_id, {}).get('oc_count_30d', 0)
            member['oc_count_7d'] = participation.get(member_id, {}).get('oc_count_7d', 0)
            
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
        # 1. Higher 30-day OC count = higher priority (most active members first)
        # 2. Higher 7-day OC count = higher priority (most recent activity)
        # 3. Active members first
        # This ensures members with highest participation get assigned to OCs that need fewer members
        members_sorted = sorted(
            members,
            key=lambda m: (
                -m.get('oc_count_30d', 0),  # Higher count first (negative for descending)
                -m.get('oc_count_7d', 0),   # Higher count first (negative for descending)
                not m.get('is_active', False),  # Active members first
            )
        )

        # Assign members to OCs
        # Active members → OCs starting soonest (if already has members) or OCs that won't expire for >1 day
        # Inactive members → OCs with longer delay
        active_members = [m for m in members_sorted if m['is_active']]
        inactive_members = [m for m in members_sorted if not m['is_active']]

        # Sort OCs by priority for active members
        # Priority: OCs that already have members and are starting soon, or OCs expiring >1 day away
        # Also sort by difficulty (higher first) so members get assigned to highest level they can do
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
        
        # Sort OCs by priority:
        # 1. Difficulty (descending) - higher levels first
        # 2. Partially filled OCs first (filled_slots > 0) - prioritize OCs that already have members
        # 3. For empty OCs at same level: prefer those with more time until expiration (to group unused members)
        # 4. Members needed to start (ascending) - OCs that need fewer members first
        # This ensures we fill partially filled OCs before creating new ones, and group unused members together
        def sort_key(oc):
            difficulty = oc.get('difficulty')
            if difficulty is None:
                difficulty = 0
            else:
                try:
                    difficulty = int(difficulty)
                except (ValueError, TypeError):
                    difficulty = 0
            
            # Calculate how many members are needed to start this OC
            total_slots = oc.get('total_slots', 0)
            filled_slots = oc.get('filled_slots', 0)
            members_needed = max(0, total_slots - filled_slots)
            
            # Prioritize partially filled OCs (filled_slots > 0) over empty OCs
            # Use negative of filled_slots so partially filled (positive) comes before empty (0)
            is_partially_filled = 1 if filled_slots > 0 else 0
            
            # For empty OCs, calculate hours until expiration (prefer OCs with more time)
            # This helps group unused members into the same OC with more time remaining
            hours_until_expiry = 0
            if filled_slots == 0:  # Only consider expiration for empty OCs
                expired_at = oc.get('expired_at')
                if expired_at:
                    try:
                        if isinstance(expired_at, str):
                            if expired_at.endswith('Z'):
                                expired_at = datetime.fromisoformat(expired_at.replace('Z', '+00:00'))
                            else:
                                expired_at = datetime.fromisoformat(expired_at)
                        elif not isinstance(expired_at, datetime):
                            expired_at = datetime.fromisoformat(str(expired_at))
                        
                        if expired_at.tzinfo is None:
                            expired_at = expired_at.replace(tzinfo=timezone.utc)
                        
                        hours_until_expiry = (expired_at - now).total_seconds() / 3600
                    except Exception:
                        hours_until_expiry = 0
            
            # Return tuple: (negative difficulty for descending, negative is_partially_filled for descending, 
            #                negative hours_until_expiry for descending (only matters for empty OCs), members_needed for ascending)
            # This sorts by: difficulty descending, then partially filled first, then for empty OCs: more time first, then members_needed ascending
            return (-difficulty, -is_partially_filled, -hours_until_expiry if filled_slots == 0 else 0, members_needed)
        
        ocs_for_active.sort(key=sort_key)
        ocs_for_inactive.sort(key=sort_key)
        
        # Create a combined sorted list for reference (used for output)
        # This maintains the priority order for display
        ocs_sorted = (ocs_for_active + ocs_for_inactive)
        ocs_sorted.sort(key=sort_key)
        
        # Debug: Log Level 6 OCs to verify sorting
        level_6_ocs = [oc for oc in ocs_sorted if oc.get('difficulty') == 6]
        if level_6_ocs:
            logger.info(f"DEBUG: Level 6 OCs after sorting:")
            for oc in level_6_ocs:
                oc_id = oc.get('oc_id')
                oc_name = oc.get('oc_name')
                filled = oc.get('filled_slots', 0)
                total = oc.get('total_slots', 0)
                needed = total - filled
                is_partial = 1 if filled > 0 else 0
                logger.info(f"  OC {oc_id} ({oc_name}): filled={filled}, total={total}, needed={needed}, is_partial={is_partial}, sort_key={sort_key(oc)}")

        # Assign all members to available OCs
        # Structure: oc_id -> [member_name]
        assignments = {}  # oc_id -> list of member names
        
        # Track which members have been assigned
        assigned_members = set()
        
        # Track assignment reasoning for reporting
        # Structure: member_name -> {
        #   'assigned_oc_id': str,
        #   'assigned_oc_name': str,
        #   'assigned_level': int,
        #   'max_recommended_oc': int,
        #   'reason': str,  # 'primary', 'grouped', 'fallback', 'lower_level', 'new_member'
        #   'grouped_with': [str],  # other members grouped together
        #   'considered_ocs': [{'oc_id': str, 'oc_name': str, 'level': int, 'reason_skipped': str}],
        #   'warnings': [str]
        # }
        assignment_reasons = {}  # member_name -> assignment details
        
        # Filter out excluded OC names
        excluded_oc_names_lower = [name.lower() for name in excluded_oc_names]
        def is_excluded_oc(oc):
            oc_name = oc.get('oc_name', '').lower()
            return any(excluded_name in oc_name for excluded_name in excluded_oc_names_lower)
        
        # Helper function to check if a member can join an OC
        def can_member_join_oc(member, oc, oc_list_context=None):
            """Check if a member can join a specific OC based on their qualifications."""
            member_name = member['member_name']
            member_id = member['member_id']
            has_oc_history = member_id in members_with_oc_history
            member_perf = member_performance.get(member_name, {})
            member_max_oc = member_perf.get('max_recommended_oc')
            member_level_rates = member_perf.get('level_rates', {})
            member_highest_level = member_perf.get('highest_level')
            member_highest_level_rate = member_perf.get('highest_level_rate')
            is_jump_recommendation = bool(member_perf.get('is_jump_recommendation'))
            # Get member's highest historical OC rate for "move up" calculations
            member_highest_hist = member_highest_historical.get(member_name, {})
            member_highest_hist_rate = member_highest_hist.get('checkpoint_rate')
            
            if is_excluded_oc(oc):
                return False
            
            oc_difficulty = oc.get('difficulty')
            if oc_difficulty is None:
                return False
            
            try:
                oc_difficulty = int(oc_difficulty)
            except (ValueError, TypeError):
                return False
            
            # Members with no OC history can only join Level 1 OCs
            if not has_oc_history:
                return oc_difficulty == 1
            else:
                # Check if member has valid checkpoint_pass_rate for this SPECIFIC OC
                oc_name = oc.get('oc_name', '').strip()
                member_oc_specific_rates = member_oc_rates.get(member_name, {})
                
                has_valid_oc_rate = False
                has_oc_specific_data = False
                best_oc_rate = None
                
                oc_name_lower = oc_name.lower().strip()
                for key, rate in member_oc_specific_rates.items():
                    key_lower = key.lower()
                    if key_lower.startswith(oc_name_lower + '_'):
                        has_oc_specific_data = True
                        try:
                            rate_num = float(rate)
                            if 0 <= rate_num <= 1:
                                rate_num = rate_num * 100
                            if best_oc_rate is None or rate_num > best_oc_rate:
                                best_oc_rate = rate_num
                            # Level 1: 0-90%, Level 2+: 80-90%
                            if oc_difficulty == 1:
                                if 0 <= rate_num <= 90:
                                    has_valid_oc_rate = True
                            else:
                                if 80 <= rate_num <= 90:
                                    has_valid_oc_rate = True
                        except (ValueError, TypeError):
                            continue
                
                # If we have OC-specific data and member can't meet requirements, skip
                if has_oc_specific_data and not has_valid_oc_rate:
                    return False
                
                # If no OC-specific data, fallback to level-based check
                if not has_oc_specific_data:
                    level_rate = member_level_rates.get(oc_difficulty)
                    if level_rate is None:
                        # Allow a "jump" to the recommended level when recommendation came from 90%+ at highest
                        # and there is no level-rate history for the next level yet.
                        if (
                            is_jump_recommendation
                            and member_max_oc is not None
                            and oc_difficulty == int(member_max_oc)
                            and member_highest_level is not None
                            and member_highest_level_rate is not None
                            and float(member_highest_level_rate) >= 90
                        ):
                            return True  # jump: no level_rate for this oc_difficulty; allow based on 90%+ at highest
                        else:
                            # Overcome the drop: use member's highest checkpoint rate from highest difficulty rank OC in historical DB
                            # This is the "move up" calculation - using drop_from_prev from Historical DB
                            best_rate = None
                            best_rate_source = None
                            # Prefer highest historical OC rate from Historical DB
                            if member_highest_hist_rate is not None:
                                try:
                                    best_rate = float(member_highest_hist_rate)
                                    if 0 <= best_rate <= 1:
                                        best_rate = best_rate * 100
                                    best_rate_source = f"highest historical OC (rank {member_highest_hist.get('oc_rank', 'N/A')})"
                                except (ValueError, TypeError):
                                    pass
                            # Fallback to closest difficulty rate (member_max_oc level) if highest historical not available
                            if best_rate is None and member_max_oc is not None and member_level_rates:
                                closest_rate = member_level_rates.get(member_max_oc)
                                if closest_rate is not None:
                                    try:
                                        best_rate = float(closest_rate)
                                        if 0 <= best_rate <= 1:
                                            best_rate = best_rate * 100
                                        best_rate_source = f"Level {member_max_oc} rate"
                                    except (ValueError, TypeError):
                                        pass
                            # Final fallback to highest_level_rate
                            if best_rate is None and member_highest_level_rate is not None:
                                try:
                                    best_rate = float(member_highest_level_rate)
                                    if 0 <= best_rate <= 1:
                                        best_rate = best_rate * 100
                                    best_rate_source = f"highest level rate"
                                except (ValueError, TypeError):
                                    pass
                            # Use drop_from_prev from Historical DB for "move up" calculation
                            # IMPORTANT: Drop is ACCUMULATIVE - sum actual drop values for each rank step
                            oc_name_normalized = (oc.get('oc_name') or '').strip().lower()
                            oc_rank_for_check = oc_rank_map.get(oc_name_normalized)
                            member_highest_rank = member_highest_hist.get('oc_rank')
                            
                            if best_rate is not None and member_highest_rank is not None and oc_rank_for_check is not None and oc_rank_for_check > member_highest_rank:
                                # Calculate cumulative drop by summing drops for each rank step
                                cumulative_drop = calculate_cumulative_drop(member_highest_rank, oc_rank_for_check)
                                if cumulative_drop is not None and cumulative_drop > 0:
                                    threshold = 0 if oc_difficulty == 1 else 80
                                    predicted_rate = best_rate - cumulative_drop
                                    if predicted_rate >= threshold:
                                        rank_diff = oc_rank_for_check - member_highest_rank
                                        drops_list = [f"{rank_to_drop_map.get(r, 0):.1f}%" for r in range(member_highest_rank + 1, oc_rank_for_check + 1) if r in rank_to_drop_map]
                                        logger.debug(
                                            f"Member can overcome drop to {oc.get('oc_name')} (Level {oc_difficulty}): "
                                            f"{best_rate_source} rate {best_rate:.1f}% - sum([{', '.join(drops_list)}]) = "
                                            f"{best_rate:.1f}% - {cumulative_drop:.1f}% = {predicted_rate:.1f}% >= {threshold}%"
                                        )
                                        return True  # overcome the drop: allow using highest historical OC rate with cumulative drop
                            elif oc_rank_for_check is None or member_highest_rank is None:
                                logger.debug(f"No rank data available for drop calculation: OC rank={oc_rank_for_check}, member highest rank={member_highest_rank}")
                            return False
                    try:
                        rate_num = float(level_rate)
                        if 0 <= rate_num <= 1:
                            rate_num = rate_num * 100
                        # Level 1: 0-90%, Level 2+: 80-90%
                        if oc_difficulty == 1:
                            if not (0 <= rate_num <= 90):
                                return False
                        else:
                            if not (80 <= rate_num <= 90):
                                return False
                    except (ValueError, TypeError):
                        return False
                
                # Check if OC difficulty is <= max_recommended_oc
                if member_max_oc is not None and oc_difficulty > member_max_oc:
                    return False
            
            return True
        
        # Assign members to OCs based on priority:
        # 1. Active members → OCs starting soonest (if already has members) or OCs that won't expire for >1 day
        # 2. Inactive members → OCs with longer delay
        # 3. Group unused members together into the same OC when possible
        for member in members_sorted:
            member_name = member['member_name']
            
            if member_name in assigned_members:
                continue
            
            # Get appropriate OC list based on activity
            # If inactive member's list is empty, fall back to active list so they can still be assigned
            if member['is_active']:
                oc_list = ocs_for_active
            else:
                oc_list = ocs_for_inactive if ocs_for_inactive else ocs_for_active
            
            # Debug logging for specific members
            if member_name in ["Adilon_Scorpian", "Hiyori"]:
                logger.info(f"DEBUG {member_name}: is_active = {member.get('is_active')}, using {'ocs_for_active' if member['is_active'] else 'ocs_for_inactive'}")
                logger.info(f"DEBUG {member_name}: ocs_for_active has {len(ocs_for_active)} OCs, ocs_for_inactive has {len(ocs_for_inactive)} OCs")
            
            # Check if member has any OC history
            member_id = member['member_id']
            has_oc_history = member_id in members_with_oc_history
            
            # Get member's performance data
            member_perf = member_performance.get(member_name, {})
            member_max_oc = member_perf.get('max_recommended_oc')
            member_level_rates = member_perf.get('level_rates', {})
            
            # Get member's highest historical OC data for predicted rate calculations
            highest_historical = member_highest_historical.get(member_name, {})
            highest_rate = highest_historical.get('checkpoint_rate')
            highest_oc_rank = highest_historical.get('oc_rank')
            
            # Helper function to enrich OC entry with oc_rank and predicted checkpoint rate
            def enrich_oc_entry(oc_entry_dict, oc_name_str):
                oc_name_norm = (oc_name_str or '').strip().lower()
                oc_entry_dict['oc_rank'] = oc_rank_map.get(oc_name_norm)
                
                # Calculate predicted checkpoint rate
                if highest_rate is not None and oc_entry_dict.get('oc_rank') is not None:
                    considered_oc_rank = oc_entry_dict.get('oc_rank')
                    
                    # If considered OC has lower difficulty rank than member's highest, and member's rate >= 80%,
                    # the member should be able to handle it (no drop calculation needed)
                    if (highest_oc_rank is not None and 
                        considered_oc_rank is not None and 
                        considered_oc_rank < highest_oc_rank and 
                        highest_rate >= 80):
                        # Member can handle this OC - use their highest rate as predicted rate
                        oc_entry_dict['predicted_checkpoint_rate'] = highest_rate
                    else:
                        # Calculate drop: only if considered OC has higher or equal difficulty rank
                        # Use drop_from_prev (float) from Historical DB
                        # IMPORTANT: Drop is ACCUMULATIVE per additional difficulty rank
                        drop = oc_drop_map.get(oc_name_norm)
                        if drop is not None:
                            try:
                                drop_float = float(drop)
                                if drop_float > 0:
                                    # Calculate rank difference for accumulative drop
                                    rank_diff = 1  # Default to 1 if ranks not available
                                    if highest_oc_rank is not None and considered_oc_rank is not None:
                                        rank_diff = max(1, considered_oc_rank - highest_oc_rank)
                                    
                                    # Accumulative drop: multiply single-step drop by rank difference
                                    cumulative_drop = drop_float * rank_diff
                                    predicted_rate = highest_rate - cumulative_drop
                                    oc_entry_dict['predicted_checkpoint_rate'] = predicted_rate
                                    # Debug logging for predicted rate calculation
                                    # Note: member_name here refers to the primary member, but highest_rate/highest_oc_rank
                                    # may have been temporarily set for grouped member checks
                                    debug_member = getattr(enrich_oc_entry, '_debug_member', member_name)
                                    if debug_member in ["Mazcariu", "Rachel420"] or (highest_oc_rank is not None and considered_oc_rank is not None and considered_oc_rank > highest_oc_rank):
                                        logger.info(
                                            f"PREDICTED_RATE: {debug_member} -> {oc_name_str}: "
                                            f"highest_rate={highest_rate:.1f}%, drop_per_rank={drop_float:.1f}%, "
                                            f"rank_diff={rank_diff}, cumulative_drop={cumulative_drop:.1f}%, "
                                            f"predicted={predicted_rate:.1f}%, "
                                            f"highest_rank={highest_oc_rank}, considered_rank={considered_oc_rank}"
                                        )
                                else:
                                    # Drop is 0 or negative, use highest rate directly
                                    oc_entry_dict['predicted_checkpoint_rate'] = highest_rate
                            except (ValueError, TypeError) as e:
                                # Invalid drop value, use highest rate as fallback
                                logger.warning(f"Invalid drop value '{drop}' for OC {oc_name_str}: {e}")
                                oc_entry_dict['predicted_checkpoint_rate'] = highest_rate
                        elif highest_oc_rank is not None and considered_oc_rank is not None:
                            # If we have ranks but no drop data, and considered OC is lower rank,
                            # use highest rate (member should be able to handle it)
                            if considered_oc_rank < highest_oc_rank and highest_rate >= 80:
                                oc_entry_dict['predicted_checkpoint_rate'] = highest_rate
                            elif considered_oc_rank > highest_oc_rank:
                                # Higher rank OC but no drop data - log warning
                                logger.warning(
                                    f"No drop_from_prev data for OC {oc_name_str} (rank {considered_oc_rank}) "
                                    f"when member's highest is rank {highest_oc_rank}. Cannot calculate predicted rate."
                                )
                        else:
                            # No drop data and no rank comparison possible
                            logger.debug(f"No drop data for OC {oc_name_str}, cannot calculate predicted rate")
                
                return oc_entry_dict
            
            # Debug logging for specific members
            if member_name in ["Adilon_Scorpian", "Hiyori"]:
                logger.info(f"DEBUG {member_name}: Starting assignment, member_max_oc = {member_max_oc}, level_rates = {member_level_rates}")
            
            # Initialize assignment tracking for this member
            if member_name not in assignment_reasons:
                # Get member's highest historical OC data
                highest_historical = member_highest_historical.get(member_name, {})
                assignment_reasons[member_name] = {
                    'assigned_oc_id': None,
                    'assigned_oc_name': None,
                    'assigned_level': None,
                    'max_recommended_oc': member_max_oc,
                    'reason': None,
                    'grouped_with': [],
                    'considered_ocs': [],
                    'warnings': [],
                    'highest_historical_oc': highest_historical  # Store for display
                }
            
            # Filter OC list to only include OCs at or below member's max recommended level
            # This prevents checking OCs that are too high for the member (more efficient)
            # For members with no OC history, only check Level 1 OCs
            filtered_oc_list = []
            seen_oc_ids = set()

            # IMPORTANT: Always consider OCs at the member's max level across *all* OCs first,
            # regardless of the active/inactive split. Otherwise active members can miss an
            # empty-but-soon-expiring max-level OC and get placed into max-1 instead.
            for oc in ocs_sorted:
                oc_id = oc.get("oc_id")
                oc_difficulty = oc.get('difficulty')
                if oc_difficulty is None:
                    continue
                try:
                    oc_difficulty = int(oc_difficulty)
                except (ValueError, TypeError):
                    continue

                if oc_id is None or oc_id in seen_oc_ids:
                    continue

                if not has_oc_history:
                    continue

                if member_max_oc is not None and oc_difficulty == int(member_max_oc):
                    filtered_oc_list.append(oc)
                    seen_oc_ids.add(oc_id)

            for oc in oc_list:
                oc_difficulty = oc.get('difficulty')
                if oc_difficulty is None:
                    continue
                try:
                    oc_difficulty = int(oc_difficulty)
                except (ValueError, TypeError):
                    continue
                
                oc_id = oc.get("oc_id")
                if oc_id is not None and oc_id in seen_oc_ids:
                    continue

                # Members with no OC history can only join Level 1 OCs
                if not has_oc_history:
                    if oc_difficulty == 1:
                        filtered_oc_list.append(oc)
                else:
                    # Members with OC history: allow 1..member_max_oc and above (overcome-the-drop can assign higher)
                    if member_max_oc is not None:
                        if oc_difficulty >= 1:
                            filtered_oc_list.append(oc)
                            if oc_id is not None:
                                seen_oc_ids.add(oc_id)
                    else:
                        # If no max_recommended_oc, check all OCs (shouldn't happen, but be safe)
                        filtered_oc_list.append(oc)
                        if oc_id is not None:
                            seen_oc_ids.add(oc_id)
            
            # Try to assign member to highest level OC they can do (filtered list is already sorted by difficulty descending)
            # This ensures members get assigned to their max recommended level, not just the first available
            if member_name in ["Adilon_Scorpian", "Hiyori"]:
                logger.info(f"DEBUG {member_name}: Starting OC loop, filtered_oc_list has {len(filtered_oc_list)} OCs (from {len(oc_list)} total)")
                level_counts = {}
                for oc in filtered_oc_list:
                    diff = oc.get('difficulty')
                    if diff:
                        level_counts[diff] = level_counts.get(diff, 0) + 1
                logger.info(f"DEBUG {member_name}: Filtered OC list breakdown by level: {level_counts}")
            for oc in filtered_oc_list:
                # Skip excluded OCs
                if is_excluded_oc(oc):
                    oc_entry = {
                        'oc_id': oc.get('oc_id'),
                        'oc_name': oc.get('oc_name', 'Unknown'),
                        'level': oc.get('difficulty'),
                        'reason_skipped': 'Excluded OC (No Reserve or similar)'
                    }
                    enrich_oc_entry(oc_entry, oc.get('oc_name', 'Unknown'))
                    assignment_reasons[member_name]['considered_ocs'].append(oc_entry)
                    continue
                
                oc_difficulty = oc.get('difficulty')
                if oc_difficulty is None:
                    continue
                
                try:
                    oc_difficulty = int(oc_difficulty)
                except (ValueError, TypeError):
                    continue
                
                oc_id = oc.get('oc_id')
                oc_name = oc.get('oc_name', 'Unknown')
                
                # Note: max_recommended_oc and OC history checks are already done in the filter above
                # No need to check again here - all OCs in filtered_oc_list are already valid
                
                # Check if member has valid checkpoint_pass_rate for this SPECIFIC OC
                # Level 1 OCs: 0-90%, Level 2+ OCs: 80-90%
                oc_name = oc.get('oc_name', '').strip()
                member_oc_specific_rates = member_oc_rates.get(member_name, {})
                
                # Check if member has any position for this specific OC with rate in valid range
                has_valid_oc_rate = False
                has_oc_specific_data = False
                best_oc_rate = None
                matching_keys = []
                
                oc_name_lower = oc_name.lower().strip()
                for key, rate in member_oc_specific_rates.items():
                        # Key format is "oc_name_position_id", so check if it starts with oc_name
                        key_lower = key.lower()
                        if key_lower.startswith(oc_name_lower + '_'):
                            matching_keys.append(key)
                            has_oc_specific_data = True
                            try:
                                rate_num = float(rate)
                                if 0 <= rate_num <= 1:
                                    rate_num = rate_num * 100
                                
                                # Track best rate for this OC
                                if best_oc_rate is None or rate_num > best_oc_rate:
                                    best_oc_rate = rate_num
                                
                                # Level 1: 0-90%, Level 2+: 80-90%
                                if oc_difficulty == 1:
                                    if 0 <= rate_num <= 90:
                                        has_valid_oc_rate = True
                                else:
                                    if 80 <= rate_num <= 90:
                                        has_valid_oc_rate = True
                            except (ValueError, TypeError):
                                continue
                
                # Debug logging for problematic assignments
                if (member_name == "DubZzZ" and oc_name_lower == "leave no trace") or \
                   (member_name in ["Adilon_Scorpian", "Hiyori"] and oc_difficulty >= 3):
                    logger.info(f"DEBUG {member_name}/{oc_name} (Level {oc_difficulty}): member_oc_specific_rates has {len(member_oc_specific_rates)} keys")
                    logger.info(f"DEBUG {member_name}/{oc_name}: oc_name = '{oc_name}', oc_name_lower = '{oc_name_lower}'")
                    logger.info(f"DEBUG {member_name}/{oc_name}: matching_keys = {matching_keys}")
                    logger.info(f"DEBUG {member_name}/{oc_name}: has_oc_specific_data = {has_oc_specific_data}, best_oc_rate = {best_oc_rate}, has_valid_oc_rate = {has_valid_oc_rate}")
                    logger.info(f"DEBUG {member_name}/{oc_name}: member_max_oc = {member_max_oc}, oc_difficulty = {oc_difficulty}, level_rate = {member_level_rates.get(oc_difficulty)}")
                
                # If we have OC-specific data and member can't meet requirements, skip this OC
                # Level 1 OCs: 0-90%, Level 2+ OCs: 80-90%
                if has_oc_specific_data and not has_valid_oc_rate:
                    if oc_difficulty == 1:
                        # For Level 1, check if rate is in 0-90% range
                        if best_oc_rate is not None and 0 <= best_oc_rate <= 90:
                            has_valid_oc_rate = True
                        else:
                            logger.info(f"Skipping {member_name} for Level {oc_difficulty} OC '{oc_name}': has OC-specific data with best rate {best_oc_rate} (not in 0-90 range)")
                            oc_entry = {
                                'oc_id': oc_id,
                                'oc_name': oc_name,
                                'level': oc_difficulty,
                                'reason_skipped': f'OC-specific rate {best_oc_rate:.1f}% not in 0-90% range'
                            }
                            enrich_oc_entry(oc_entry, oc_name)
                            assignment_reasons[member_name]['considered_ocs'].append(oc_entry)
                            continue
                    else:
                        logger.info(f"Skipping {member_name} for Level {oc_difficulty} OC '{oc_name}': has OC-specific data with best rate {best_oc_rate} (not in 80-90 range)")
                        oc_entry = {
                            'oc_id': oc_id,
                            'oc_name': oc_name,
                            'level': oc_difficulty,
                            'reason_skipped': f'OC-specific rate {best_oc_rate:.1f}% not in 80-90% range'
                        }
                        enrich_oc_entry(oc_entry, oc_name)
                        assignment_reasons[member_name]['considered_ocs'].append(oc_entry)
                        continue
                    
                # If no OC-specific data, fallback to level-based check
                if not has_oc_specific_data:
                    level_rate = member_level_rates.get(oc_difficulty)
                    if level_rate is None:
                        # Allow "jump" recommendation to the member's max level even without level history.
                        is_jump = bool(member_perf.get('is_jump_recommendation'))
                        highest_rate = member_perf.get('highest_level_rate')
                        highest_level = member_perf.get('highest_level')
                        if (
                            is_jump
                            and member_max_oc is not None
                            and oc_difficulty == int(member_max_oc)
                            and highest_rate is not None
                            and float(highest_rate) >= 90
                        ):
                            # Permit this OC. Add a note so it's visible in the reasoning report.
                            assignment_reasons[member_name]['warnings'].append(
                                f"Jump recommendation: assigning to Level {oc_difficulty} based on {float(highest_rate):.1f}% at Level {highest_level}"
                            )
                        else:
                            # Overcome the drop: use member's highest checkpoint rate from highest difficulty rank OC in historical DB
                            # This is the "move up" calculation - using drop_from_prev from Historical DB
                            best_rate = None
                            best_rate_source = None
                            # Prefer highest historical OC rate from Historical DB
                            if highest_historical.get('checkpoint_rate') is not None:
                                try:
                                    best_rate = float(highest_historical.get('checkpoint_rate'))
                                    if 0 <= best_rate <= 1:
                                        best_rate = best_rate * 100
                                    best_rate_source = f"highest historical OC (rank {highest_historical.get('oc_rank', 'N/A')})"
                                except (ValueError, TypeError):
                                    pass
                            # Fallback to closest difficulty rate (member_max_oc level) if highest historical not available
                            if best_rate is None and member_max_oc is not None and member_level_rates:
                                closest_rate = member_level_rates.get(member_max_oc)
                                if closest_rate is not None:
                                    try:
                                        best_rate = float(closest_rate)
                                        if 0 <= best_rate <= 1:
                                            best_rate = best_rate * 100
                                        best_rate_source = f"Level {member_max_oc} rate"
                                    except (ValueError, TypeError):
                                        pass
                            # Final fallback to highest_level_rate
                            if best_rate is None and highest_rate is not None:
                                try:
                                    best_rate = float(highest_rate)
                                    if 0 <= best_rate <= 1:
                                        best_rate = best_rate * 100
                                    best_rate_source = f"highest level rate"
                                except (ValueError, TypeError):
                                    pass
                            # Use drop_from_prev from Historical DB for "move up" calculation
                            # IMPORTANT: Drop is ACCUMULATIVE - sum actual drop values for each rank step
                            oc_name_normalized = (oc_name or '').strip().lower()
                            oc_rank_for_check = oc_rank_map.get(oc_name_normalized)
                            highest_oc_rank = highest_historical.get('oc_rank')
                            
                            if best_rate is not None and highest_oc_rank is not None and oc_rank_for_check is not None and oc_rank_for_check > highest_oc_rank:
                                # Calculate cumulative drop by summing drops for each rank step
                                cumulative_drop = calculate_cumulative_drop(highest_oc_rank, oc_rank_for_check)
                                if cumulative_drop is not None and cumulative_drop > 0:
                                    threshold = 0 if oc_difficulty == 1 else 80
                                    calculated_rate = best_rate - cumulative_drop
                                    if calculated_rate >= threshold:
                                        rank_diff = oc_rank_for_check - highest_oc_rank
                                        drops_list = [f"{rank_to_drop_map.get(r, 0):.1f}%" for r in range(highest_oc_rank + 1, oc_rank_for_check + 1) if r in rank_to_drop_map]
                                        drops_sum_str = ' + '.join(drops_list)
                                        assignment_reasons[member_name]['warnings'].append(
                                            f"Overcome the drop: assigning to Level {oc_difficulty} OC '{oc_name}' "
                                            f"using {best_rate_source} ({best_rate:.1f}% - ({drops_sum_str}) = {best_rate:.1f}% - {cumulative_drop:.1f}% = {calculated_rate:.1f}% >= {threshold})"
                                        )
                                        logger.info(
                                            f"Member {member_name} can overcome drop to {oc_name} (Level {oc_difficulty}): "
                                            f"{best_rate_source} rate {best_rate:.1f}% - ({drops_sum_str}) = "
                                            f"{best_rate:.1f}% - {cumulative_drop:.1f}% = {calculated_rate:.1f}% >= {threshold}%"
                                        )
                                        # fall through to "Check if OC has space"
                                    else:
                                        drops_list = [f"{rank_to_drop_map.get(r, 0):.1f}%" for r in range(highest_oc_rank + 1, oc_rank_for_check + 1) if r in rank_to_drop_map]
                                        drops_sum_str = ' + '.join(drops_list)
                                        extra = f"; {best_rate_source} {best_rate:.1f}% - ({drops_sum_str}) = {best_rate:.1f}% - {cumulative_drop:.1f}% = {calculated_rate:.1f}% < {threshold}"
                                        logger.debug(f"Skipping {member_name} for Level {oc_difficulty} OC {oc_name}: no level-based rate data for this level{extra}")
                                        oc_entry = {
                                            'oc_id': oc_id, 'oc_name': oc_name, 'level': oc_difficulty,
                                            'reason_skipped': f'No level-based rate data for this level{extra}'
                                        }
                                        enrich_oc_entry(oc_entry, oc_name)
                                        assignment_reasons[member_name]['considered_ocs'].append(oc_entry)
                                        continue
                                else:
                                    logger.debug(f"Cannot calculate cumulative drop from rank {highest_oc_rank} to {oc_rank_for_check} for OC {oc_name}")
                            elif oc_rank_for_check is None or highest_oc_rank is None:
                                logger.debug(f"No rank data for drop calculation: OC rank={oc_rank_for_check}, member highest rank={highest_oc_rank}")
                            else:
                                logger.debug(f"Skipping {member_name} for Level {oc_difficulty} OC {oc_name}: no level-based rate data for this level")
                                oc_entry = {
                                    'oc_id': oc_id, 'oc_name': oc_name, 'level': oc_difficulty,
                                    'reason_skipped': 'No level-based rate data for this level'
                                }
                                enrich_oc_entry(oc_entry, oc_name)
                                assignment_reasons[member_name]['considered_ocs'].append(oc_entry)
                                continue
                    
                    # When level_rate is not None: ensure percentage format and strict check
                    # (when level_rate is None we fell through from jump or overcome and skip to "Check if OC has space")
                    if level_rate is not None:
                        try:
                            rate_num = float(level_rate)
                            if 0 <= rate_num <= 1:
                                rate_num = rate_num * 100
                        except (ValueError, TypeError):
                            logger.debug(f"Skipping {member_name} for Level {oc_difficulty} OC {oc_name}: invalid rate format")
                            assignment_reasons[member_name]['considered_ocs'].append({
                                'oc_id': oc_id,
                                'oc_name': oc_name,
                                'level': oc_difficulty,
                                'reason_skipped': f'Invalid rate format: {level_rate}'
                            })
                            continue
                        
                        # STRICT CHECK: Rate must be in valid range
                        # Level 1 OCs: 0-90% (per guidelines: "Only with Level 1 OCs, participants may have a 0 to 90 success rate")
                        # Level 2+ OCs: 80-90%
                        if oc_difficulty == 1:
                            if not (0 <= rate_num <= 90):
                                logger.debug(f"Skipping {member_name} for Level {oc_difficulty} OC {oc_name}: rate {rate_num} not in 0-90 range")
                                assignment_reasons[member_name]['considered_ocs'].append({
                                    'oc_id': oc_id,
                                    'oc_name': oc_name,
                                    'level': oc_difficulty,
                                    'reason_skipped': f'Level rate {rate_num:.1f}% not in 0-90% range'
                                })
                                continue
                        else:
                            if not (80 <= rate_num <= 90):
                                logger.debug(f"Skipping {member_name} for Level {oc_difficulty} OC {oc_name}: rate {rate_num} not in 80-90 range")
                                assignment_reasons[member_name]['considered_ocs'].append({
                                    'oc_id': oc_id,
                                    'oc_name': oc_name,
                                    'level': oc_difficulty,
                                    'reason_skipped': f'Level rate {rate_num:.1f}% not in 80-90% range'
                                })
                                continue
                
                # Check if OC has space
                if oc_id not in assignments:
                    assignments[oc_id] = []
                
                # Check available slots (total slots minus filled slots)
                total_slots = oc.get('total_slots', 0)
                filled_slots = oc.get('filled_slots', 0)
                assigned_count = len(assignments[oc_id])
                available_slots = total_slots - filled_slots - assigned_count
                
                if available_slots > 0:
                    # CRITICAL: Before assigning, check if this OC is appropriate for the PRIMARY member
                    # Allow all lower: if member meets minimums for higher, they may join any easier OC (min_allowed_level=1)
                    if member_max_oc is not None:
                        min_allowed_level = 1
                        if oc_difficulty < min_allowed_level:
                            # OC is more than 1 level below their max - skip it and continue searching
                            oc_entry = {
                                'oc_id': oc_id,
                                'oc_name': oc_name,
                                'level': oc_difficulty,
                                'reason_skipped': f'OC level {oc_difficulty} is more than 1 level below max recommended {member_max_oc}'
                            }
                            enrich_oc_entry(oc_entry, oc_name)
                            assignment_reasons[member_name]['considered_ocs'].append(oc_entry)
                            logger.debug(f"Skipping {member_name} for Level {oc_difficulty} OC '{oc_name}': more than 1 level below max {member_max_oc}")
                            continue
                    
                    # CRITICAL: Check predicted rate before assigning to higher difficulty rank OCs
                    # If predicted rate < 80% and OC has higher difficulty rank than member's highest, don't assign
                    oc_entry_for_check = {'oc_id': oc_id, 'oc_name': oc_name, 'level': oc_difficulty}
                    enrich_oc_entry(oc_entry_for_check, oc_name)
                    predicted_rate = oc_entry_for_check.get('predicted_checkpoint_rate')
                    oc_rank = oc_entry_for_check.get('oc_rank')
                    
                    if (predicted_rate is not None and 
                        highest_oc_rank is not None and 
                        oc_rank is not None and 
                        oc_rank > highest_oc_rank and 
                        predicted_rate < 80):
                        # Member's predicted rate is below 80% for a higher difficulty rank OC - skip assignment
                        oc_entry = {
                            'oc_id': oc_id,
                            'oc_name': oc_name,
                            'level': oc_difficulty,
                            'oc_rank': oc_rank,
                            'predicted_checkpoint_rate': predicted_rate,
                            'reason_skipped': f'Predicted checkpoint rate {predicted_rate:.1f}% < 80% for higher difficulty rank OC (member highest rank: {highest_oc_rank}, OC rank: {oc_rank})'
                        }
                        assignment_reasons[member_name]['considered_ocs'].append(oc_entry)
                        logger.info(
                            f"Skipping {member_name} for Level {oc_difficulty} OC '{oc_name}' (rank {oc_rank}): "
                            f"predicted rate {predicted_rate:.1f}% < 80% (member's highest rank: {highest_oc_rank})"
                        )
                        continue
                    
                    # Found a suitable OC! Now try to group other unassigned members who can join this same OC
                    # This ensures unused members are grouped together
                    members_to_assign = [member_name]
                    assignment_reason = 'primary'
                    
                    # Check if this is an empty OC (filled_slots == 0) - if so, try to group other members
                    # Only group members who are at or near their max recommended level for this OC
                    if filled_slots == 0:
                        # Get current member's max recommended OC to use as reference
                        current_member_perf = member_performance.get(member_name, {})
                        current_member_max_oc = current_member_perf.get('max_recommended_oc')
                        
                        # Find ALL other unassigned members who can join this same OC
                        # Only group if they're at or near their max recommended level (within 1 level)
                        for other_member in members_sorted:
                            other_member_name = other_member['member_name']
                            if other_member_name in assigned_members:
                                continue
                            if other_member_name == member_name:
                                continue
                            
                            # Check if other member can join this OC
                            if not can_member_join_oc(other_member, oc):
                                continue
                            
                            # Only group if the OC is at or near their max recommended level
                            # This prevents grouping high-level members into low-level OCs
                            other_member_perf = member_performance.get(other_member_name, {})
                            other_member_max_oc = other_member_perf.get('max_recommended_oc')
                            
                            # CRITICAL: Check predicted rate for grouped members too
                            # If predicted rate < 80% and OC has higher difficulty rank than member's highest, don't group
                            other_highest_historical = member_highest_historical.get(other_member_name, {})
                            other_highest_oc_rank = other_highest_historical.get('oc_rank')
                            other_oc_entry_for_check = {'oc_id': oc_id, 'oc_name': oc_name, 'level': oc_difficulty}
                            # Need to temporarily set highest_rate and highest_oc_rank for enrich_oc_entry
                            # Store original values
                            orig_highest_rate = highest_rate
                            orig_highest_oc_rank = highest_oc_rank
                            other_highest_rate = other_highest_historical.get('checkpoint_rate')
                            highest_rate = other_highest_rate
                            highest_oc_rank = other_highest_oc_rank
                            # Set debug member name for logging
                            enrich_oc_entry._debug_member = other_member_name
                            enrich_oc_entry(other_oc_entry_for_check, oc_name)
                            # Restore original values
                            highest_rate = orig_highest_rate
                            highest_oc_rank = orig_highest_oc_rank
                            delattr(enrich_oc_entry, '_debug_member')
                            
                            other_predicted_rate = other_oc_entry_for_check.get('predicted_checkpoint_rate')
                            other_oc_rank = other_oc_entry_for_check.get('oc_rank')
                            
                            # Debug logging for grouped members
                            if other_member_name in ["Mazcariu", "Rachel420"] or (other_highest_oc_rank is not None and other_oc_rank is not None and other_oc_rank > other_highest_oc_rank):
                                logger.info(
                                    f"GROUPING_CHECK: {other_member_name} -> {oc_name}: "
                                    f"highest_rate={other_highest_historical.get('checkpoint_rate')}, "
                                    f"predicted_rate={other_predicted_rate}, "
                                    f"highest_rank={other_highest_oc_rank}, considered_rank={other_oc_rank}"
                                )
                            
                            if (other_predicted_rate is not None and 
                                other_highest_oc_rank is not None and 
                                other_oc_rank is not None and 
                                other_oc_rank > other_highest_oc_rank and 
                                other_predicted_rate < 80):
                                # Grouped member's predicted rate is below 80% for a higher difficulty rank OC - don't group
                                # Note: predicted_rate already uses accumulative drop from enrich_oc_entry
                                logger.info(
                                    f"Not grouping {other_member_name} into Level {oc_difficulty} OC '{oc_name}' (rank {other_oc_rank}): "
                                    f"predicted rate {other_predicted_rate:.1f}% < 80% (member's highest rank: {other_highest_oc_rank})"
                                )
                                continue
                            
                            # If other member has a max recommended OC: allow all lower (min_allowed_level=1)
                            if other_member_max_oc is not None:
                                min_allowed_level = 1
                                if oc_difficulty < min_allowed_level:
                                    # OC is more than 1 level below their max - don't group
                                    logger.debug(f"Not grouping {other_member_name} into Level {oc_difficulty} OC: their max is {other_member_max_oc}, minimum allowed is {min_allowed_level}")
                                    continue
                                
                                # CRITICAL: Also check if there are available OCs at their max level
                                # Don't group them into a lower level OC if suitable OCs exist at their max level
                                if oc_difficulty < other_member_max_oc:
                                    # Check if there are any available OCs at their max level
                                    has_available_oc_at_max = False
                                    for check_oc in ocs:
                                        if is_excluded_oc(check_oc):
                                            continue
                                        check_difficulty = check_oc.get('difficulty')
                                        if check_difficulty is None:
                                            continue
                                        try:
                                            check_difficulty = int(check_difficulty)
                                        except (ValueError, TypeError):
                                            continue
                                        
                                        if check_difficulty != other_member_max_oc:
                                            continue
                                        
                                        # Check if member can join this OC
                                        if can_member_join_oc(other_member, check_oc):
                                            check_oc_id = check_oc['oc_id']
                                            check_total_slots = check_oc.get('total_slots', 0)
                                            check_filled_slots = check_oc.get('filled_slots', 0)
                                            check_assigned_count = len(assignments.get(check_oc_id, []))
                                            check_available_slots = check_total_slots - check_filled_slots - check_assigned_count
                                            
                                            if check_available_slots > 0:
                                                has_available_oc_at_max = True
                                                logger.debug(f"Not grouping {other_member_name} into Level {oc_difficulty} OC: Level {other_member_max_oc} OC '{check_oc.get('oc_name')}' ({check_oc_id}) is available with {check_available_slots} slots")
                                                break
                                    
                                    if has_available_oc_at_max:
                                        # Don't group - they should be assigned to their max level OC instead
                                        continue
                            
                            # Check if there's still space
                            if len(members_to_assign) < available_slots:
                                members_to_assign.append(other_member_name)
                            else:
                                break  # OC is full
                    
                    # Update assignment reason if grouped
                    if len(members_to_assign) > 1:
                        assignment_reason = 'grouped'
                    
                    # Assign all members in the group to this OC
                    for idx, m_name in enumerate(members_to_assign):
                        assignments[oc_id].append(m_name)
                        assigned_members.add(m_name)
                        logger.info(f"Assigned {m_name} to OC {oc_id} ({oc.get('oc_name')}, Level {oc_difficulty})")
                        
                        # Record assignment reasoning
                        if m_name not in assignment_reasons:
                            # Get member's highest historical OC data
                            highest_historical_grouped = member_highest_historical.get(m_name, {})
                            assignment_reasons[m_name] = {
                                'assigned_oc_id': None,
                                'assigned_oc_name': None,
                                'assigned_level': None,
                                'max_recommended_oc': member_performance.get(m_name, {}).get('max_recommended_oc'),
                                'reason': None,
                                'grouped_with': [],
                                'considered_ocs': [],
                                'warnings': [],
                                'highest_historical_oc': highest_historical_grouped  # Store for display
                            }
                        
                        assignment_reasons[m_name]['assigned_oc_id'] = oc_id
                        assignment_reasons[m_name]['assigned_oc_name'] = oc_name
                        assignment_reasons[m_name]['assigned_level'] = oc_difficulty
                        assignment_reasons[m_name]['reason'] = assignment_reason if idx == 0 else 'grouped'
                        if len(members_to_assign) > 1:
                            assignment_reasons[m_name]['grouped_with'] = [m for m in members_to_assign if m != m_name]
                        
                        # Add the assigned OC to considered_ocs so it shows up when details are expanded
                        # This helps users see what OC was selected even when it was the first one checked
                        # Calculate assigned_count after assignment (includes current members)
                        final_assigned_count = len(assignments[oc_id])
                        
                        # Get this member's highest historical OC data for detailed reason
                        m_highest_historical = member_highest_historical.get(m_name, {})
                        m_highest_rate = m_highest_historical.get('checkpoint_rate')
                        m_highest_oc_rank = m_highest_historical.get('oc_rank')
                        m_max_oc = member_performance.get(m_name, {}).get('max_recommended_oc')
                        
                        # Build detailed reason similar to rejected higher-level OCs
                        detailed_reason_parts = []
                        if m_max_oc is not None and oc_difficulty > m_max_oc:
                            detailed_reason_parts.append(f'OC level {oc_difficulty} is above max recommended level {m_max_oc}')
                        
                        # Get OC rank and predicted rate for this member
                        oc_name_normalized = oc_name.strip().lower()
                        m_oc_rank = oc_rank_map.get(oc_name_normalized)
                        m_predicted_rate = None
                        
                        if m_highest_rate is not None and m_oc_rank is not None:
                            if m_highest_oc_rank is not None and m_oc_rank is not None:
                                if m_oc_rank < m_highest_oc_rank and m_highest_rate >= 80:
                                    m_predicted_rate = m_highest_rate
                                elif m_oc_rank > m_highest_oc_rank:
                                    # Calculate cumulative drop by summing drops for each rank step
                                    cumulative_drop = calculate_cumulative_drop(m_highest_oc_rank, m_oc_rank)
                                    if cumulative_drop is not None and cumulative_drop > 0:
                                        m_predicted_rate = m_highest_rate - cumulative_drop
                                    else:
                                        # Fallback: use single drop if cumulative can't be calculated
                                        drop = oc_drop_map.get(oc_name_normalized)
                                        if drop is not None:
                                            try:
                                                drop_float = float(drop)
                                                if drop_float > 0:
                                                    m_predicted_rate = m_highest_rate - drop_float
                                                else:
                                                    m_predicted_rate = m_highest_rate
                                            except (ValueError, TypeError):
                                                m_predicted_rate = m_highest_rate
                                        else:
                                            m_predicted_rate = m_highest_rate
                                else:
                                    # Same rank - use highest rate
                                    m_predicted_rate = m_highest_rate
                            
                            if m_oc_rank is not None:
                                detailed_reason_parts.append(f'Difficulty rank: {m_oc_rank}')
                            if m_predicted_rate is not None and m_highest_oc_rank is not None and m_oc_rank is not None and m_oc_rank > m_highest_oc_rank:
                                # Calculate cumulative drop for display
                                cumulative_drop = calculate_cumulative_drop(m_highest_oc_rank, m_oc_rank)
                                if cumulative_drop is not None:
                                    drops_list = [f"{rank_to_drop_map.get(r, 0):.1f}%" for r in range(m_highest_oc_rank + 1, m_oc_rank + 1) if r in rank_to_drop_map]
                                    drops_sum_str = ' + '.join(drops_list)
                                    detailed_reason_parts.append(f'Predicted checkpoint rate: {m_highest_rate:.1f}% - ({drops_sum_str}) = {m_highest_rate:.1f}% - {cumulative_drop:.1f}% = {m_predicted_rate:.1f}%')
                                else:
                                    # Fallback display
                                    drop_val = oc_drop_map.get(oc_name_normalized, 0)
                                    detailed_reason_parts.append(f'Predicted checkpoint rate: {m_highest_rate:.1f}% - {drop_val:.1f}% = {m_predicted_rate:.1f}%')
                            
                            # Add "overcome the drop" info if applicable
                            if m_highest_oc_rank is not None and m_oc_rank is not None and m_oc_rank > m_highest_oc_rank:
                                cumulative_drop = calculate_cumulative_drop(m_highest_oc_rank, m_oc_rank)
                                if cumulative_drop is not None and cumulative_drop > 0:
                                    threshold = 0 if oc_difficulty == 1 else 80
                                    calculated_rate = m_highest_rate - cumulative_drop
                                    drops_list = [f"{rank_to_drop_map.get(r, 0):.1f}%" for r in range(m_highest_oc_rank + 1, m_oc_rank + 1) if r in rank_to_drop_map]
                                    drops_sum_str = ' + '.join(drops_list)
                                    if calculated_rate >= threshold:
                                        detailed_reason_parts.append(
                                            f'Could potentially overcome drop using highest historical OC (rank {m_highest_oc_rank}) '
                                            f'({m_highest_rate:.1f}% - ({drops_sum_str}) = {m_highest_rate:.1f}% - {cumulative_drop:.1f}% = {calculated_rate:.1f}% >= {threshold}%)'
                                        )
                                    else:
                                        detailed_reason_parts.append(
                                            f'Cannot overcome drop using highest historical OC (rank {m_highest_oc_rank}) '
                                            f'({m_highest_rate:.1f}% - ({drops_sum_str}) = {m_highest_rate:.1f}% - {cumulative_drop:.1f}% = {calculated_rate:.1f}% < {threshold}%)'
                                        )
                        
                        # Build the reason: detailed parts first, then "Selected for assignment"
                        reason_text = '; '.join(detailed_reason_parts) if detailed_reason_parts else ''
                        if reason_text:
                            reason_text += f'; Selected for assignment (total: {total_slots}, filled: {filled_slots}, assigned: {final_assigned_count})'
                        else:
                            reason_text = f'Selected for assignment (total: {total_slots}, filled: {filled_slots}, assigned: {final_assigned_count})'
                        
                        oc_entry = {
                            'oc_id': oc_id,
                            'oc_name': oc_name,
                            'level': oc_difficulty,
                            'oc_rank': m_oc_rank,
                            'predicted_checkpoint_rate': m_predicted_rate,
                            'reason_skipped': reason_text,
                            'total_slots': total_slots,
                            'filled_slots': filled_slots,
                            'assigned_count': final_assigned_count,
                            'available_slots': available_slots
                        }
                        assignment_reasons[m_name]['considered_ocs'].append(oc_entry)
                        
                        # If assigned above max recommended level, set a backup/alternative assignment
                        if m_max_oc is not None and oc_difficulty > m_max_oc:
                            backup_member = next((m for m in members_sorted if m['member_name'] == m_name), None)
                            if backup_member is not None:
                                for level in range(m_max_oc, 0, -1):
                                    if assignment_reasons[m_name].get('backup_oc_id') is not None:
                                        break
                                    for backup_oc in ocs:
                                        if is_excluded_oc(backup_oc):
                                            continue
                                        try:
                                            b_difficulty = int(backup_oc.get('difficulty'))
                                        except (ValueError, TypeError):
                                            continue
                                        if b_difficulty != level:
                                            continue
                                        if not can_member_join_oc(backup_member, backup_oc):
                                            continue
                                        b_oc_id = backup_oc.get('oc_id')
                                        b_total = backup_oc.get('total_slots', 0)
                                        b_filled = backup_oc.get('filled_slots', 0)
                                        b_assigned = len(assignments.get(b_oc_id, []))
                                        if b_total - b_filled - b_assigned <= 0:
                                            continue
                                        assignment_reasons[m_name]['backup_oc_id'] = b_oc_id
                                        assignment_reasons[m_name]['backup_oc_name'] = backup_oc.get('oc_name', 'Unknown')
                                        assignment_reasons[m_name]['backup_level'] = b_difficulty
                                        logger.debug(f"Backup assignment for {m_name}: Level {b_difficulty} - {assignment_reasons[m_name]['backup_oc_name']} (ID: {b_oc_id})")
                                        break
                    
                    if len(members_to_assign) > 1:
                        logger.info(f"Grouped {len(members_to_assign)} members together in OC {oc_id} ({oc.get('oc_name')}): {', '.join(members_to_assign)}")
                    
                    if member_name in ["Adilon_Scorpian", "Hiyori", "Xython", "April-x", "Signa"]:
                        logger.info(f"DEBUG {member_name}: ASSIGNED to Level {oc_difficulty} OC '{oc.get('oc_name')}' - stopping search")
                    break
                else:
                    if member_name in ["Adilon_Scorpian", "Hiyori", "DubZzZ"]:
                        logger.info(f"DEBUG {member_name}: OC {oc_id} ({oc_name}, Level {oc_difficulty}) has no available slots (total: {total_slots}, filled: {filled_slots}, assigned: {assigned_count})")
                    oc_entry = {
                        'oc_id': oc_id,
                        'oc_name': oc_name,
                        'level': oc_difficulty,
                        'reason_skipped': f'No available slots (total: {total_slots}, filled: {filled_slots}, assigned: {assigned_count})'
                    }
                    enrich_oc_entry(oc_entry, oc_name)
                    assignment_reasons[member_name]['considered_ocs'].append(oc_entry)
            
            # After main loop, evaluate higher-level OCs to show why they weren't considered
            # This helps users understand why members weren't advanced to more difficult OCs
            if member_max_oc is not None and has_oc_history:
                member_perf = member_performance.get(member_name, {})
                member_level_rates = member_perf.get('level_rates', {})
                member_highest_level_rate = member_perf.get('highest_level_rate')
                member_oc_specific_rates = member_oc_rates.get(member_name, {})
                
                # Use the member's highest checkpoint rate from highest difficulty rank OC in historical DB
                # This is the "move up" calculation - using drop_from_prev from Historical DB
                best_rate_for_eval = None
                best_rate_source_eval = None
                highest_historical_eval = member_highest_historical.get(member_name, {})
                # Prefer highest historical OC rate from Historical DB
                if highest_historical_eval.get('checkpoint_rate') is not None:
                    try:
                        best_rate_for_eval = float(highest_historical_eval.get('checkpoint_rate'))
                        if 0 <= best_rate_for_eval <= 1:
                            best_rate_for_eval = best_rate_for_eval * 100
                        best_rate_source_eval = f"highest historical OC (rank {highest_historical_eval.get('oc_rank', 'N/A')})"
                    except (ValueError, TypeError):
                        pass
                # Fallback to closest difficulty rate (member_max_oc level) if highest historical not available
                if best_rate_for_eval is None and member_max_oc is not None and member_level_rates:
                    closest_difficulty_rate = member_level_rates.get(member_max_oc)
                    if closest_difficulty_rate is not None:
                        try:
                            best_rate_for_eval = float(closest_difficulty_rate)
                            if 0 <= best_rate_for_eval <= 1:
                                best_rate_for_eval = best_rate_for_eval * 100
                            best_rate_source_eval = f"Level {member_max_oc} rate"
                        except (ValueError, TypeError):
                            pass
                # Final fallback to highest_level_rate
                if best_rate_for_eval is None and member_highest_level_rate is not None:
                    try:
                        best_rate_for_eval = float(member_highest_level_rate)
                        if 0 <= best_rate_for_eval <= 1:
                            best_rate_for_eval = best_rate_for_eval * 100
                        best_rate_source_eval = f"highest level rate"
                    except (ValueError, TypeError):
                        pass
                
                # Check all OCs that are above the member's max recommended level
                # Use the original ocs list to ensure we check all available OCs
                for oc in ocs:
                    if is_excluded_oc(oc):
                        continue
                    
                    oc_id = oc.get('oc_id')
                    oc_name = oc.get('oc_name', '').strip()
                    oc_difficulty = oc.get('difficulty')
                    if oc_difficulty is None:
                        continue
                    try:
                        oc_difficulty = int(oc_difficulty)
                    except (ValueError, TypeError):
                        continue
                    
                    # Only check OCs above max recommended level (including Level max_oc+1, max_oc+2, etc.)
                    # This ensures we evaluate Level 4 OCs when max is 3, Level 5 when max is 4, etc.
                    if oc_difficulty <= member_max_oc:
                        continue
                    
                    # Check if already in considered_ocs - if so, we'll still evaluate it to ensure
                    # the "above max level" reason is included, but we'll update the reason if needed
                    existing_entry = None
                    for c in assignment_reasons.get(member_name, {}).get('considered_ocs', []):
                        if c.get('oc_id') == oc_id and c.get('level') == oc_difficulty:
                            existing_entry = c
                            break
                    
                    # If already in list but reason doesn't mention "above max level", we'll add it
                    # Otherwise, if it's already there with the right reason, skip to avoid duplicates
                    if existing_entry and 'above max recommended level' in existing_entry.get('reason_skipped', '').lower():
                        continue
                    
                    # Get OC's difficulty rank and calculate predicted checkpoint rate
                    oc_name_normalized = oc_name.strip().lower()
                    oc_rank = oc_rank_map.get(oc_name_normalized)
                    highest_historical = member_highest_historical.get(member_name, {})
                    highest_rate = highest_historical.get('checkpoint_rate')
                    highest_oc_rank = highest_historical.get('oc_rank')
                    predicted_rate = None
                    if highest_rate is not None and oc_rank is not None:
                        # If considered OC has lower difficulty rank than member's highest, and member's rate >= 80%,
                        # the member should be able to handle it (no drop calculation needed)
                        if (highest_oc_rank is not None and 
                            oc_rank is not None and 
                            oc_rank < highest_oc_rank and 
                            highest_rate >= 80):
                            # Member can handle this OC - use their highest rate as predicted rate
                            predicted_rate = highest_rate
                        else:
                            # Calculate cumulative drop: sum actual drop values for each rank step
                            if highest_oc_rank is not None and oc_rank is not None and oc_rank > highest_oc_rank:
                                cumulative_drop = calculate_cumulative_drop(highest_oc_rank, oc_rank)
                                if cumulative_drop is not None and cumulative_drop > 0:
                                    predicted_rate = highest_rate - cumulative_drop
                                else:
                                    # Fallback: use single drop if cumulative can't be calculated
                                    drop = oc_drop_map.get(oc_name_normalized)
                                    if drop is not None:
                                        try:
                                            drop_float = float(drop)
                                            if drop_float > 0:
                                                predicted_rate = highest_rate - drop_float
                                            else:
                                                predicted_rate = highest_rate
                                        except (ValueError, TypeError):
                                            predicted_rate = highest_rate
                                    else:
                                        predicted_rate = highest_rate
                            elif highest_oc_rank is not None and oc_rank is not None and oc_rank == highest_oc_rank:
                                # Same rank - use highest rate
                                predicted_rate = highest_rate
                            else:
                                # No rank comparison possible
                                predicted_rate = highest_rate
                    
                    # Evaluate why this higher-level OC wasn't considered
                    reason_parts = []
                    reason_parts.append(f'OC level {oc_difficulty} is above max recommended level {member_max_oc}')
                    if oc_rank is not None:
                        reason_parts.append(f'Difficulty rank: {oc_rank}')
                    if predicted_rate is not None and highest_oc_rank is not None and oc_rank is not None and oc_rank > highest_oc_rank:
                        # Calculate cumulative drop for display
                        cumulative_drop = calculate_cumulative_drop(highest_oc_rank, oc_rank)
                        if cumulative_drop is not None:
                            drops_list = [f"{rank_to_drop_map.get(r, 0):.1f}%" for r in range(highest_oc_rank + 1, oc_rank + 1) if r in rank_to_drop_map]
                            drops_sum_str = ' + '.join(drops_list)
                            reason_parts.append(f'Predicted checkpoint rate: {highest_rate:.1f}% - ({drops_sum_str}) = {highest_rate:.1f}% - {cumulative_drop:.1f}% = {predicted_rate:.1f}%')
                        else:
                            # Fallback display
                            drop_per_rank = oc_drop_map.get(oc_name_normalized, 0)
                            reason_parts.append(f'Predicted checkpoint rate: {highest_rate:.1f}% - {drop_per_rank:.1f}% = {predicted_rate:.1f}%')
                    
                    # Check if member has OC-specific data for this OC
                    has_oc_specific_data = False
                    best_oc_rate = None
                    for key, rate in member_oc_specific_rates.items():
                        if key.lower().startswith(oc_name.lower() + '_'):
                            has_oc_specific_data = True
                            try:
                                rate_num = float(rate)
                                if 0 <= rate_num <= 1:
                                    rate_num = rate_num * 100
                                if best_oc_rate is None or rate_num > best_oc_rate:
                                    best_oc_rate = rate_num
                            except (ValueError, TypeError):
                                pass
                    
                    if has_oc_specific_data and best_oc_rate is not None:
                        # Check if OC-specific rate would qualify
                        if oc_difficulty == 1:
                            if not (0 <= best_oc_rate <= 90):
                                reason_parts.append(f'OC-specific rate {best_oc_rate:.1f}% not in 0-90% range')
                        else:
                            if not (80 <= best_oc_rate <= 90):
                                reason_parts.append(f'OC-specific rate {best_oc_rate:.1f}% not in 80-90% range')
                    else:
                        # Check level-based rate
                        level_rate = member_level_rates.get(oc_difficulty)
                        if level_rate is not None:
                            try:
                                rate_num = float(level_rate)
                                if 0 <= rate_num <= 1:
                                    rate_num = rate_num * 100
                                
                                if oc_difficulty == 1:
                                    if not (0 <= rate_num <= 90):
                                        reason_parts.append(f'Level rate {rate_num:.1f}% not in 0-90% range')
                                else:
                                    if not (80 <= rate_num <= 90):
                                        reason_parts.append(f'Level rate {rate_num:.1f}% not in 80-90% range')
                            except (ValueError, TypeError):
                                reason_parts.append('Invalid level rate format')
                        else:
                            # No level rate - check "overcome the drop" using highest historical OC rate with drop_from_prev from Historical DB
                            # IMPORTANT: Drop is ACCUMULATIVE - sum actual drop values for each rank step
                            if best_rate_for_eval is not None:
                                oc_name_normalized = oc_name.strip().lower()
                                oc_rank_for_check = oc_rank_map.get(oc_name_normalized)
                                highest_rank_eval = highest_historical_eval.get('oc_rank')
                                
                                if highest_rank_eval is not None and oc_rank_for_check is not None and oc_rank_for_check > highest_rank_eval:
                                    # Calculate cumulative drop by summing drops for each rank step
                                    cumulative_drop = calculate_cumulative_drop(highest_rank_eval, oc_rank_for_check)
                                    if cumulative_drop is not None and cumulative_drop > 0:
                                        threshold = 0 if oc_difficulty == 1 else 80
                                        calculated_rate = best_rate_for_eval - cumulative_drop
                                        drops_list = [f"{rank_to_drop_map.get(r, 0):.1f}%" for r in range(highest_rank_eval + 1, oc_rank_for_check + 1) if r in rank_to_drop_map]
                                        drops_sum_str = ' + '.join(drops_list)
                                        if calculated_rate >= threshold:
                                            # Actually could qualify via "overcome the drop" - but still above max
                                            reason_parts.append(
                                                f'Could potentially overcome drop using {best_rate_source_eval} '
                                                f'({best_rate_for_eval:.1f}% - ({drops_sum_str}) = {best_rate_for_eval:.1f}% - {cumulative_drop:.1f}% = {calculated_rate:.1f}% >= {threshold}%), '
                                                f'but above max recommended level'
                                            )
                                        else:
                                            reason_parts.append(
                                                f'Cannot overcome drop using {best_rate_source_eval} '
                                                f'({best_rate_for_eval:.1f}% - ({drops_sum_str}) = {best_rate_for_eval:.1f}% - {cumulative_drop:.1f}% = {calculated_rate:.1f}% < {threshold}%)'
                                            )
                                else:
                                    reason_parts.append(
                                        f'No level rate data for Level {oc_difficulty} and no drop_from_prev data available for "overcome the drop" rule. '
                                        f'{best_rate_source_eval}: {best_rate_for_eval:.1f}%'
                                    )
                            else:
                                reason_parts.append(
                                    f'No level rate data for Level {oc_difficulty} and no highest historical OC rate available. '
                                    f'Max recommended: Level {member_max_oc}'
                                )
                    
                    # Add to considered_ocs with comprehensive reason, or update existing entry
                    oc_entry = {
                        'oc_id': oc_id,
                        'oc_name': oc_name,
                        'level': oc_difficulty,
                        'oc_rank': oc_rank,
                        'predicted_checkpoint_rate': predicted_rate,
                        'reason_skipped': '; '.join(reason_parts)
                    }
                    if existing_entry:
                        # Don't overwrite entries that are already selected for assignment
                        # They already have the correct predicted rate and detailed reason
                        if 'Selected for assignment' in existing_entry.get('reason_skipped', ''):
                            # This OC was selected - don't modify it, just skip
                            continue
                        
                        # Update the existing entry to include "above max level" reason and new fields
                        existing_reason = existing_entry.get('reason_skipped', '')
                        if 'above max recommended level' not in existing_reason.lower():
                            # Prepend the "above max level" reason to the existing reason
                            existing_entry['reason_skipped'] = '; '.join(reason_parts) + '; ' + existing_reason
                        # Update oc_rank and predicted_rate if not already set
                        if oc_rank is not None and existing_entry.get('oc_rank') is None:
                            existing_entry['oc_rank'] = oc_rank
                        if predicted_rate is not None and existing_entry.get('predicted_checkpoint_rate') is None:
                            existing_entry['predicted_checkpoint_rate'] = predicted_rate
                    else:
                        # Add new entry
                        assignment_reasons[member_name]['considered_ocs'].append(oc_entry)
            
            # If member wasn't assigned and has OC history, try all OCs (not just activity-based list)
            if member_name not in assigned_members and has_oc_history:
                # Fallback: try all OCs regardless of activity status
                # Use sorted list (combine active and inactive, then sort) to maintain priority
                all_ocs_sorted = (ocs_for_active + ocs_for_inactive)
                all_ocs_sorted.sort(key=sort_key)
                
                member_perf = member_performance.get(member_name, {})
                member_max_oc = member_perf.get('max_recommended_oc')
                member_level_rates = member_perf.get('level_rates', {})
                
                for oc in all_ocs_sorted:
                    # Skip No Reserve OC
                    if is_excluded_oc(oc):
                        continue
                    
                    oc_difficulty = oc.get('difficulty')
                    if oc_difficulty is None:
                        continue
                    
                    try:
                        oc_difficulty = int(oc_difficulty)
                    except (ValueError, TypeError):
                        continue
                    
                    # CRITICAL: Check max_recommended_oc FIRST, before doing any rate checks
                    # This prevents wasting time checking OCs that are too high for the member
                    if member_max_oc is not None and oc_difficulty > member_max_oc:
                        # Don't add to considered_ocs for fallback loop - these shouldn't be considered at all
                        continue
                    
                    # Check if member has valid checkpoint_pass_rate for this SPECIFIC OC
                    oc_name = oc.get('oc_name', '').strip()
                    member_oc_specific_rates = member_oc_rates.get(member_name, {})
                    
                    # Check if member has any position for this specific OC with rate in 80-90
                    has_valid_oc_rate = False
                    has_oc_specific_data = False
                    best_oc_rate = None
                    
                    for key, rate in member_oc_specific_rates.items():
                        # Key format is "oc_name_position_id", so check if it starts with oc_name
                        if key.lower().startswith(oc_name.lower() + '_'):
                            has_oc_specific_data = True
                            try:
                                rate_num = float(rate)
                                if 0 <= rate_num <= 1:
                                    rate_num = rate_num * 100
                                
                                # Track best rate for this OC
                                if best_oc_rate is None or rate_num > best_oc_rate:
                                    best_oc_rate = rate_num
                                
                                # Level 1: 0-90%, Level 2+: 80-90%
                                if oc_difficulty == 1:
                                    if 0 <= rate_num <= 90:
                                        has_valid_oc_rate = True
                                else:
                                    if 80 <= rate_num <= 90:
                                        has_valid_oc_rate = True
                            except (ValueError, TypeError):
                                continue
                    
                    # If we have OC-specific data and member can't meet requirements, skip this OC
                    # Level 1 OCs: 0-90%, Level 2+ OCs: 80-90%
                    if has_oc_specific_data and not has_valid_oc_rate:
                        if oc_difficulty == 1:
                            # For Level 1, check if rate is in 0-90% range
                            if best_oc_rate is not None and 0 <= best_oc_rate <= 90:
                                has_valid_oc_rate = True
                            else:
                                logger.info(f"Fallback: Skipping {member_name} for Level {oc_difficulty} OC '{oc_name}': has OC-specific data with best rate {best_oc_rate} (not in 0-90 range)")
                                continue
                        else:
                            logger.info(f"Fallback: Skipping {member_name} for Level {oc_difficulty} OC '{oc_name}': has OC-specific data with best rate {best_oc_rate} (not in 80-90 range)")
                            continue
                    
                    # If no OC-specific data, fallback to level-based check
                    if not has_oc_specific_data:
                        level_rate = member_level_rates.get(oc_difficulty)
                        if level_rate is None:
                            continue
                        
                        # Ensure rate is in percentage format
                        try:
                            rate_num = float(level_rate)
                            if 0 <= rate_num <= 1:
                                rate_num = rate_num * 100
                        except (ValueError, TypeError):
                            continue
                        
                        # STRICT CHECK: Rate must be in valid range
                        # Level 1 OCs: 0-90%, Level 2+ OCs: 80-90%
                        if oc_difficulty == 1:
                            if not (0 <= rate_num <= 90):
                                continue
                        else:
                            if not (80 <= rate_num <= 90):
                                continue
                    
                    oc_id = oc['oc_id']
                    
                    # Check if OC has space
                    if oc_id not in assignments:
                        assignments[oc_id] = []
                    
                    # Check available slots
                    total_slots = oc.get('total_slots', 0)
                    filled_slots = oc.get('filled_slots', 0)
                    assigned_count = len(assignments[oc_id])
                    available_slots = total_slots - filled_slots - assigned_count
                    
                    if available_slots > 0:
                        assignments[oc_id].append(member_name)
                        assigned_members.add(member_name)
                        logger.debug(f"Fallback: Assigned {member_name} to OC {oc_id} (Level {oc_difficulty})")
                        assignment_reasons[member_name]['assigned_oc_id'] = oc_id
                        assignment_reasons[member_name]['assigned_oc_name'] = oc.get('oc_name', 'Unknown')
                        assignment_reasons[member_name]['assigned_level'] = oc_difficulty
                        assignment_reasons[member_name]['reason'] = 'fallback'
                        break
            
            # If still not assigned and no OC history, try all Level 1 OCs
            if member_name not in assigned_members and not has_oc_history:
                for oc in ocs:
                    # Skip No Reserve OC
                    if is_excluded_oc(oc):
                        continue
                    
                    difficulty = oc.get('difficulty')
                    if difficulty is None:
                        continue
                    
                    try:
                        difficulty = int(difficulty)
                    except (ValueError, TypeError):
                        continue
                    
                    if difficulty != 1:
                        continue
                    
                    oc_id = oc['oc_id']
                    
                    # Check if OC has space
                    if oc_id not in assignments:
                        assignments[oc_id] = []
                    
                    # Check available slots
                    total_slots = oc.get('total_slots', 0)
                    filled_slots = oc.get('filled_slots', 0)
                    assigned_count = len(assignments[oc_id])
                    available_slots = total_slots - filled_slots - assigned_count
                    
                    if available_slots > 0:
                        assignments[oc_id].append(member_name)
                        assigned_members.add(member_name)
                        logger.debug(f"Assigned new member {member_name} to Level 1 OC {oc_id}")
                        assignment_reasons[member_name]['assigned_oc_id'] = oc_id
                        assignment_reasons[member_name]['assigned_oc_name'] = oc.get('oc_name', 'Unknown')
                        assignment_reasons[member_name]['assigned_level'] = 1
                        assignment_reasons[member_name]['reason'] = 'new_member'
                        break

        # Handle members who couldn't be assigned to their recommended level
        # Try to assign them to lower levels where they can meet requirements
        # BUT only if there are NO available OCs at their max recommended level
        unassigned_members = [m for m in members_sorted if m['member_name'] not in assigned_members]
        
        # First, handle members with no OC history who weren't assigned to Level 1 OCs
        # This is a fallback in case they were skipped in the main loop
        for member in unassigned_members:
            member_name = member['member_name']
            member_id = member['member_id']
            has_oc_history = member_id in members_with_oc_history
            
            if not has_oc_history:
                logger.info(f"Fallback: Processing member {member_name} (no OC history) for Level 1 assignment")
                # Try to assign to Level 1 OCs - check ALL OCs, not just filtered lists
                level_1_ocs_found = 0
                level_1_ocs_available = 0
                for oc in ocs:
                    if is_excluded_oc(oc):
                        continue
                    
                    difficulty = oc.get('difficulty')
                    if difficulty is None:
                        continue
                    
                    try:
                        difficulty = int(difficulty)
                    except (ValueError, TypeError):
                        continue
                    
                    if difficulty != 1:
                        continue
                    
                    level_1_ocs_found += 1
                    oc_id = oc['oc_id']
                    
                    # Check if OC has space
                    if oc_id not in assignments:
                        assignments[oc_id] = []
                    
                    # Check available slots
                    total_slots = oc.get('total_slots', 0)
                    filled_slots = oc.get('filled_slots', 0)
                    assigned_count = len(assignments[oc_id])
                    available_slots = total_slots - filled_slots - assigned_count
                    
                    if available_slots > 0:
                        level_1_ocs_available += 1
                        assignments[oc_id].append(member_name)
                        assigned_members.add(member_name)
                        logger.info(f"Fallback: Assigned new member {member_name} to Level 1 OC {oc_id} ({oc.get('oc_name')}) - {available_slots} slots available")
                        if member_name not in assignment_reasons:
                            # Get member's highest historical OC data
                            highest_historical_new = member_highest_historical.get(member_name, {})
                            assignment_reasons[member_name] = {
                                'assigned_oc_id': None,
                                'assigned_oc_name': None,
                                'assigned_level': None,
                                'max_recommended_oc': None,
                                'reason': None,
                                'grouped_with': [],
                                'considered_ocs': [],
                                'warnings': [],
                                'highest_historical_oc': highest_historical_new  # Store for display
                            }
                        assignment_reasons[member_name]['assigned_oc_id'] = oc_id
                        assignment_reasons[member_name]['assigned_oc_name'] = oc.get('oc_name', 'Unknown')
                        assignment_reasons[member_name]['assigned_level'] = 1
                        assignment_reasons[member_name]['reason'] = 'new_member'
                        break
                    else:
                        logger.debug(f"Fallback: Level 1 OC {oc_id} ({oc.get('oc_name')}) has no available slots (total: {total_slots}, filled: {filled_slots}, assigned: {assigned_count})")
                
                if member_name not in assigned_members:
                    logger.warning(f"Fallback: Could not assign {member_name} to Level 1 OC - found {level_1_ocs_found} Level 1 OCs, {level_1_ocs_available} with available slots")
                    if member_name not in assignment_reasons:
                        # Get member's highest historical OC data
                        highest_historical_unassigned = member_highest_historical.get(member_name, {})
                        assignment_reasons[member_name] = {
                            'assigned_oc_id': None,
                            'assigned_oc_name': None,
                            'assigned_level': None,
                            'max_recommended_oc': None,
                            'reason': 'unassigned',
                            'grouped_with': [],
                            'considered_ocs': [],
                            'warnings': [f'No Level 1 OCs available (found {level_1_ocs_found} Level 1 OCs, {level_1_ocs_available} with available slots)'],
                            'highest_historical_oc': highest_historical_unassigned  # Store for display
                        }
        
        # Now handle members with OC history who couldn't be assigned
        unassigned_members = [m for m in members_sorted if m['member_name'] not in assigned_members]
        
        for member in unassigned_members:
            member_name = member['member_name']
            member_id = member['member_id']
            has_oc_history = member_id in members_with_oc_history
            
            if not has_oc_history:
                continue  # Already handled above
            
            member_perf = member_performance.get(member_name, {})
            member_max_oc = member_perf.get('max_recommended_oc')
            member_level_rates = member_perf.get('level_rates', {})
            
            if member_max_oc is None:
                continue
            
            # First, check if there are any available OCs at the member's max recommended level
            # Only assign to lower levels if NO OCs are available at max level
            has_available_oc_at_max = False
            for oc in ocs:
                if is_excluded_oc(oc):
                    continue
                
                oc_difficulty = oc.get('difficulty')
                if oc_difficulty is None:
                    continue
                
                try:
                    oc_difficulty = int(oc_difficulty)
                except (ValueError, TypeError):
                    continue
                
                if oc_difficulty != member_max_oc:
                    continue
                
                # Check if member can join this OC
                if can_member_join_oc(member, oc):
                    oc_id = oc['oc_id']
                    total_slots = oc.get('total_slots', 0)
                    filled_slots = oc.get('filled_slots', 0)
                    assigned_count = len(assignments.get(oc_id, []))
                    available_slots = total_slots - filled_slots - assigned_count
                    
                    if available_slots > 0:
                        has_available_oc_at_max = True
                        warning_msg = f"Member {member_name} (max OC {member_max_oc}) was not assigned but Level {member_max_oc} OC '{oc.get('oc_name')}' ({oc_id}) is available with {available_slots} slots. This indicates a bug in the assignment logic."
                        logger.warning(warning_msg)
                        if member_name not in assignment_reasons:
                            # Get member's highest historical OC data
                            highest_historical_warning = member_highest_historical.get(member_name, {})
                            assignment_reasons[member_name] = {
                                'assigned_oc_id': None,
                                'assigned_oc_name': None,
                                'assigned_level': None,
                                'max_recommended_oc': member_max_oc,
                                'reason': None,
                                'grouped_with': [],
                                'considered_ocs': [],
                                'warnings': [],
                                'highest_historical_oc': highest_historical_warning  # Store for display
                            }
                        assignment_reasons[member_name]['warnings'].append(warning_msg)
                        break
            
            # If there are available OCs at max level, skip lower level assignment
            # This prevents assigning members below their max when suitable OCs exist
            if has_available_oc_at_max:
                logger.info(f"Skipping lower level assignment for {member_name}: Level {member_max_oc} OCs are available")
                continue
            
            # Try to find a lower level where member can meet requirements
            for level in range(member_max_oc - 1, 0, -1):
                level_rate = member_level_rates.get(level)
                if level_rate is None:
                    continue
                
                # Check if rate is in valid range (80-90)
                try:
                    rate_num = float(level_rate)
                    if 0 <= rate_num <= 1:
                        rate_num = rate_num * 100
                    if not (80 <= rate_num <= 90):
                        continue
                except (ValueError, TypeError):
                    continue
                
                # Find an OC at this level
                for oc in ocs:
                    if is_excluded_oc(oc):
                        continue
                    
                    oc_difficulty = oc.get('difficulty')
                    if oc_difficulty is None:
                        continue
                    
                    try:
                        oc_difficulty = int(oc_difficulty)
                    except (ValueError, TypeError):
                        continue
                    
                    if oc_difficulty != level:
                        continue
                    
                    oc_id = oc['oc_id']
                    
                    # Check if OC has space
                    if oc_id not in assignments:
                        assignments[oc_id] = []
                    
                    total_slots = oc.get('total_slots', 0)
                    filled_slots = oc.get('filled_slots', 0)
                    assigned_count = len(assignments[oc_id])
                    available_slots = total_slots - filled_slots - assigned_count
                    
                    if available_slots > 0:
                        assignments[oc_id].append(member_name)
                        assigned_members.add(member_name)
                        logger.info(f"Assigned {member_name} to lower level {level} OC {oc_id} (couldn't meet requirements for level {member_max_oc})")
                        assignment_reasons[member_name]['assigned_oc_id'] = oc_id
                        assignment_reasons[member_name]['assigned_oc_name'] = oc.get('oc_name', 'Unknown')
                        assignment_reasons[member_name]['assigned_level'] = level
                        assignment_reasons[member_name]['reason'] = 'lower_level'
                        assignment_reasons[member_name]['warnings'].append(f'Assigned to Level {level} instead of max recommended Level {member_max_oc} (no suitable OCs available at max level)')
                        break
                
                if member_name in assigned_members:
                    break
        
        # Track members who need higher level OCs spawned
        # Members who have max_recommended_oc but no available OCs at that level
        spawn_suggestions = {}  # level -> [member_names]

        # Pre-compute how many open slots exist per level across all available OCs.
        # This is used for spawn suggestions so we don't recommend spawning when an OC already exists.
        available_slots_by_level: Dict[int, int] = {}
        for oc in ocs:
            if is_excluded_oc(oc):
                continue
            oc_difficulty = oc.get("difficulty")
            if oc_difficulty is None:
                continue
            try:
                level = int(oc_difficulty)
            except (ValueError, TypeError):
                continue

            total_slots = oc.get("total_slots", 0) or 0
            filled_slots = oc.get("filled_slots", 0) or 0
            assigned_count = len(assignments.get(oc["oc_id"], []))
            try:
                open_slots = int(total_slots) - int(filled_slots) - int(assigned_count)
            except (ValueError, TypeError):
                open_slots = 0
            if open_slots > 0:
                available_slots_by_level[level] = available_slots_by_level.get(level, 0) + open_slots
        
        for member in members_sorted:
            member_name = member['member_name']
            if member_name in assigned_members:
                continue
            
            member_id = member['member_id']
            has_oc_history = member_id in members_with_oc_history
            if not has_oc_history:
                continue
            
            member_perf = member_performance.get(member_name, {})
            member_max_oc = member_perf.get('max_recommended_oc')
            member_level_rates = member_perf.get('level_rates', {})
            
            if member_max_oc is None:
                continue
            
            # Only suggest spawning when there are *no* open slots at that level.
            # Do NOT gate this on member performance rates; otherwise we can suggest spawning
            # even when an OC already exists (e.g. members "recommended" to a higher level).
            has_available_oc = available_slots_by_level.get(int(member_max_oc), 0) > 0
            
            # If no available OC at recommended level, suggest spawning
            if not has_available_oc:
                if member_max_oc not in spawn_suggestions:
                    spawn_suggestions[member_max_oc] = []
                spawn_suggestions[member_max_oc].append(member_name)
        
        # Log unassigned members for debugging and add to assignment reasons
        unassigned = [m['member_name'] for m in members_sorted if m['member_name'] not in assigned_members]
        if unassigned:
            logger.info(f"Unassigned members ({len(unassigned)}): {', '.join(unassigned)}")
            for member_name in unassigned:
                if member_name not in assignment_reasons:
                    member_perf = member_performance.get(member_name, {})
                    # Get member's highest historical OC data
                    highest_historical_final = member_highest_historical.get(member_name, {})
                    assignment_reasons[member_name] = {
                        'assigned_oc_id': None,
                        'assigned_oc_name': None,
                        'assigned_level': None,
                        'max_recommended_oc': member_perf.get('max_recommended_oc'),
                        'reason': 'unassigned',
                        'grouped_with': [],
                        'considered_ocs': [],
                        'warnings': ['Member was not assigned to any OC'],
                        'highest_historical_oc': highest_historical_final  # Store for display
                    }
        
        if spawn_suggestions:
            logger.info(f"OC spawn suggestions: {spawn_suggestions}")
            logger.info(f"Will build needed_ocs list for levels: {list(spawn_suggestions.keys())}")

        # Generate email text using form letter format
        email_lines: List[str] = []

        # Preface text (configurable/saved). Preserve line breaks exactly, including trailing
        # blank lines. `splitlines()` drops trailing empty lines when the string ends with \n,
        # so we normalize and split on '\n' instead.
        if email_preface is not None:
            normalized_preface = email_preface.replace("\r\n", "\n")
            email_lines.extend(normalized_preface.split("\n"))

        # Group assignments by level (6, 5, 4, 3, 2, 1), then by OC
        # Structure: level -> [oc_id] -> [assignments]
        # Use sorted OCs list to maintain priority order
        assignments_by_level = {}  # level -> {oc_id: [assignments]}
        
        for oc in ocs_sorted:
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
            
            # Get all OCs at this level that have assignments, and sort them by priority
            # Priority: partially filled OCs first, then by members needed
            # Use sorted OCs list to maintain priority order
            level_ocs_with_assignments = []
            for oc in ocs_sorted:
                oc_id = oc['oc_id']
                if oc_id in level_assignments:
                    level_ocs_with_assignments.append(oc)
            
            # Sort OCs at this level by the same priority used for assignment:
            # 1. Partially filled OCs first (filled_slots > 0)
            # 2. Members needed (ascending) - OCs that need fewer members first
            def level_oc_sort_key(oc):
                total_slots = oc.get('total_slots', 0)
                filled_slots = oc.get('filled_slots', 0)
                members_needed = max(0, total_slots - filled_slots)
                is_partially_filled = 1 if filled_slots > 0 else 0
                # Return tuple: (negative is_partially_filled for descending, members_needed for ascending)
                return (-is_partially_filled, members_needed)
            
            level_ocs_with_assignments.sort(key=level_oc_sort_key)
            
            # Output each OC at this level in priority order
            for oc in level_ocs_with_assignments:
                oc_id = oc['oc_id']
                oc_name = oc['oc_name']
                # Use the correct URL format: #/tab=crimes&crimeId=
                oc_url = f"https://www.torn.com/factions.php?step=your#/tab=crimes&crimeId={oc_id}"
                
                # Format: Lv <number> - <OC Name> - <OC URL>
                email_lines.append(f"Lv {level} - {oc_name} - {oc_url}")
                
                # Member list (one per line with dashes); include backup/alternative when assigned above max
                for member_name in level_assignments[oc_id]:
                    email_lines.append(f"- {member_name}")
                    reason = assignment_reasons.get(member_name, {})
                    if reason.get('backup_oc_id') and reason.get('backup_oc_name') is not None and reason.get('backup_level') is not None:
                        backup_url = f"https://www.torn.com/factions.php?step=your#/tab=crimes&crimeId={reason['backup_oc_id']}"
                        email_lines.append(f"  If too difficult: Lv {reason['backup_level']} - {reason['backup_oc_name']} - {backup_url}")
                
                # Add 2 blank lines between each OC assignment for proper email formatting
                email_lines.append("")
                email_lines.append("")
        
        # Build list of needed OCs (for UI display)
        needed_ocs = []  # List of {level: int, oc_names: [str]}
        
        # Check for members assigned below their max recommended level
        # This identifies OCs that should be spawned so members can be assigned to higher levels
        members_assigned_below_max = {}  # level -> [member_names]
        
        for oc in ocs:
            oc_id = oc['oc_id']
            if oc_id not in assignments or not assignments[oc_id]:
                continue
            
            oc_difficulty = oc.get('difficulty')
            if oc_difficulty is None:
                continue
            try:
                oc_difficulty = int(oc_difficulty)
            except (ValueError, TypeError):
                continue
            
            # Check each assigned member
            for member_name in assignments[oc_id]:
                member_perf = member_performance.get(member_name, {})
                member_max_oc = member_perf.get('max_recommended_oc')
                
                # If member is assigned to a level below their max, suggest spawning at max level
                if (
                    member_max_oc is not None
                    and oc_difficulty < member_max_oc
                    and available_slots_by_level.get(int(member_max_oc), 0) <= 0
                ):
                    if member_max_oc not in members_assigned_below_max:
                        members_assigned_below_max[member_max_oc] = []
                    if member_name not in members_assigned_below_max[member_max_oc]:
                        members_assigned_below_max[member_max_oc].append(member_name)
        
        # Merge with spawn_suggestions (for unassigned members)
        all_needed_levels = set(spawn_suggestions.keys()) | set(members_assigned_below_max.keys())
        
        # Build needed_ocs list for UI display (but don't add to email text)
        if spawn_suggestions or members_assigned_below_max:
            # Get OC names for each level from historical data
            # Query for common OC names at each level that needs spawning
            level_oc_names = {}  # level -> [oc_names]
            for oc in ocs:
                if is_excluded_oc(oc):
                    continue
                difficulty = oc.get('difficulty')
                if difficulty is None:
                    continue
                try:
                    level = int(difficulty)
                except (ValueError, TypeError):
                    continue
                
                oc_name = oc.get('oc_name')
                if oc_name:
                    if level not in level_oc_names:
                        level_oc_names[level] = []
                    if oc_name not in level_oc_names[level]:
                        level_oc_names[level].append(oc_name)
            
            # For levels that need spawning, get OC names from historical data
            # Include both unassigned members and members assigned below max
            needed_levels = sorted(all_needed_levels, reverse=True)
            for level in needed_levels:
                # Combine members from both sources
                member_names = list(set(
                    (spawn_suggestions.get(level, [])) + 
                    (members_assigned_below_max.get(level, []))
                ))
                if not member_names:
                    continue
                
                # Get OC names for this level from historical data
                # Query for distinct OC names at this level
                try:
                    query = f"""
                    SELECT DISTINCT
                      name AS oc_name
                    FROM
                      `torncity-402423.torn_data.v2_faction_40832_crimes-raw`
                    WHERE
                      difficulty = {level}
                      AND name IS NOT NULL
                      AND name != ''
                    ORDER BY
                      name ASC
                    LIMIT 10
                    """
                    historical_ocs = self.bq.execute_query(query)
                    suggested_oc_names = [
                        row["oc_name"] for row in historical_ocs if row.get("oc_name")
                    ]
                    # Apply the same exclusion rules as the rest of the generator.
                    if excluded_oc_names:
                        excluded_lower = [n.lower() for n in excluded_oc_names]
                        suggested_oc_names = [
                            name
                            for name in suggested_oc_names
                            if not any(ex in name.lower() for ex in excluded_lower)
                        ]
                    
                    # If we have historical OCs, use them; otherwise use from current OCs
                    if not suggested_oc_names:
                        suggested_oc_names = level_oc_names.get(level, [])
                    
                    # Fallback: use generic names based on level if still no suggestions
                    if not suggested_oc_names:
                        if level == 6:
                            suggested_oc_names = ["Counter Offer", "Guardian Angels", "Bidding War"]
                        elif level == 5:
                            suggested_oc_names = ["Counter Offer", "Guardian Ángels"]
                        elif level == 4:
                            suggested_oc_names = ["Market Forces", "Mob Mentality"]
                        else:
                            suggested_oc_names = [f"Level {level} OC"]
                    
                    # Store for UI display (limit to 2 most common)
                    if suggested_oc_names:
                        needed_ocs.append({
                            'level': level,
                            'oc_names': suggested_oc_names[:2],
                            'member_names': member_names
                        })
                except Exception as e:
                    logger.warning(f"Error querying historical OCs for level {level}: {e}")
                    # Use fallback names
                    if level == 6:
                        suggested_oc_names = ["Counter Offer", "Guardian Angels", "Bidding War"]
                    elif level == 5:
                        suggested_oc_names = ["Counter Offer", "Guardian Ángels"]
                    elif level == 4:
                        suggested_oc_names = ["Market Forces", "Mob Mentality"]
                    else:
                        suggested_oc_names = [f"Level {level} OC"]
                    
                    if suggested_oc_names:
                        needed_ocs.append({
                            'level': level,
                            'oc_names': suggested_oc_names[:2],
                            'member_names': member_names
                        })


        email_text = "\n".join(email_lines)
        
        # Log what we're returning
        logger.info(f"Returning needed_ocs: {needed_ocs}")
        
        # Optional validation of assignment invariants (gated by OC_ASSIGNMENT_VALIDATE)
        if os.getenv("OC_ASSIGNMENT_VALIDATE"):
            for member_name, data in assignment_reasons.items():
                try:
                    al = data.get("assigned_level")
                    mro = data.get("max_recommended_oc")
                    if al is not None and mro is not None:
                        al_int, mro_int = int(al), int(mro)
                        if al_int > mro_int:
                            warnings = data.get("warnings", [])
                            if not any("Overcome the drop" in str(w) for w in warnings):
                                logger.warning(
                                    f"OC_ASSIGNMENT_VALIDATE: {member_name} assigned_level={al_int} > max_recommended_oc={mro_int} without 'Overcome the drop' in warnings"
                                )
                    for w in data.get("warnings", []) or []:
                        if str(w).startswith("Overcome the drop"):
                            if data.get("assigned_level") is None:
                                logger.warning(
                                    f"OC_ASSIGNMENT_VALIDATE: {member_name} has 'Overcome the drop' warning but assigned_level is None"
                                )
                            break
                except (ValueError, TypeError) as e:
                    logger.debug(f"OC_ASSIGNMENT_VALIDATE skip {member_name}: {e}")
        
        # Return both email text, needed OCs, and assignment reasons
        return {
            'email_text': email_text,
            'needed_ocs': needed_ocs,
            'assignment_reasons': assignment_reasons
        }

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
              os.crime_id,
              os.executed_at,
              os.executed_date,
              os.progress,
              os.outcome,
              CASE
                WHEN os.outcome = 'Successful' THEN 'Success'
                ELSE 'Failure'
              END AS status,
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

