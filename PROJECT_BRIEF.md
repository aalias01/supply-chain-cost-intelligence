# Project Brief — Supply Chain Cost Intelligence

| Priority Score | Tier | Recommended Ship Slot | Effort |
|----------------|------|----------------------|--------|
| **4.30** | **P1** | **Order #5** *(after CMAPSS · Retail Returns · RAG · HVAC)* | 12–16 hrs across 3–4 sessions |

**Score breakdown** — ED 5 · DIFF 4 · SC 4 · DSS 5 · BV 4 · EE 3
**Lane:** A/B (Industrial ops — bridges to retail supply chain)
**Target companies:** **Costco supply chain**, Boeing Supply Chain, Amazon Robotics, Walmart, Daikin, Rheem, Siemens, Honeywell

**Conditions to re-rank:**
- If a Costco supply-chain or general supply-chain DS role surfaces: keep at P1, lead with this on resume
- If chosen real dataset proves untenable, fall back order: USAspending → Olist → Kaggle SCMS — only synthetic as last resort, clearly labeled
- If shipping ahead of Retail Returns (timing pressure): SQL practice here directly transfers to Retail Returns RFM features → small efficiency win

---

## Problem Statement

Manufacturing and retail companies lose millions annually to supplier inefficiencies — price anomalies, unreliable lead times, and suboptimal sourcing decisions hidden in procurement data. Given a real, public procurement dataset, can we segment suppliers by performance profile, surface cost reduction opportunities, and deliver actionable intelligence to procurement teams?

This mirrors the $5M+ cost optimization Alvin led at Daikin — now as a reproducible, data-driven system rather than manual analysis, and built on **real public data** instead of synthetic.

---

## Why This Project for Alvin

- **Direct domain match:** Alvin performed exactly this analysis at Daikin (BOM analysis, supplier optimization, $2M direct + $3M indirect savings). He knows what the output should look like and what questions procurement managers actually ask.
- **SQL depth showcase:** DATA 514 coursework applied to a real business problem — CTEs, window functions, aggregations, ranking. SQL competence transfers directly to the Retail Returns project (RFM, ranking, top-decile customers).
- **Different output format:** Quarto analytical report rather than a web app. Shows versatility in how DS results are communicated. Pairs well with a complementary Tableau or PowerBI dashboard.
- **Differentiator from tech candidates:** Pure tech candidates don't understand procurement data, BOM structure, or what makes a "good" supplier. Alvin does.
- **Real data, real story:** This was the original brief's weak link — synthetic data signals "candidate couldn't find a real dataset." This rewrite uses public real data so the project lands harder with hiring managers.

---

## Dataset — Real Public Data Only

The original brief defaulted to synthetic. This rewrite reverses that default — synthetic is now last-resort.

### Primary — USAspending.gov Federal Procurement
- **Source:** https://www.usaspending.gov/download_center/award_data_archive (Award data archive) or API
- **What's there:** Every US federal contract, grant, loan — vendor name, NAICS category, dollar amount, period of performance, recipient location, award type
- **Size:** Tens of millions of records; trivially filterable to a manageable scope (e.g. one agency × one year × one NAICS category gives 10K–100K records)
- **Why this dataset:** Real, free, very large, hand-cleanable. Government procurement is *literally the same problem class* as private-sector supplier intelligence — same cost / vendor / category structure.
- **Caveat:** No defect-rate or on-time-delivery columns directly — engineer those from period_of_performance vs. action_date deltas (lead time proxy) and from vendor-level price-variance signals.

### Alternative — Brazilian Olist eCommerce
- **Source:** Kaggle → "Brazilian E-Commerce Public Dataset by Olist"
- **What's there:** Sellers, products, orders, deliveries, reviews — joinable across 9 tables
- **Why use it:** Sellers function exactly like suppliers; on-time delivery is directly observable; review scores act as quality signal
- **Best fit for:** Retail-leaning narrative (lane B) — pairs with Retail Returns Intelligence dataset (already on Olist as supplement)

### Alternative — Kaggle SCMS Delivery History
- **Source:** Search Kaggle for "SCMS Delivery History" or "Supply Chain Shipment Pricing"
- **What's there:** USAID supply chain data — pharmaceutical shipments globally with actual delivery and cost data
- **Why use it:** Pharma supply-chain framing; international scope; clean and explicit lead-time / freight-cost columns

### Last-resort fallback — Synthetic
- Only if all three real options prove unworkable
- Label clearly in README; note real-data attempts and failure mode
- Use Daikin / Rheem domain knowledge to generate credible BOM + supplier data

---

## Tech Stack

| Layer | Tool | Justification |
|-------|------|---------------|
| Database | DuckDB (preferred) or SQLite | DuckDB handles the larger USAspending slice better |
| SQL analysis | CTEs, window functions, aggregation, ranking | DATA 514 applied |
| Python wrangling | Pandas (for what SQL can't do cleanly) | |
| Clustering | scikit-learn — K-means, hierarchical | Interpretable supplier segments |
| Visualization | Matplotlib, Seaborn, Plotly | Plotly for interactive segment scatterplots in the report |
| Optional dashboard | **Tableau Public** OR **PowerBI Desktop** | Different visualization stack from Retail Returns; demonstrates breadth |
| Report | **Quarto** — rendered HTML | Primary deliverable |
| Hosting | Vercel (static Quarto HTML) | |
| Environment | conda (`environment.yml`) + pip (`requirements.txt`) | |
| Version control | git + GitHub repo `aalias01/supply-chain-cost-intelligence` | |

---

## SQL Skills to Demonstrate

```sql
-- Vendor performance ranking with window functions
WITH vendor_metrics AS (
    SELECT
        recipient_name,
        naics_code,
        AVG(action_amount) AS avg_award,
        COUNT(*) AS award_count,
        SUM(action_amount) AS lifetime_spend,
        AVG(JULIANDAY(period_of_performance_end) - JULIANDAY(action_date)) AS avg_lead_time_days
    FROM federal_awards
    WHERE action_date >= DATE('2023-01-01')
    GROUP BY recipient_name, naics_code
)
SELECT
    recipient_name,
    naics_code,
    avg_award,
    avg_lead_time_days,
    RANK() OVER (PARTITION BY naics_code ORDER BY avg_award ASC) AS cost_rank,
    RANK() OVER (PARTITION BY naics_code ORDER BY avg_lead_time_days ASC) AS speed_rank,
    SUM(lifetime_spend) OVER (PARTITION BY naics_code) AS naics_total_spend
FROM vendor_metrics
WHERE award_count >= 5;

-- Price anomaly detection per NAICS category
WITH category_stats AS (
    SELECT
        naics_code,
        AVG(action_amount) AS mean_amount,
        STDDEV(action_amount) AS std_amount
    FROM federal_awards
    GROUP BY naics_code
)
SELECT
    f.recipient_name,
    f.naics_code,
    f.action_amount,
    (f.action_amount - cs.mean_amount) / cs.std_amount AS z_score
FROM federal_awards f
JOIN category_stats cs USING (naics_code)
WHERE ABS(z_score) > 2
ORDER BY z_score DESC;
```

The CTEs, window functions, and self-join patterns are deliberately the same patterns the Retail Returns project uses for RFM and customer ranking — code reuse and skill reinforcement across two projects.

---

## Clustering Approach

Cluster vendors / sellers / suppliers into segments by: cost percentile, lead time, on-time delivery rate (where directly observable), defect / cancellation rate, total spend.

Expected segments (each named with a business recommendation):
- **Premium reliable** — high cost, perfect delivery, zero defects → strategic partners; preserve relationship
- **Cost-efficient** — low cost, acceptable delivery → preferred for non-critical / commodity parts
- **Underperforming** — high cost OR poor delivery → candidates for replacement; quantified $ at stake
- **Risky / volatile** — inconsistent performance → needs monitoring; flag for procurement team

Output: cluster name + business recommendation + estimated $ impact, *not* "Cluster 0, Cluster 1." Hiring managers grade on whether the output is operationally useful, not whether the math is right.

---

## Deliverables

1. `data/load_usaspending.py` — script to download, filter, load the chosen real-data slice into DuckDB
2. `sql/01_vendor_performance.sql` — vendor ranking and KPI queries
3. `sql/02_price_anomalies.sql` — price anomaly detection (z-score per NAICS / category)
4. `sql/03_cost_opportunities.sql` — quantified cost reduction opportunities (if-we-moved-X analysis)
5. `notebooks/01_eda.ipynb` — exploratory analysis: spend distribution, vendor concentration (Pareto)
6. `notebooks/02_clustering.ipynb` — supplier segmentation with K-means + hierarchical comparison
7. `report/supply_chain_intelligence.qmd` — Quarto report (the primary deliverable)
8. `report/supply_chain_intelligence.html` — rendered output
9. *Optional:* `dashboards/supplier_intel.twbx` (Tableau) OR `.pbix` (PowerBI) — published / exported as PDF for README
10. `README.md` — recruiter-facing with link to rendered Quarto report and dashboard PDF

---

## Project Phases

### Phase 1 — Data Acquisition + EDA (3–4 hrs)
- [ ] Choose dataset: USAspending preferred; Olist if leaning retail; SCMS if leaning pharma/global
- [ ] Download and load into DuckDB; commit a small sample to repo, gitignore the full
- [ ] EDA: spend distribution, vendor concentration (Pareto — which 20% of vendors account for 80% of spend?)
- [ ] Identify key analytical entity (vendor / seller / supplier) and primary metrics
- [ ] Output: `notebooks/01_eda.ipynb` with documented findings

### Phase 2 — SQL Analysis (4–5 hrs)
- [ ] Vendor performance ranking with window functions (CTEs + RANK / DENSE_RANK)
- [ ] Price anomaly detection (z-score via SQL per category)
- [ ] Cost opportunity quantification: "if we moved X% of orders from high-cost to cost-efficient vendors..."
- [ ] Lead time analysis by category and vendor (where derivable)
- [ ] Output: `sql/*.sql` files + queries embedded in Quarto

### Phase 3 — Clustering (2–3 hrs)
- [ ] Feature scaling and preparation
- [ ] K-means: elbow method for k, silhouette score
- [ ] Hierarchical clustering as comparison
- [ ] Assign and name cluster segments
- [ ] Business recommendations per cluster (with $ estimates)
- [ ] Output: `notebooks/02_clustering.ipynb`

### Phase 4 — Quarto Report (2–3 hrs)
- [ ] Write narrative: problem → data → findings → recommendations → limitations
- [ ] Embed SQL results and cluster visualizations
- [ ] Render to HTML
- [ ] Deploy static HTML to Vercel
- [ ] Update README with report link

### Phase 5 — *Optional:* Tableau or PowerBI Dashboard (2–3 hrs)
*Only if PowerBI hasn't already been done on Retail Returns, OR if Tableau is the better resume signal for the target role. Skip if redundant.*

- [ ] Connect Tableau Public or PowerBI Desktop to the Gold-layer aggregated table
- [ ] Pages: (1) Spend Overview, (2) Vendor Performance Heatmap, (3) Cluster Segments, (4) Cost Opportunity Tracker
- [ ] Publish (Tableau Public) or export PDF (PowerBI)
- [ ] Embed PDF link / live URL in README

---

## Interview Talking Points

1. *"I deliberately chose USAspending.gov over a synthetic dataset — the data is messy, real, and at scale, which is what hiring managers actually want to see. Cleaning federal procurement data is itself a demonstration of competence."*
2. *"I led this type of analysis manually at Daikin and identified $5M+ in savings — $2M direct, $3M indirect. This project shows the same business problem solved as a reproducible data system, which is what scaling that work would actually look like."*
3. *"I chose SQL-first for the core analysis because that's how procurement and ops analysts actually work. CTEs and window functions handle the ranking and aggregation cleanly; Python clustering only sits on top of what SQL has already prepared."*
4. *"The clustering output is framed as named business segments with quantified $ recommendations, not 'Cluster 0, Cluster 1.' That's intentional — DS output that procurement managers can act on next Tuesday morning."*
5. *"The same SQL patterns — CTEs for customer-level aggregation, window functions for ranking — show up in my Retail Returns project for RFM and excessive-returner detection. Same SQL muscle, two domains."*

---

## Success Criteria

- [ ] GitHub repo public; Quarto report deployed and accessible via Vercel URL
- [ ] All SQL queries demonstrate CTEs, window functions, and aggregation
- [ ] Real public data used (USAspending / Olist / SCMS) — not synthetic
- [ ] Cluster analysis has named segments with quantified $ business recommendations
- [ ] Can explain every analytical decision in an interview
- [ ] Resume bullet: *"Built SQL-driven supply chain cost intelligence system on real federal procurement data; applied K-means clustering to segment vendors and surface $X in potential cost reduction — same DS muscle I used at Daikin to identify $5M+ in savings, now reproducible."*

---

*Brief created: April 2026 · Updated April 2026 (real-data switch) · May 2026 (ship slot corrected to #5 — RAG promoted to #3, HVAC to #4 in May 2026 activation pass) | Priority Score 4.30 · Tier P1 · Ship slot #5*
