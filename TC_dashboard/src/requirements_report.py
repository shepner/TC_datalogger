"""Faction Requirements Report for Torn City faction management."""

import logging
from typing import Any, Dict, List

from src.bigquery_client import BigQueryClient

logger = logging.getLogger(__name__)


class RequirementsReport:
    """Generates faction requirements compliance reports."""

    def __init__(self, bigquery_client: BigQueryClient):
        """
        Initialize requirements report generator.

        Args:
            bigquery_client: BigQuery client for querying data
        """
        self.bq = bigquery_client

    def get_requirements_report(self) -> List[Dict[str, Any]]:
        """
        Get faction requirements compliance report for all members.

        Returns:
            List of member requirement dictionaries
        """
        query_file = "sql_queries/faction_requirements.sql"
        
        # Read query from file
        from pathlib import Path
        base_path = Path(__file__).parent.parent.parent
        query_path = base_path / query_file
        
        if not query_path.exists():
            # Try relative to current file
            query_path = Path(__file__).parent.parent.parent / "sql_queries" / "faction_requirements.sql"
        
        if query_path.exists():
            return self.bq.execute_query_file(str(query_path))
        else:
            # Fallback: inline query
            query = """
            WITH member_info AS (
              SELECT
                id AS member_id,
                name AS member_name,
                level,
                position,
                days_in_faction,
                TIMESTAMP_SECONDS(SAFE_CAST(last_action.timestamp AS INT64)) AS last_action_timestamp,
                DATE_DIFF(CURRENT_DATE(), DATE(TIMESTAMP_SECONDS(SAFE_CAST(last_action.timestamp AS INT64))), DAY) AS days_inactive
              FROM
                `torncity-402423.torn_data.v2_faction_40832_members-raw`
            ),
            oc_participation AS (
              SELECT
                slot.user.id AS member_id,
                COUNT(DISTINCT crime.id) AS oc_count_30d
              FROM
                `torncity-402423.torn_data.v2_faction_40832_crimes-raw` AS crime,
                UNNEST(crime.slots) AS slot
              WHERE
                slot.user.id IS NOT NULL
                AND crime.executed_at IS NOT NULL
                AND TIMESTAMP_SECONDS(SAFE_CAST(crime.executed_at AS INT64)) >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
              GROUP BY
                slot.user.id
            ),
            trading_items AS (
              SELECT
                TRIM(REGEXP_EXTRACT(event, r' from (.+)$')) AS user_name,
                SUM(CAST(REGEXP_EXTRACT(event, r'You were sent (\d+)x ') AS INT64)) AS trading_count_30d
              FROM
                `torncity-402423.torn_data.v2_torn_user_events-raw`
              WHERE
                STARTS_WITH(event, 'You were sent')
                AND TIMESTAMP_SECONDS(SAFE_CAST(timestamp AS INT64)) >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
              GROUP BY
                user_name
            ),
            chain_participation AS (
              -- Attackers: array of objects with id field
              SELECT DISTINCT
                CAST(JSON_EXTRACT_SCALAR(participant_json, '$.id') AS INT64) AS member_id
              FROM
                `torncity-402423.torn_data.v2_faction_40832_chains-raw` AS chain,
                UNNEST(JSON_EXTRACT_ARRAY(chain.attackers)) AS participant_json
              WHERE
                JSON_EXTRACT_SCALAR(participant_json, '$.id') IS NOT NULL
                AND chain.end IS NOT NULL
                AND TIMESTAMP_SECONDS(SAFE_CAST(chain.end AS INT64)) >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
              UNION DISTINCT
              -- Non-attackers: array of integers (member IDs directly)
              SELECT DISTINCT
                CAST(participant_id AS INT64) AS member_id
              FROM
                `torncity-402423.torn_data.v2_faction_40832_chains-raw` AS chain,
                UNNEST(JSON_EXTRACT_ARRAY(chain.non_attackers)) AS participant_id
              WHERE
                participant_id IS NOT NULL
                AND participant_id != ''
                AND chain.end IS NOT NULL
                AND TIMESTAMP_SECONDS(SAFE_CAST(chain.end AS INT64)) >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
            )
            SELECT
              m.member_id,
              m.member_name,
              m.level,
              m.position,
              m.days_in_faction,
              m.days_inactive,
              COALESCE(oc.oc_count_30d, 0) AS oc_count_30d,
              COALESCE(oc.oc_count_30d, 0) >= 3 AS oc_requirement_met,
              COALESCE(t.trading_count_30d, 0) AS trading_count_30d,
              COALESCE(t.trading_count_30d, 0) > 480 AS trading_requirement_met,
              cp.member_id IS NOT NULL AS chain_participation,
              -- Requirements met if: (OC >= 3 OR trading > 480) AND chains participation
              -- But if days_in_faction < 30, return NULL (N/A)
              CASE
                WHEN m.days_in_faction < 30 THEN NULL
                ELSE (COALESCE(oc.oc_count_30d, 0) >= 3 OR COALESCE(t.trading_count_30d, 0) > 480)
                     AND cp.member_id IS NOT NULL
              END AS all_requirements_met,
              -- Can promote if requirements met AND active within 2 days
              CASE
                WHEN m.days_in_faction < 30 THEN NULL
                ELSE (COALESCE(oc.oc_count_30d, 0) >= 3 OR COALESCE(t.trading_count_30d, 0) > 480)
                     AND cp.member_id IS NOT NULL
                     AND m.days_inactive <= 2
              END AS can_promote,
              CASE
                -- If less than 30 days in faction, no action
                WHEN m.days_in_faction < 30 THEN 'none'
                -- Level 1: remove if requirements not met
                WHEN m.level = 1 AND NOT (
                  (COALESCE(oc.oc_count_30d, 0) >= 3 OR COALESCE(t.trading_count_30d, 0) > 480)
                  AND cp.member_id IS NOT NULL
                ) THEN 'remove'
                -- Level > 1: promote if requirements met and active
                WHEN m.level > 1 AND (
                  (COALESCE(oc.oc_count_30d, 0) >= 3 OR COALESCE(t.trading_count_30d, 0) > 480)
                  AND cp.member_id IS NOT NULL
                  AND m.days_inactive <= 2
                ) THEN 'promote'
                -- Level > 1: demote if requirements not met
                WHEN m.level > 1 AND NOT (
                  (COALESCE(oc.oc_count_30d, 0) >= 3 OR COALESCE(t.trading_count_30d, 0) > 480)
                  AND cp.member_id IS NOT NULL
                ) THEN 'demote'
                ELSE 'none'
              END AS action
            FROM
              member_info AS m
            LEFT JOIN
              oc_participation AS oc
            ON
              m.member_id = oc.member_id
            LEFT JOIN
              trading_items AS t
            ON
              LOWER(TRIM(m.member_name)) = LOWER(TRIM(t.user_name))
            LEFT JOIN
              chain_participation AS cp
            ON
              m.member_id = cp.member_id
            ORDER BY
              m.level DESC,
              m.member_name ASC
            """
            return self.bq.execute_query(query)

    def generate_action_summary(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Generate summary of actions needed (promote, demote, remove).

        Returns:
            Dictionary with lists of members for each action
        """
        report = self.get_requirements_report()
        
        actions = {
            'promote': [],
            'demote': [],
            'remove': [],
            'none': []
        }
        
        for member in report:
            action = member.get('action', 'none')
            if action in actions:
                actions[action].append(member)
        
        return actions

    def format_action_text(self, actions: Dict[str, List[Dict[str, Any]]]) -> str:
        """
        Format action summary as text for copy/paste.

        Args:
            actions: Dictionary of actions from generate_action_summary()

        Returns:
            Formatted text
        """
        lines = []
        
        if actions['promote']:
            lines.append("=== PROMOTE ===")
            for member in actions['promote']:
                lines.append(f"{member['member_name']} (Level {member['level']} → {member['level'] + 1})")
            lines.append("")
        
        if actions['demote']:
            lines.append("=== DEMOTE ===")
            for member in actions['demote']:
                lines.append(f"{member['member_name']} (Level {member['level']} → {member['level'] - 1})")
            lines.append("")
        
        if actions['remove']:
            lines.append("=== REMOVE ===")
            for member in actions['remove']:
                lines.append(f"{member['member_name']} (Level {member['level']}, requirements not met)")
            lines.append("")
        
        if not any(actions.values()):
            lines.append("No actions needed - all members meet requirements or are inactive.")
        
        return "\n".join(lines)

