-- Chain Participation Tracking
-- 
-- Purpose: Track chain participation per member from chain reports
-- 
-- Outputs:
--   - member_id: Member ID
--   - member_name: Member name
--   - chain_id: Chain ID
--   - chain_start: When the chain started
--   - chain_end: When the chain ended
--   - respect_gained: Respect gained in this chain
--   - attacks: Number of attacks in this chain
--
-- Usage: Run this query to track member chain participation for faction requirements
--        Note: This query assumes chain reports are stored with member participation data

SELECT
  chain.id AS chain_id,
  TIMESTAMP_SECONDS(SAFE_CAST(chain.start AS INT64)) AS chain_start,
  TIMESTAMP_SECONDS(SAFE_CAST(chain.end AS INT64)) AS chain_end,
  CAST(member_id AS INT64) AS member_id,
  JSON_EXTRACT_SCALAR(chain.members, CONCAT('$.', member_id, '.userID')) AS member_name,
  SAFE_CAST(JSON_EXTRACT_SCALAR(chain.members, CONCAT('$.', member_id, '.respect')) AS FLOAT64) AS respect_gained,
  SAFE_CAST(JSON_EXTRACT_SCALAR(chain.members, CONCAT('$.', member_id, '.attacks')) AS INT64) AS attacks
FROM
  `torncity-402423.torn_data.v2_faction_40832_chains-raw` AS chain,
  UNNEST(REGEXP_EXTRACT_ALL(chain.members, r'"(\d+)"\s*:\s*\{')) AS member_id
WHERE
  chain.members IS NOT NULL
ORDER BY
  chain_start DESC,
  member_id ASC;

