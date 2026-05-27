-- sql/02_price_anomalies.sql
-- Price anomaly detection: z-score per NAICS category
--
-- Identifies awards priced abnormally high or low relative to their category's
-- distribution. A high z-score (|z| > 2) signals:
--   Positive (high price): potential overcharge, preferred vendor pricing, sole-source
--   Negative (low price): competitive award, substandard specification, or data error
--
-- This is the "what's hiding in the data" analysis — actionable for procurement audits.
-- Interview story: same z-score logic I'd use in Python, executed in SQL for speed and
-- portability to any BI tool.

-- ── Per-NAICS price distribution ────────────────────────────────────────────
WITH category_stats AS (
    SELECT
        naics_code,
        naics_description,
        COUNT(*)                            AS award_count,
        AVG(action_amount)                  AS mean_amount,
        STDDEV(action_amount)               AS std_amount,
        PERCENTILE_CONT(0.10) WITHIN GROUP (ORDER BY action_amount) AS p10_amount,
        PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY action_amount) AS p25_amount,
        PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY action_amount) AS p50_amount,
        PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY action_amount) AS p75_amount,
        PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY action_amount) AS p90_amount,
        PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY action_amount) AS p99_amount
    FROM federal_awards
    WHERE action_amount > 0
    GROUP BY naics_code, naics_description
    HAVING COUNT(*) >= 10               -- enough data for stable stats
),

-- ── Z-score per award ─────────────────────────────────────────────────────
z_scored AS (
    SELECT
        a.award_id_piid,
        a.recipient_name,
        a.naics_code,
        a.naics_description,
        a.action_date,
        a.action_amount,
        a.avg_lead_time_days            AS lead_time_days,
        a.agency_name,

        cs.mean_amount,
        cs.std_amount,
        cs.p50_amount,

        -- Z-score: how many std deviations from category mean?
        CASE
            WHEN cs.std_amount > 0
            THEN ROUND(
                (a.action_amount - cs.mean_amount) / cs.std_amount,
                3
            )
            ELSE NULL
        END                             AS z_score,

        -- IQR ratio: how many IQRs above median? (robust to outliers)
        CASE
            WHEN (cs.p75_amount - cs.p25_amount) > 0
            THEN ROUND(
                (a.action_amount - cs.p50_amount) / (cs.p75_amount - cs.p25_amount),
                3
            )
            ELSE NULL
        END                             AS iqr_ratio,

        -- Categorize anomaly direction
        CASE
            WHEN (a.action_amount - cs.mean_amount) / NULLIF(cs.std_amount, 0) > 2
            THEN 'overpriced'
            WHEN (a.action_amount - cs.mean_amount) / NULLIF(cs.std_amount, 0) < -2
            THEN 'underpriced'
            ELSE 'normal'
        END                             AS anomaly_type

    FROM federal_awards a
    JOIN category_stats cs USING (naics_code)
    WHERE a.action_amount > 0
),

-- ── Vendor-level anomaly profile ──────────────────────────────────────────
vendor_anomaly_profile AS (
    SELECT
        recipient_name,
        naics_code,
        COUNT(*)                        AS total_awards,
        SUM(CASE WHEN anomaly_type = 'overpriced' THEN 1 ELSE 0 END)
                                        AS overpriced_count,
        SUM(CASE WHEN anomaly_type = 'underpriced' THEN 1 ELSE 0 END)
                                        AS underpriced_count,
        AVG(ABS(z_score))               AS mean_abs_z,
        MAX(z_score)                    AS max_z_score,
        SUM(CASE WHEN anomaly_type = 'overpriced'
            THEN action_amount - p50_amount ELSE 0 END)
                                        AS excess_spend_vs_median
    FROM z_scored
    GROUP BY recipient_name, naics_code
)

-- ── Output 1: Individual anomalous awards (|z| > 2) ──────────────────────
SELECT
    z.recipient_name,
    z.naics_code,
    z.naics_description,
    z.action_date,
    ROUND(z.action_amount, 2)           AS action_amount,
    ROUND(z.mean_amount, 2)             AS category_mean,
    ROUND(z.p50_amount, 2)              AS category_median,
    z.z_score,
    z.iqr_ratio,
    z.anomaly_type,
    z.agency_name
FROM z_scored z
WHERE ABS(z.z_score) > 2
ORDER BY z.z_score DESC;

-- ── Output 2: Vendors with systematic overpricing pattern ────────────────
-- Uncomment to run separately:
-- SELECT
--     vap.recipient_name,
--     vap.naics_code,
--     vap.total_awards,
--     vap.overpriced_count,
--     ROUND(vap.overpriced_count * 100.0 / vap.total_awards, 1) AS pct_overpriced,
--     ROUND(vap.mean_abs_z, 2)        AS mean_abs_z,
--     ROUND(vap.excess_spend_vs_median / 1e6, 3) AS excess_spend_millions
-- FROM vendor_anomaly_profile vap
-- WHERE vap.overpriced_count >= 2
--   AND vap.overpriced_count * 1.0 / vap.total_awards >= 0.3  -- 30%+ awards overpriced
-- ORDER BY vap.excess_spend_millions DESC;
