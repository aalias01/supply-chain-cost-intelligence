-- sql/01_vendor_performance.sql
-- Vendor performance ranking: cost efficiency × lead time × volume
--
-- Demonstrates: CTEs, window functions (RANK, SUM OVER, AVG OVER),
-- multi-level aggregation, and procurement-domain KPI construction.
--
-- Same SQL patterns used in retail_returns_intelligence for RFM and customer ranking —
-- deliberate cross-project SQL skill reinforcement.
--
-- Run against: data/processed/supply_chain.duckdb (via DuckDB CLI or Python)
-- Output: vendor_performance.parquet (used by notebooks/02_clustering.ipynb)

-- ── Step 1: Vendor-level KPIs ───────────────────────────────────────────────
WITH vendor_metrics AS (
    SELECT
        recipient_name,
        naics_code,
        naics_description,

        -- Volume signals
        COUNT(*)                        AS award_count,
        SUM(action_amount)              AS lifetime_spend,
        AVG(action_amount)              AS avg_award,
        MEDIAN(action_amount)           AS median_award,
        STDDEV(action_amount)           AS std_award,

        -- Lead time signals (performance_end - award date)
        AVG(lead_time_days)             AS avg_lead_time_days,
        STDDEV(lead_time_days)          AS std_lead_time_days,
        MEDIAN(lead_time_days)          AS median_lead_time_days,

        -- Recency (most recent award)
        MAX(action_date)                AS last_award_date,

        -- Geographic concentration
        COUNT(DISTINCT state_code)      AS states_served

    FROM federal_awards
    WHERE
        action_amount > 0               -- exclude modifications that reduce value
        AND lead_time_days IS NOT NULL
        AND lead_time_days > 0
    GROUP BY recipient_name, naics_code, naics_description
),

-- ── Step 2: NAICS-level benchmarks (the "market" each vendor is judged against) ──
naics_benchmarks AS (
    SELECT
        naics_code,
        COUNT(DISTINCT recipient_name)  AS naics_vendor_count,
        SUM(lifetime_spend)             AS naics_total_spend,
        AVG(avg_award)                  AS naics_avg_award,
        MEDIAN(avg_lead_time_days)      AS naics_median_lead_time,
        PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY avg_award)
                                        AS naics_p25_award,
        PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY avg_award)
                                        AS naics_p75_award
    FROM vendor_metrics
    GROUP BY naics_code
),

-- ── Step 3: Rankings and relative positioning ─────────────────────────────
ranked_vendors AS (
    SELECT
        v.*,
        b.naics_vendor_count,
        b.naics_total_spend,
        b.naics_avg_award,
        b.naics_median_lead_time,
        b.naics_p25_award,
        b.naics_p75_award,

        -- Spend concentration: this vendor's share of NAICS spend
        ROUND(v.lifetime_spend / (b.naics_total_spend + 0.01), 4)
                                        AS naics_spend_share,

        -- Cost rank within NAICS (1 = cheapest)
        RANK() OVER (
            PARTITION BY v.naics_code
            ORDER BY v.avg_award ASC
        )                               AS cost_rank_asc,

        -- Speed rank within NAICS (1 = fastest)
        RANK() OVER (
            PARTITION BY v.naics_code
            ORDER BY v.avg_lead_time_days ASC
        )                               AS speed_rank_asc,

        -- Volume rank within NAICS (1 = most awards)
        RANK() OVER (
            PARTITION BY v.naics_code
            ORDER BY v.award_count DESC
        )                               AS volume_rank,

        -- Rolling cumulative spend per NAICS (Pareto)
        SUM(v.lifetime_spend) OVER (
            PARTITION BY v.naics_code
            ORDER BY v.lifetime_spend DESC
            ROWS UNBOUNDED PRECEDING
        )                               AS cumulative_naics_spend,

        -- Composite efficiency score: cost × lead time (lower = better)
        -- Normalized to [0,1] within NAICS
        (
            RANK() OVER (PARTITION BY v.naics_code ORDER BY v.avg_award ASC) +
            RANK() OVER (PARTITION BY v.naics_code ORDER BY v.avg_lead_time_days ASC)
        ) / (2.0 * b.naics_vendor_count)
                                        AS composite_efficiency_score
    FROM vendor_metrics v
    JOIN naics_benchmarks b USING (naics_code)
    WHERE v.award_count >= 3            -- minimum 3 awards for stable estimates
)

-- ── Final output ──────────────────────────────────────────────────────────
SELECT
    recipient_name,
    naics_code,
    naics_description,
    award_count,
    ROUND(lifetime_spend, 2)            AS lifetime_spend,
    ROUND(avg_award, 2)                 AS avg_award,
    ROUND(median_award, 2)              AS median_award,
    ROUND(avg_lead_time_days, 1)        AS avg_lead_time_days,
    ROUND(std_lead_time_days, 1)        AS std_lead_time_days,
    last_award_date,
    states_served,
    ROUND(naics_spend_share, 4)         AS naics_spend_share,
    ROUND(naics_total_spend, 2)         AS naics_total_spend,
    cost_rank_asc,
    speed_rank_asc,
    volume_rank,
    naics_vendor_count,
    ROUND(composite_efficiency_score, 4) AS composite_efficiency_score,

    -- Percentile flags (useful for quick filtering in notebooks)
    CASE WHEN avg_award <= naics_p25_award THEN 1 ELSE 0 END
                                        AS is_low_cost,   -- bottom 25% cost
    CASE WHEN avg_award >= naics_p75_award THEN 1 ELSE 0 END
                                        AS is_high_cost,  -- top 25% cost
    CASE WHEN avg_lead_time_days <= naics_median_lead_time THEN 1 ELSE 0 END
                                        AS is_fast_delivery

FROM ranked_vendors
ORDER BY naics_code, cost_rank_asc;

-- ── Save to parquet for Python ─────────────────────────────────────────────
-- COPY (the above query) TO 'data/processed/vendor_performance.parquet' (FORMAT PARQUET);
