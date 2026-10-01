"""Write the load-test results as Markdown, for the GitHub job summary."""
import csv
import os
import sys
from pathlib import Path


def main(out_dir):
    out = Path(out_dir)
    lines = ["## Load test", ""]

    stack = out / "stack.tsv"
    if stack.exists():
        rows = [line.split("\t") for line in stack.read_text().split("\n") if line.count("\t") == 2]
        lines += ["### Stack", "", "| | used | from |", "|---|---|---|"]
        lines += [f"| {name} | `{value}` | {source} |" for name, value, source in rows] + [""]

    profile = [
        ("dataset preset", os.getenv("PRESET")),
        ("users", os.getenv("USERS")),
        ("spawn rate, per s", os.getenv("SPAWN_RATE")),
        ("duration", os.getenv("RUN_TIME")),
        ("fail ratio limit", os.getenv("LOADTEST_FAIL_RATIO")),
        ("p95 ceiling, ms", os.getenv("LOADTEST_P95_MS") if os.getenv("LOADTEST_P95_MS") not in ("", "0", None)
         else "none"),
    ]
    lines += ["### Profile", "", "| | |", "|---|---|"] + [f"| {k} | `{v}` |" for k, v in profile] + [""]

    dataset = out / "dataset.tsv"
    if dataset.exists():
        rows = [line.split("\t") for line in dataset.read_text().split("\n") if "\t" in line]
        lines += ["### Dataset", "", "| table | rows |", "|---|---:|"]
        lines += [f"| {name} | {int(count):,} |" for name, count in rows] + [""]

    stats = out / "run_stats.csv"
    if not stats.exists():
        lines.append("No results: the load step did not produce `run_stats.csv`.")
    else:
        lines += [
            "### Requests", "",
            "| operation | requests | failed | fail % | p50 ms | p95 ms | p99 ms | req/s |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for row in csv.DictReader(stats.open()):
            requests, failures = int(row["Request Count"]), int(row["Failure Count"])
            share = failures / requests if requests else 0
            name = f"**{row['Name']}**" if row["Name"] == "Aggregated" else row["Name"]
            lines.append(
                f"| {name} | {requests:,} | {failures:,} | {share:.2%} | {row['50%']} | {row['95%']} "
                f"| {row['99%']} | {float(row['Requests/s']):.2f} |"
            )
    print("\n".join(lines))


if __name__ == "__main__":
    main(sys.argv[1])
