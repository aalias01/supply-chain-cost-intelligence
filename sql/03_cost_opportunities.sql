-- sql/03_cost_opportunities.sql
-- Cost reduction opportunity quantification: "if-we-switched" analysis
--
-- Estimates savings if high-cost (Underperforming) vendors shifted volume to
-- Cost-Efficient vendors within the same NAICS category.
--
-- This is the "dollar story" — the output that makes an analyst say "show me more."
-- Interview story: "This is structurally identical to the Daikin BOM re-sourcing
-- analysis that found $5M in savings — except it runs in seconds instead of weeks."
--
-- Three opportunity types:
--   1. Vendor substitution: replace high-cost vendor with cost-efficient equivalent
--   2. Volume consolidation: consolidate spend on best-performing vendor per category
--   3. Lead time reduction: quantify inventory-holding cost savings from faster vendors

-- ── Vendor performance summary for opportunity analysis ─────────────────────
WITH vendor_perf AS (
    SELECT
        recipient_name,
        naics_code,
        naics_description,
        COUNT(*)                        AS award_count,
        SUM(action_amount)              AS total_spend,
        AVG(action_amount)              AS avg_award,
        AVG(lead_time_days)             AS avg_lead_time_days
    FROM federal_awards
    WHERE action_amount > 0
      AND lead_time_days > 0
    GROUP BY recipient_name, naics_code, naics_description
    HAVING COUNT(*) >= 3
),

-- ── NAICS-level benchmarks ──────────────────────────────────────────────────
naics_bench AS (
    SELECT
        naics_code,
        COUNT(DISTINCT recipient_name)  AS vendor_count,
        SUM(total_spend)                AS naics_total_spend,
        AVG(avg_award)                  AS naics_avg_award,
        PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY avg_award)
                                        AS p25_avg_award,   -- "good" cost benchmark
        PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY avg_award)
                                        AS p50_avg_award,
        PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY avg_award)
                                        AS p75_avg_award,   -- "expensive" threshold
        AVG(avg_lead_time_days)         AS naics_avg_lead_time,
        PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY avg_lead_time_days)
                                        AS p25_lead_time    -- "fast" benchmark
    FROM vendor_perf
    GROUP BY naics_code
    HAVING COUNT(DISTINCT recipient_name) >= 3
),

-- ── Flag vendors as high/low cost relative to NAICS benchmark ─────────────
vendor_flagged AS (
    SELECT
        v.*,
        b.naics_total_spend,
        b.naics_avg_award,
        b.p25_avg_award,
        b.p50_avg_award,
        b.p75_avg_award,
        b.naics_avg_lead_time,
        b.p25_lead_time,
        b.vendor_count,

        -- Tier assignment
        CASE
            WHEN v.avg_award <= b.p25_avg_award
                 AND v.avg_lead_time_days <= b.p25_lead_time
            THEN 'Cost-Efficient'
            WHEN v.avg_award <= b.p50_avg_award
            THEN 'Acceptable'
            WHEN v.avg_award >= b.p75_avg_award
            THEN 'High-Cost'
            ELSE 'Average'
        END                             AS cost_tier,

        -- Dollar gap: how much MORE this vendor charges vs. p25 benchmark
        GREATEST(0, v.avg_award - b.p25_avg_award)
                                        AS price_premium_vs_p25

    FROM vendor_perf v
    JOIN naics_bench b USING (naics_code)
)

-- ── Opportunity 1: Vendor substitution savings ────────────────────────────
-- For each High-Cost vendor: what are the savings if we switched to Cost-Efficient pricing?
-- Conservative assumption: only 30% of volume re-sourceable (contracts, sole-source constraints)
SELECT
    naics_code,
    naics_description,
    recipient_name                      AS high_cost_vendor,
    ROUND(total_spend, 2)               AS current_spend,
    ROUND(avg_award, 2)                 AS avg_award_current,
    ROUND(p25_avg_award, 2)             AS target_avg_award,   -- Cost-Efficient benchmark
    ROUND(price_premium_vs_p25, 2)      AS price_premium_per_award,
    award_count                         AS awards_to_re_source,
    ROUND(price_premium_vs_p25 * award_count, 2)
                                        AS gross_savings_full_switch,
    ROUND(price_premium_vs_p25 * award_count * 0.30, 2)
                                        AS conservative_savings_30pct,  -- ← headline number
    cost_tier,
    vendor_count                        AS naics_alternatives_available

FROM vendor_flagged
WHERE cost_tier = 'High-Cost'
  AND vendor_count >= 3               -- at least 2 alternatives exist in NAICS
ORDER BY conservative_savings_30pct DESC;

-- ── Opportunity 2: Volume consolidation ──────────────────────────────────
-- Uncomment to run separately:
-- SELECT
--     naics_code,
--     naics_description,
--     COUNT(DISTINCT recipient_name) AS vendors_currently_used,
--     SUM(total_spend) AS total_category_spend,
--     MIN(avg_award) AS best_available_price,
--     AVG(avg_award) AS current_avg_price,
--     ROUND((AVG(avg_award) - MIN(avg_award)) / AVG(avg_award) * SUM(total_spend), 2)
--         AS max_consolidation_savings
-- FROM vendor_flagged
-- GROUP BY naics_code, naics_description
-- HAVING COUNT(DISTINCT recipient_name) > 5
-- ORDER BY max_consolidation_savings DESC;

-- ── Opportunity 3: Lead time reduction value (inventory carrying cost) ────
-- Uncomment to run separately:
-- Assumes: 20% annual carrying cost of inventory, 30-day safety stock at avg award value
-- SELECT
--     naics_code,
--     naics_description,
--     recipient_name,
--     ROUND(avg_lead_time_days, 1) AS current_lead_time,
--     ROUND(p25_lead_time, 1) AS target_lead_time,
--     ROUND(avg_lead_time_days - p25_lead_time, 1) AS lead_time_reduction_days,
--     ROUND(
--         (avg_lead_time_days - p25_lead_time) / 365.0  -- fraction of year
--         * total_spend                                  -- annual inventory value proxy
--         * 0.20,                                        -- 20% carrying cost rate
--         2
--     ) AS est_carrying_cost_savings
-- FROM vendor_flagged
-- WHERE avg_lead_time_days > p25_lead_time * 1.5  -- 50% slower than fast benchmark
-- ORDER BY est_carrying_cost_savings DESC;
