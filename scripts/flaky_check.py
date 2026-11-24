#!/usr/bin/env python3
"""Separate flaky tests from real OS differences, using repeated runs of the same repos.

collect_results.py keeps only the NEWEST result per (repo, OS). This script keeps EVERY run,
so it needs the original run plus the reruns in the same results folder
(results/<run_id>/result-<os>-<owner>__<repo>/result.json, as produced by `gh run download`).

    python3 scripts/flaky_check.py results/ --repos-file lists/differs.txt --out-prefix analysis/flaky

Per OS a repo is STABLE if all its runs have the same outcome and the same set of failing tests.
Verdict per repo:
    OS-SPECIFIC (stable)     every OS is stable and the OSes differ  -> a real portability finding
    FLAKY on <os,...>        at least one OS gave different results across its own runs
    no difference            stable and identical on all OSes (the first-run difference was flakiness)
    needs more runs          some OS has fewer than --min-runs runs
"""
import argparse
import csv
import glob
import json
import os
from collections import defaultdict

OS_ORDER = ["ubuntu", "windows", "macos"]


def load_all(results_dir):
    runs = defaultdict(list)  # (repo, os) -> [result, ...]
    for path in glob.glob(os.path.join(results_dir, "**", "result.json"), recursive=True):
        try:
            with open(path, encoding="utf-8") as f:
                r = json.load(f)
        except (OSError, ValueError):
            continue
        r["_run"] = os.path.relpath(path, results_dir).split(os.sep)[0]
        runs[(r.get("repo"), r.get("os_name"))].append(r)
    for rs in runs.values():
        rs.sort(key=lambda r: r.get("started_utc", ""))
    return runs


def summarize_os(rs):
    outcomes = [r["outcome"] for r in rs]
    sets = [frozenset(r.get("failed_tests", [])) for r in rs]
    stable = len(set(outcomes)) == 1 and len(set(sets)) == 1
    always = frozenset.intersection(*sets) if sets else frozenset()
    sometimes = (frozenset.union(*sets) - always) if sets else frozenset()
    return {"n": len(rs), "outcomes": outcomes, "stable": stable, "always_failing": always, "flaky_tests": sometimes}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results_dir")
    ap.add_argument("--repos-file", default="", help="only look at these repos (one owner/repo per line)")
    ap.add_argument("--out-prefix", default="analysis/flaky")
    ap.add_argument("--min-runs", type=int, default=3, help="runs per (repo, OS) needed for a verdict (original + reruns)")
    args = ap.parse_args()

    runs = load_all(args.results_dir)
    repos = sorted({repo for repo, _ in runs})
    if args.repos_file:
        with open(args.repos_file, encoding="utf-8") as f:
            wanted = {ln.strip().lower() for ln in f if ln.strip() and not ln.startswith("#")}
        repos = [r for r in repos if r.lower() in wanted]

    os.makedirs(os.path.dirname(os.path.abspath(args.out_prefix)), exist_ok=True)
    fields = ["repo", "verdict"]
    for o in OS_ORDER:
        fields += [f"{o}_runs", f"{o}_outcomes", f"{o}_flaky_tests"]
    fields += ["flaky_test_examples", "always_failing_only_on"]
    counts = defaultdict(int)
    rows = []
    for repo in repos:
        per = {o: summarize_os(runs[(repo, o)]) for o in OS_ORDER if (repo, o) in runs}
        row = {"repo": repo}
        examples = []
        for o in OS_ORDER:
            s = per.get(o)
            row[f"{o}_runs"] = s["n"] if s else 0
            row[f"{o}_outcomes"] = ",".join(s["outcomes"]) if s else ""
            row[f"{o}_flaky_tests"] = len(s["flaky_tests"]) if s else ""
            if s:
                examples += [f"{o}:{t}" for t in sorted(s["flaky_tests"])[:3]]
        row["flaky_test_examples"] = " | ".join(examples[:6])

        missing = [o for o in OS_ORDER if o not in per or per[o]["n"] < args.min_runs]
        unstable = [o for o in OS_ORDER if o in per and not per[o]["stable"]]
        if missing:
            verdict = "needs more runs (" + ",".join(missing) + ")"
        elif unstable:
            verdict = "FLAKY on " + ",".join(unstable)
        else:
            stable_outcomes = {o: per[o]["outcomes"][0] for o in OS_ORDER}
            always = {o: per[o]["always_failing"] for o in OS_ORDER}
            if len(set(stable_outcomes.values())) > 1:
                verdict = "OS-SPECIFIC (stable): " + ",".join(f"{o}={v}" for o, v in stable_outcomes.items())
            elif len(set(always.values())) > 1:
                verdict = "OS-SPECIFIC (stable): different tests fail per OS"
            else:
                verdict = "no difference"
        row["verdict"] = verdict
        if all(o in per for o in OS_ORDER) and not unstable:
            common = frozenset.intersection(*(per[o]["always_failing"] for o in OS_ORDER))
            only = {o: len(per[o]["always_failing"] - common) for o in OS_ORDER}
            row["always_failing_only_on"] = "; ".join(f"{o}:{n}" for o, n in only.items() if n)
        counts[verdict.split(" (")[0].split(" on ")[0].split(":")[0]] += 1
        rows.append(row)

    with open(args.out_prefix + "_report.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    print(f"{len(rows)} repos analysed -> {args.out_prefix}_report.csv")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {v:5}  {k}")


if __name__ == "__main__":
    main()
