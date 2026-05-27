"""
src/clustering.py — Vendor feature preparation and K-means supplier segmentation.

Takes the vendor-level summary table (built from SQL queries in sql/) and:
1. Prepares a clean feature matrix (cost percentile, lead time, award frequency, etc.)
2. Finds optimal k with the elbow method + silhouette scoring
3. Fits K-means and hierarchical clustering
4. Assigns named business segments (not "Cluster 0")
5. Estimates dollar impact per cluster

Named segments (expected, validate against actual data):
    Premium Reliable  — low cost, fast delivery, consistent
    Cost-Efficient    — low cost, acceptable delivery
    Underperforming   — high cost or slow delivery; replacement candidates
    Risky / Volatile  — inconsistent; needs monitoring

Usage:
    from src.clustering import VendorSegmenter
    seg = VendorSegmenter()
    seg.fit(vendor_df)
    vendor_df_with_segments = seg.assign_segments(vendor_df)
    seg.estimate_savings(vendor_df_with_segments)
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans, AgglomerativeClustering
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler


# ---------------------------------------------------------------------------
# Vendor feature engineering
# ---------------------------------------------------------------------------

def build_vendor_features(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """
    Build a vendor-level feature matrix for clustering.

    Input df must have these columns (produced by sql/01_vendor_performance.sql run
    and joined into Python via DuckDB):
        recipient_name, naics_code, avg_award, award_count, lifetime_spend,
        avg_lead_time_days, cost_rank (RANK per NAICS), speed_rank (RANK per NAICS),
        naics_total_spend

    Returns:
        (feature_df, feature_names)
    """
    vendor = df.copy()

    # Cost percentile within NAICS (0 = cheapest, 1 = most expensive)
    vendor["cost_pct"] = (
        vendor.groupby("naics_code")["avg_award"]
        .rank(pct=True)
    )

    # Lead time percentile (0 = fastest, 1 = slowest)
    vendor["lead_time_pct"] = (
        vendor.groupby("naics_code")["avg_lead_time_days"]
        .rank(pct=True)
    )

    # Award frequency (log-scaled — volume signal)
    vendor["log_award_count"] = np.log1p(vendor["award_count"])

    # Share of NAICS spend (vendor concentration within category)
    vendor["naics_spend_share"] = (
        vendor["lifetime_spend"] / (vendor["naics_total_spend"] + 1e-6)
    ).clip(0, 1)

    # Lead time variability — if available (requires std_lead_time_days from SQL)
    if "std_lead_time_days" in vendor.columns:
        vendor["lead_time_cv"] = (
            vendor["std_lead_time_days"] / (vendor["avg_lead_time_days"].abs() + 1e-6)
        ).clip(0, 5)
    else:
        vendor["lead_time_cv"] = 0.0

    feature_cols = [
        "cost_pct",
        "lead_time_pct",
        "log_award_count",
        "naics_spend_share",
        "lead_time_cv",
    ]
    available = [c for c in feature_cols if c in vendor.columns]
    return vendor, available


# ---------------------------------------------------------------------------
# VendorSegmenter
# ---------------------------------------------------------------------------

class VendorSegmenter:
    """
    K-means vendor segmentation with business-friendly named clusters.

    Args:
        max_k: maximum k to test in elbow analysis (default 10)
        random_state: reproducibility seed
    """

    # Cluster naming rules: (cost_pct_range, lead_time_pct_range) → name
    SEGMENT_RULES = [
        ("Premium Reliable",  (0.0, 0.4), (0.0, 0.4)),  # low cost + fast
        ("Cost-Efficient",    (0.0, 0.5), (0.4, 0.7)),  # low cost + moderate delivery
        ("Underperforming",   (0.5, 1.0), (0.5, 1.0)),  # high cost + slow
        ("Risky / Volatile",  None,       None),          # catch-all for inconsistent
    ]

    SEGMENT_RECOMMENDATIONS = {
        "Premium Reliable":  "Strategic partners — protect and deepen. Do not re-source on price alone.",
        "Cost-Efficient":    "Preferred for commodity and non-critical awards. Increase volume.",
        "Underperforming":   "Replacement candidates. Quantify savings below.",
        "Risky / Volatile":  "Flag for active monitoring. Set escalation triggers.",
    }

    def __init__(self, max_k: int = 10, random_state: int = 42):
        self.max_k = max_k
        self.random_state = random_state
        self.scaler = StandardScaler()
        self.kmeans: Optional[KMeans] = None
        self.feature_names: list[str] = []
        self.best_k: int = 4
        self.inertias: list[float] = []
        self.silhouettes: list[float] = []

    def fit(self, vendor_df: pd.DataFrame) -> "VendorSegmenter":
        """
        Prepare features, find optimal k, fit K-means.

        Args:
            vendor_df: vendor-level DataFrame with columns from build_vendor_features()
        """
        vendor_df, feat_cols = build_vendor_features(vendor_df)
        self.feature_names = feat_cols
        X = vendor_df[feat_cols].fillna(vendor_df[feat_cols].median())
        X_scaled = self.scaler.fit_transform(X)

        # Elbow + silhouette analysis
        k_range = range(2, min(self.max_k + 1, len(X) // 5 + 1))
        self.inertias, self.silhouettes = [], []
        for k in k_range:
            km = KMeans(n_clusters=k, random_state=self.random_state, n_init=10)
            labels = km.fit_predict(X_scaled)
            self.inertias.append(km.inertia_)
            self.silhouettes.append(silhouette_score(X_scaled, labels) if len(set(labels)) > 1 else 0)

        # Best k = highest silhouette score
        self.best_k = list(k_range)[int(np.argmax(self.silhouettes))]
        print(f"[clustering] Optimal k={self.best_k} (silhouette={max(self.silhouettes):.3f})")

        self.kmeans = KMeans(
            n_clusters=self.best_k,
            random_state=self.random_state,
            n_init=20,
        )
        self.kmeans.fit(X_scaled)
        return self

    def assign_segments(self, vendor_df: pd.DataFrame) -> pd.DataFrame:
        """
        Assign cluster labels and map to named business segments.

        Returns the vendor_df with additional columns:
            cluster_id, segment_name, recommendation
        """
        if self.kmeans is None:
            raise RuntimeError("Call fit() first.")

        vendor_df, _ = build_vendor_features(vendor_df)
        X = vendor_df[self.feature_names].fillna(vendor_df[self.feature_names].median())
        X_scaled = self.scaler.transform(X)

        vendor_df = vendor_df.copy()
        vendor_df["cluster_id"] = self.kmeans.predict(X_scaled)

        # Map cluster → business name using centroid position
        centroids = pd.DataFrame(
            self.scaler.inverse_transform(self.kmeans.cluster_centers_),
            columns=self.feature_names,
        )

        cluster_name_map = {}
        used_names = set()
        for cid, row in centroids.iterrows():
            name = self._name_cluster(row, used_names)
            cluster_name_map[cid] = name
            used_names.add(name)

        vendor_df["segment_name"] = vendor_df["cluster_id"].map(cluster_name_map)
        vendor_df["recommendation"] = vendor_df["segment_name"].map(self.SEGMENT_RECOMMENDATIONS)
        return vendor_df

    def _name_cluster(self, centroid_row: pd.Series, used_names: set) -> str:
        """Assign a name to a cluster based on its centroid's cost and lead-time position."""
        cost = centroid_row.get("cost_pct", 0.5)
        lead = centroid_row.get("lead_time_pct", 0.5)
        cv   = centroid_row.get("lead_time_cv", 0)

        if cv > 1.5 and "Risky / Volatile" not in used_names:
            return "Risky / Volatile"
        if cost < 0.4 and lead < 0.4 and "Premium Reliable" not in used_names:
            return "Premium Reliable"
        if cost < 0.5 and "Cost-Efficient" not in used_names:
            return "Cost-Efficient"
        if cost >= 0.5 and "Underperforming" not in used_names:
            return "Underperforming"
        # Fallback
        options = ["Premium Reliable", "Cost-Efficient", "Underperforming", "Risky / Volatile"]
        for opt in options:
            if opt not in used_names:
                return opt
        return f"Cluster {len(used_names)}"

    def estimate_savings(self, vendor_df: pd.DataFrame) -> pd.DataFrame:
        """
        Estimate dollar savings if Underperforming vendors shifted to Cost-Efficient pricing.

        Returns a summary DataFrame with segment stats and estimated savings.
        The savings estimate is the "interview story number" — keep it conservative
        (use 50th percentile of Cost-Efficient avg_award as target, not minimum).
        """
        seg = vendor_df.groupby("segment_name").agg(
            vendor_count=("recipient_name", "nunique"),
            total_spend=("lifetime_spend", "sum"),
            avg_award=("avg_award", "mean"),
            avg_lead_time_days=("avg_lead_time_days", "mean"),
            award_count=("award_count", "sum"),
        ).reset_index()

        # Savings: if Underperforming moved to median Cost-Efficient price
        cost_eff = vendor_df[vendor_df["segment_name"] == "Cost-Efficient"]["avg_award"].median()
        underperf = vendor_df[vendor_df["segment_name"] == "Underperforming"].copy()
        if cost_eff and len(underperf) > 0:
            underperf_avg = underperf["avg_award"].mean()
            underperf_spend = underperf["lifetime_spend"].sum()
            price_reduction_pct = max(0, (underperf_avg - cost_eff) / underperf_avg)
            estimated_savings = underperf_spend * price_reduction_pct * 0.3  # 30% re-sourcing rate (conservative)
            print(f"\n[clustering] Cost reduction opportunity:")
            print(f"  Underperforming avg award:   ${underperf_avg:,.0f}")
            print(f"  Cost-Efficient median award: ${cost_eff:,.0f}")
            print(f"  Price reduction if re-sourced: {price_reduction_pct*100:.1f}%")
            print(f"  Estimated annual savings:    ${estimated_savings:,.0f} (at 30% re-source rate)")
        return seg
