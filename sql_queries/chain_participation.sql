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
  participant.id AS member_id,
  participant.name AS member_name,
  participant.respect AS respect_gained,
  participant.attacks AS attacks
FROM
  `torncity-402423.torn_data.v2_faction_40832_chains-raw` AS chain,
  UNNEST(chain.participants) AS participant
WHERE
  participant.id IS NOT NULL
ORDER BY
  chain_start DESC,
  member_name ASC;

