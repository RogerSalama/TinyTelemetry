#!/usr/bin/env python3
import csv
import os
import statistics
import matplotlib.pyplot as plt

MET_CSV = os.path.join("logs", "metrics.csv")
OUT_PNG = os.path.join("logs", "bytes_vs_interval.png")

bytes_per_report = []
reporting_interval_ms = []

if not os.path.exists(MET_CSV):
    raise FileNotFoundError(f"{MET_CSV} not found. Run your server and generate metrics first.")

with open(MET_CSV, "r", newline="") as f:
    rdr = csv.DictReader(f)
    cols = rdr.fieldnames
    if "bytes_per_report" not in cols or "reporting_interval_ms" not in cols:
        raise RuntimeError("metrics.csv must have 'bytes_per_report' and 'reporting_interval_ms' columns.")
    for row in rdr:
        try:
            bpr = float(row["bytes_per_report"])
            ri  = float(row["reporting_interval_ms"])
        except ValueError:
            continue
        # Skip runs with zero metrics (e.g., before patches)
        if bpr > 0.0 and ri > 0.0:
            bytes_per_report.append(bpr)
            reporting_interval_ms.append(ri)

if not bytes_per_report:
    raise RuntimeError("No valid metrics to plot. Ensure your runs produced non-zero values.")

plt.figure(figsize=(8, 5))
plt.scatter(reporting_interval_ms, bytes_per_report, color="#1f77b4", alpha=0.8, edgecolor="k")
plt.title("bytes_per_report vs reporting_interval", fontsize=14)
plt.xlabel("Reporting Interval (ms)", fontsize=12)
plt.ylabel("Bytes per Report", fontsize=12)
plt.grid(True, linestyle="--", alpha=0.4)
plt.tight_layout()
plt.savefig(OUT_PNG, dpi=150)

print(f"Plot saved to: {OUT_PNG}")