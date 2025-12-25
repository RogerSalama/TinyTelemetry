import csv
import matplotlib.pyplot as plt

METRICS_FILE = "logs/metrics.csv"

bytes_per_report = []
reporting_interval = []
duplicate_rate = []
loss_rate = []

# --- Read metrics.csv ---
with open(METRICS_FILE, "r") as f:
    reader = csv.DictReader(f)
    for row in reader:
        bytes_per_report.append(float(row["bytes_per_report"]))
        reporting_interval.append(float(row["reporting_interval_ms"]))
        duplicate_rate.append(float(row["duplicate_rate"]))
        loss_rate.append(float(row["sequence_gap_count"]))

# -------------------------------------------------
# Plot 1: Bytes per report vs Reporting Interval
# -------------------------------------------------
plt.figure()
plt.plot(reporting_interval, bytes_per_report, marker='o')
plt.xlabel("Reporting Interval (ms)")
plt.ylabel("Bytes per Report")
plt.title("Bytes per Report vs Reporting Interval")
plt.grid(True)
plt.tight_layout()
plt.savefig("bytes_vs_reporting_interval.png")
plt.show()

# -------------------------------------------------
# Plot 2: Duplicate Rate vs Loss
# -------------------------------------------------
plt.figure()
plt.plot(loss_rate, duplicate_rate, marker='s')
plt.xlabel("Packet Loss (Sequence Gaps)")
plt.ylabel("Duplicate Rate")
plt.title("Duplicate Rate vs Packet Loss")
plt.grid(True)
plt.tight_layout()
plt.savefig("duplicate_rate_vs_loss.png")
plt.show()
