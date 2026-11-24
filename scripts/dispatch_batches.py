#!/usr/bin/env python3
"""Start the portability workflow batch by batch with the GitHub CLI (`gh`).

Each batch is one workflow run of at most 256 jobs. Batches run one after the other:
the script starts a run, waits for it to finish, then starts the next. Free accounts
only get 20 concurrent jobs (5 on macOS), so more parallelism would only queue.

    gh auth login                      # once, in a clone of YOUR (public) study repository
    python3 scripts/dispatch_batches.py --min-stars 50 --dry-run
    python3 scripts/dispatch_batches.py --min-stars 50 --max-batches 2      # pilot
    python3 scripts/dispatch_batches.py --min-stars 50 --first-batch 2      # continue

The filter flags must stay the same between invocations, otherwise batches overlap or
skip repositories.
"""
import argparse
import csv
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_matrix  # noqa: E402


def gh(*a, check=True):
    return subprocess.run(["gh", *a], capture_output=True, text=True, check=check).stdout.strip()


def latest_run_id(workflow):
    return gh("run", "list", "--workflow", workflow, "--limit", "1", "--json", "databaseId", "-q", ".[0].databaseId")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", default="repos_verified.csv")
    ap.add_argument("--workflow", default="portability.yml")
    ap.add_argument("--classes", default="application,package")
    ap.add_argument("--min-stars", type=int, default=0)
    ap.add_argument("--oses", default="ubuntu-24.04,windows-2025,macos-15")
    ap.add_argument("--batch-size", type=int, default=80)
    ap.add_argument("--first-batch", type=int, default=0)
    ap.add_argument("--max-batches", type=int, default=0, help="0 = all")
    ap.add_argument("--frontend", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with open(args.csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    n = len(make_matrix.select_rows(rows, set(args.classes.split(",")), args.min_stars, True))
    total = -(-n // args.batch_size)
    last = total if not args.max_batches else min(total, args.first_batch + args.max_batches)
    print(f"{n} repos -> {total} batches of {args.batch_size}; running batches {args.first_batch}..{last - 1}")

    for b in range(args.first_batch, last):
        start = b * args.batch_size
        cmd = ["workflow", "run", args.workflow,
               "-f", f"start={start}", "-f", f"count={args.batch_size}", "-f", f"classes={args.classes}",
               "-f", f"min_stars={args.min_stars}", "-f", f"oses={args.oses}",
               "-f", f"frontend={'true' if args.frontend else 'false'}"]
        print(f"[batch {b}] gh {' '.join(cmd)}")
        if args.dry_run:
            continue
        before = latest_run_id(args.workflow)
        gh(*cmd)
        run_id = before
        for _ in range(30):  # wait until the new run shows up
            time.sleep(5)
            run_id = latest_run_id(args.workflow)
            if run_id != before:
                break
        print(f"[batch {b}] run {run_id} started, waiting for it to finish ...")
        gh("run", "watch", run_id, "--interval", "60", check=False)  # job failures are recorded in the results
        print(f"[batch {b}] run {run_id} finished")


if __name__ == "__main__":
    main()
