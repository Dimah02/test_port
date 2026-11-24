#!/usr/bin/env python3
"""Estimate how long the full run will take, from the job timings of finished workflow runs.

    gh run view <RUN_ID> --json jobs > jobs_pilot.json
    python3 scripts/job_times.py jobs_pilot.json --repos 1169

Pass several JSON files to combine runs. The estimate is only as good as the sample: the
3-repo smoke test is a rough guide, a pilot batch (80 repos = 80 jobs per OS) is a good one.

Model: every repo needs one job per OS. A free GitHub plan runs 20 jobs at once, at most
5 of them on macOS. Wall-clock time is the larger of
    - the macOS bound:   repos * mean_macos_minutes / macos_concurrency
    - the overall bound: repos * (sum of the mean minutes of all OSes) / total_concurrency
Batches run one after another, so each batch also has an idle tail; add roughly 10-20 %.
Queue time is not included in job durations.
"""
import argparse
import json
import re
import statistics
from datetime import datetime

OS_RE = re.compile(r"\((ubuntu|windows|macos)\)\s*$")


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_durations(paths):
    out = {"ubuntu": [], "windows": [], "macos": []}
    skipped = 0
    for p in paths:
        with open(p, encoding="utf-8") as f:
            jobs = json.load(f).get("jobs", [])
        for j in jobs:
            m = OS_RE.search(j.get("name", ""))
            started, completed = j.get("startedAt") or "", j.get("completedAt") or ""
            if not m or started.startswith("0001") or completed.startswith("0001") or not started or not completed:
                skipped += 1
                continue
            minutes = (parse_ts(completed) - parse_ts(started)).total_seconds() / 60
            out[m.group(1)].append(minutes)
    return out, skipped


def fmt_h(minutes):
    return f"{minutes / 60:.1f} h"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("jobs_json", nargs="+")
    ap.add_argument("--repos", type=int, required=True, help="repos in the full run (see make_matrix.py --stats)")
    ap.add_argument("--total-concurrency", type=int, default=20)
    ap.add_argument("--macos-concurrency", type=int, default=5)
    args = ap.parse_args()

    d, skipped = load_durations(args.jobs_json)
    print(f"{sum(len(v) for v in d.values())} jobs read ({skipped} entries skipped: prepare job / not finished)\n")
    means = {}
    print(f"{'OS':8} {'jobs':>5} {'mean':>7} {'median':>7} {'p90':>7} {'max':>7}   (minutes)")
    for os_name, vals in d.items():
        if not vals:
            print(f"{os_name:8} {0:>5}   no finished jobs")
            continue
        vals_sorted = sorted(vals)
        p90 = vals_sorted[min(len(vals_sorted) - 1, int(0.9 * len(vals_sorted)))]
        means[os_name] = statistics.mean(vals)
        print(f"{os_name:8} {len(vals):>5} {means[os_name]:>7.1f} {statistics.median(vals):>7.1f} {p90:>7.1f} {max(vals):>7.1f}")
    if len(means) < 3:
        raise SystemExit("\nneed finished jobs for ubuntu, windows and macos to project the full run")
    if min(len(v) for v in d.values()) < 30:
        print("\nnote: fewer than 30 jobs per OS - treat this as a rough guide, rerun it on a pilot batch.")

    n = args.repos
    mac_bound = n * means["macos"] / args.macos_concurrency
    total_bound = n * sum(means.values()) / args.total_concurrency
    est = max(mac_bound, total_bound)
    limiting = "macOS concurrency" if mac_bound >= total_bound else "total concurrency"
    print(f"\nfull run, {n} repos ({3 * n} jobs):")
    print(f"  macOS bound  : {fmt_h(mac_bound)}")
    print(f"  overall bound: {fmt_h(total_bound)}")
    print(f"  estimate     : {fmt_h(est)} (limited by {limiting}); with batch tails about {fmt_h(est * 1.1)} - {fmt_h(est * 1.2)}")
    ubuntu_only = n * means["ubuntu"] / args.total_concurrency
    print(f"\ntwo-stage option, stage 1 (Ubuntu only): {fmt_h(ubuntu_only)}; stage 2 then runs Windows+macOS only for the repos that")
    print("executed tests on Ubuntu, so its macOS time scales with that share.")


if __name__ == "__main__":
    main()
