-- OC Difficulty Rankings
--
-- Derived from oc_historical_insights_snapshot. Used to populate
-- torn_data.oc_difficulty_rankings via:
--   CREATE OR REPLACE TABLE `{project}.{dataset}.oc_difficulty_rankings` AS
--   <this SELECT>
--
-- The FROM table should be oc_historical_insights_snapshot in the same project.dataset.

SELECT
  oc_name,
  oc_rank,
  computed_at,
  difficulty,
  oc_checkpoint_rate_score
FROM `torncity-402423.torn_data.oc_historical_insights_snapshot`;
