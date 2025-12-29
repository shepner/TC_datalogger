-- Faction Members Basic Information Query
-- 
-- Purpose: Extract key member information from the faction members table
-- 
-- Outputs:
--   - id: Member ID
--   - name: Member name
--   - level: Member level
--   - days_in_faction: Days the member has been in the faction
--   - last_action_timestamp: Last action timestamp as datetime
--   - status_state: Current status state
--   - position: Member position in faction
--   - is_in_oc: Whether member is currently in organized crime
--   - fetched_at: Timestamp when the record was fetched from API
--
-- Usage: Run this query in BigQuery Console to get a clean view of all faction members
--        with their current status and activity information.

SELECT
  id,
  name,
  level,
  days_in_faction,
  TIMESTAMP_SECONDS(SAFE_CAST(last_action.timestamp AS INT64)) AS last_action_timestamp,
  status.state AS status_state,
  position,
  is_in_oc,
  fetched_at
FROM
  `torncity-402423.torn_data.v2_faction_40832_members-raw`
ORDER BY
  name ASC;

