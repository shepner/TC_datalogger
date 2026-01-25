-- OC Historical Insights Snapshot
--
-- Computes per-OC stats: members_needed, planning→ready days, respect/money (total, per-member,
-- per-member-per-day) with min/median/max; OC and position difficulty ranks from checkpoint_pass_rate;
-- position-level attempt/outcome counts. Money = rewards.money + item market value.
--
-- Parameter: @window_days_back INT64 (nullable)
--   - NULL: use all data (for snapshot rebuild); window_days and source_max_executed_at set accordingly.
--   - N: filter ready_at to last N days (on-the-fly); window_days=N, source_max_executed_at=NULL.
--
-- Tables: torn_data.v2_faction_40832_crimes-raw, torn_data.v2_torn_items-raw

WITH
base AS (
  SELECT
    id,
    name AS oc_name,
    difficulty,
    planning_at,
    ready_at,
    executed_at,
    rewards.money AS reward_money,
    rewards.respect AS respect_total,
    rewards.items AS reward_items,
    slots
  FROM `torncity-402423.torn_data.v2_faction_40832_crimes-raw`
  WHERE
    executed_at IS NOT NULL
    AND ready_at IS NOT NULL
    AND planning_at IS NOT NULL
    AND (@window_days_back IS NULL
         OR ready_at >= UNIX_SECONDS(TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @window_days_back DAY)))
),
money_with_items AS (
  SELECT
    b.id,
    b.oc_name,
    b.difficulty,
    b.planning_at,
    b.ready_at,
    b.executed_at,
    b.respect_total,
    b.slots,
    COALESCE(ANY_VALUE(b.reward_money), 0) + COALESCE(SUM(it.quantity * SAFE_CAST(i.value.market_price AS INT64)), 0) AS money_total
  FROM base b
  LEFT JOIN UNNEST(b.reward_items) AS it ON TRUE
  LEFT JOIN `torncity-402423.torn_data.v2_torn_items-raw` AS i ON it.id = i.id
  GROUP BY b.id, b.oc_name, b.difficulty, b.planning_at, b.ready_at, b.executed_at, b.respect_total, b.slots
),
per_run AS (
  SELECT
    oc_name,
    difficulty,
    id,
    ARRAY_LENGTH(slots) AS members_needed,
    (ready_at - planning_at) / 86400.0 AS planning_to_ready_days,
    respect_total,
    money_total,
    respect_total / NULLIF(ARRAY_LENGTH(slots), 0) AS respect_per_member,
    money_total / NULLIF(ARRAY_LENGTH(slots), 0) AS money_per_member,
    (respect_total / NULLIF(ARRAY_LENGTH(slots), 0)) / NULLIF((ready_at - planning_at) / 86400.0, 0) AS respect_per_member_per_day,
    (money_total / NULLIF(ARRAY_LENGTH(slots), 0)) / NULLIF((ready_at - planning_at) / 86400.0, 0) AS money_per_member_per_day,
    ready_at,
    slots
  FROM money_with_items
),
run_medians AS (
  SELECT
    pr.oc_name,
    pr.id,
    FORMAT_TIMESTAMP('%Y-%m', TIMESTAMP_SECONDS(pr.ready_at)) AS month,
    APPROX_QUANTILES(COALESCE(CAST(s.checkpoint_pass_rate AS FLOAT64), 0), 100)[OFFSET(50)] AS run_median
  FROM per_run pr,
  UNNEST(pr.slots) AS s
  GROUP BY 1, 2, 3
),
monthly_medians AS (
  SELECT
    oc_name,
    month,
    APPROX_QUANTILES(run_median, 100)[OFFSET(50)] AS med
  FROM run_medians
  GROUP BY 1, 2
),
oc_scores AS (
  SELECT
    oc_name,
    AVG(med) AS oc_checkpoint_rate_score
  FROM monthly_medians
  GROUP BY 1
),
slot_level AS (
  SELECT
    pr.oc_name,
    s.position_id,
    s.position,
    pr.id AS crime_id,
    pr.ready_at,
    COALESCE(CAST(s.checkpoint_pass_rate AS FLOAT64), 0) AS rate,
    s.user.outcome AS outcome
  FROM per_run pr,
  UNNEST(pr.slots) AS s
),
pos_monthly AS (
  SELECT
    oc_name,
    position_id,
    ANY_VALUE(position) AS position,
    FORMAT_TIMESTAMP('%Y-%m', TIMESTAMP_SECONDS(ready_at)) AS month,
    APPROX_QUANTILES(rate, 100)[OFFSET(50)] AS med
  FROM slot_level
  GROUP BY 1, 2, 4
),
pos_scores AS (
  SELECT
    oc_name,
    position_id,
    ANY_VALUE(position) AS position,
    AVG(med) AS position_checkpoint_rate_score
  FROM pos_monthly
  GROUP BY 1, 2
),
pos_ranks AS (
  SELECT
    oc_name,
    position_id,
    position,
    position_checkpoint_rate_score,
    ROW_NUMBER() OVER (PARTITION BY oc_name ORDER BY position_checkpoint_rate_score DESC) AS position_rank_within_oc
  FROM pos_scores
),
outcome_counts AS (
  SELECT
    oc_name,
    position_id,
    outcome,
    COUNT(*) AS cnt
  FROM slot_level
  WHERE outcome IS NOT NULL
  GROUP BY 1, 2, 3
),
attempt_counts AS (
  SELECT
    oc_name,
    position_id,
    COUNT(*) AS attempt_count
  FROM slot_level
  GROUP BY 1, 2
),
position_outcomes_agg AS (
  SELECT
    oc_name,
    position_id,
    ARRAY_AGG(STRUCT(outcome AS outcome, cnt AS `count`) ORDER BY outcome) AS outcomes
  FROM outcome_counts
  GROUP BY 1, 2
),
positions_per_oc AS (
  SELECT
    pr.oc_name,
    pr.position_id,
    pr.position,
    pr.position_checkpoint_rate_score,
    pr.position_rank_within_oc,
    COALESCE(ac.attempt_count, 0) AS attempt_count,
    COALESCE(po.outcomes, []) AS outcomes
  FROM pos_ranks pr
  LEFT JOIN attempt_counts ac ON pr.oc_name = ac.oc_name AND pr.position_id = ac.position_id
  LEFT JOIN position_outcomes_agg po ON pr.oc_name = po.oc_name AND pr.position_id = po.position_id
),
positions_agg AS (
  SELECT
    oc_name,
    ARRAY_AGG(STRUCT(position_id, position, position_checkpoint_rate_score, position_rank_within_oc, attempt_count, outcomes) ORDER BY position_rank_within_oc) AS positions
  FROM positions_per_oc
  GROUP BY oc_name
),
oc_aggs AS (
  SELECT
    oc_name,
    MIN(difficulty) AS difficulty,
    MIN(members_needed) AS members_needed_min,
    APPROX_QUANTILES(members_needed, 100)[OFFSET(50)] AS members_needed_median,
    MAX(members_needed) AS members_needed_max,
    MIN(planning_to_ready_days) AS planning_to_ready_days_min,
    APPROX_QUANTILES(planning_to_ready_days, 100)[OFFSET(50)] AS planning_to_ready_days_median,
    MAX(planning_to_ready_days) AS planning_to_ready_days_max,
    MIN(respect_total) AS respect_total_min,
    APPROX_QUANTILES(respect_total, 100)[OFFSET(50)] AS respect_total_median,
    MAX(respect_total) AS respect_total_max,
    MIN(money_total) AS money_total_min,
    APPROX_QUANTILES(money_total, 100)[OFFSET(50)] AS money_total_median,
    MAX(money_total) AS money_total_max,
    MIN(respect_per_member) AS respect_per_member_min,
    APPROX_QUANTILES(respect_per_member, 100)[OFFSET(50)] AS respect_per_member_median,
    MAX(respect_per_member) AS respect_per_member_max,
    MIN(money_per_member) AS money_per_member_min,
    APPROX_QUANTILES(money_per_member, 100)[OFFSET(50)] AS money_per_member_median,
    MAX(money_per_member) AS money_per_member_max,
    MIN(respect_per_member_per_day) AS respect_per_member_per_day_min,
    APPROX_QUANTILES(respect_per_member_per_day, 100)[OFFSET(50)] AS respect_per_member_per_day_median,
    MAX(respect_per_member_per_day) AS respect_per_member_per_day_max,
    MIN(money_per_member_per_day) AS money_per_member_per_day_min,
    APPROX_QUANTILES(money_per_member_per_day, 100)[OFFSET(50)] AS money_per_member_per_day_median,
    MAX(money_per_member_per_day) AS money_per_member_per_day_max
  FROM per_run
  GROUP BY oc_name
),
oc_ranks AS (
  SELECT
    oa.oc_name,
    ROW_NUMBER() OVER (ORDER BY oa.difficulty ASC, os.oc_checkpoint_rate_score DESC) AS oc_rank
  FROM oc_aggs oa
  JOIN oc_scores os ON oa.oc_name = os.oc_name
),
source_max AS (
  SELECT
    CASE
      WHEN @window_days_back IS NULL THEN (
        SELECT MAX(TIMESTAMP_SECONDS(executed_at))
        FROM `torncity-402423.torn_data.v2_faction_40832_crimes-raw`
        WHERE executed_at IS NOT NULL
      )
      ELSE CAST(NULL AS TIMESTAMP)
    END AS source_max_executed_at
)

SELECT
  CURRENT_TIMESTAMP() AS computed_at,
  @window_days_back AS window_days,
  (SELECT source_max_executed_at FROM source_max) AS source_max_executed_at,
  oa.oc_name,
  oa.difficulty,
  oa.members_needed_min,
  oa.members_needed_median,
  oa.members_needed_max,
  oa.planning_to_ready_days_min,
  oa.planning_to_ready_days_median,
  oa.planning_to_ready_days_max,
  oa.respect_total_min,
  oa.respect_total_median,
  oa.respect_total_max,
  oa.money_total_min,
  oa.money_total_median,
  oa.money_total_max,
  oa.respect_per_member_min,
  oa.respect_per_member_median,
  oa.respect_per_member_max,
  oa.money_per_member_min,
  oa.money_per_member_median,
  oa.money_per_member_max,
  oa.respect_per_member_per_day_min,
  oa.respect_per_member_per_day_median,
  oa.respect_per_member_per_day_max,
  oa.money_per_member_per_day_min,
  oa.money_per_member_per_day_median,
  oa.money_per_member_per_day_max,
  os.oc_checkpoint_rate_score,
  r.oc_rank,
  COALESCE(pa.positions, []) AS positions
FROM oc_aggs oa
LEFT JOIN oc_scores os ON oa.oc_name = os.oc_name
LEFT JOIN oc_ranks r ON oa.oc_name = r.oc_name
LEFT JOIN positions_agg pa ON pa.oc_name = oa.oc_name
ORDER BY r.oc_rank, oa.oc_name;
