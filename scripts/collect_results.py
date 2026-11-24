#!/usr/bin/env python3
"""Aggregate the result.json files downloaded from the workflow runs.

    # download (one directory per workflow run), then aggregate
    for id in $(gh run list --workflow portability.yml --limit 200 --json databaseId -q '.[].databaseId'); do
        gh run download "$id" --pattern 'result-*' --dir results/"$id"
    done
    python3 scripts/collect_results.py results/ --out-prefix analysis/run1

Writes  <prefix>_long.csv   one row per (repo, OS)
        <prefix>_wide.csv   one row per repo, with a portability verdict
and prints a summary. With --emit-stage2 FILE it also writes the repos whose Linux
run executed tests (the only ones worth running on Windows/macOS).
"""
import argparse
import csv
import glob
import json
import os
from collections import Counter, defaultdict

COMPARABLE = {"passed", "tests_failed", "nonzero_exit"}   # the test suite really ran
OS_ORDER = ["ubuntu", "windows", "macos"]
LONG_FIELDS = ["repo", "os_name", "outcome", "stage_reached", "classification", "php_requested", "php_actual",
               "install_mode", "runner", "tests", "failures", "errors", "skipped", "assertions",
               "only_example_tests", "install_secs", "test_secs", "total_secs", "runner_os", "runner_machine"]


def load(results_dir):
    rows = {}
    for path in glob.glob(os.path.join(results_dir, "**", "result.json"), recursive=True):
        try:
            with open(path, encoding="utf-8") as f:
                r = json.load(f)
        except (OSError, ValueError):
            continue
        key = (r.get("repo"), r.get("os_name"))
        # if a repo was run more than once, keep the most recent result
        if key not in rows or r.get("started_utc", "") >= rows[key].get("started_utc", ""):
            rows[key] = r
    return list(rows.values())


def verdict(per_os):
    """Compare the OSes of one repo."""
    present = {o: r for o, r in per_os.items()}
    if not all(o in present for o in OS_ORDER):
        return "incomplete (missing OS result)"
    if not all(present[o]["outcome"] in COMPARABLE for o in OS_ORDER):
        return "not comparable (suite did not run on: " + ",".join(
            f"{o}={present[o]['outcome']}" for o in OS_ORDER if present[o]["outcome"] not in COMPARABLE) + ")"
    ok = {o: present[o]["outcome"] == "passed" for o in OS_ORDER}
    if len(set(ok.values())) > 1:
        return "OUTCOME DIFFERS (" + ",".join(f"{o}={'pass' if v else 'fail'}" for o, v in ok.items()) + ")"
    if len({present[o].get("tests") for o in OS_ORDER}) > 1:
        return "test count differs"
    if len({tuple(present[o].get("failed_tests", [])) for o in OS_ORDER}) > 1:
        return "different tests fail"
    return "identical (all pass)" if all(ok.values()) else "identical (same failures everywhere)"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results_dir")
    ap.add_argument("--out-prefix", default="analysis")
    ap.add_argument("--emit-stage2", default="", help="write repos whose ubuntu run executed tests to this file")
    args = ap.parse_args()

    results = load(args.results_dir)
    os.makedirs(os.path.dirname(os.path.abspath(args.out_prefix)), exist_ok=True)

    with open(args.out_prefix + "_long.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LONG_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in sorted(results, key=lambda r: (r["repo"], r["os_name"])):
            w.writerow(r)

    by_repo = defaultdict(dict)
    for r in results:
        by_repo[r["repo"]][r["os_name"]] = r

    wide_fields = ["repo", "classification", "php_requested", "portability_verdict"]
    for o in OS_ORDER:
        wide_fields += [f"{o}_outcome", f"{o}_tests", f"{o}_failures_errors"]
    wide_fields += ["failed_only_on"]
    verdicts = Counter()
    with open(args.out_prefix + "_wide.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=wide_fields)
        w.writeheader()
        for repo in sorted(by_repo):
            per_os = by_repo[repo]
            any_r = next(iter(per_os.values()))
            row = {"repo": repo, "classification": any_r.get("classification", ""),
                   "php_requested": any_r.get("php_requested", ""), "portability_verdict": verdict(per_os)}
            failing_tests = {}
            for o in OS_ORDER:
                r = per_os.get(o)
                row[f"{o}_outcome"] = r["outcome"] if r else "(no result)"
                row[f"{o}_tests"] = r.get("tests", "") if r else ""
                row[f"{o}_failures_errors"] = (r.get("failures", 0) + r.get("errors", 0)) if r and "failures" in r else ""
                if r:
                    failing_tests[o] = set(r.get("failed_tests", []))
            if len(failing_tests) == 3:
                everywhere = set.intersection(*failing_tests.values())
                row["failed_only_on"] = "; ".join(
                    f"{o}:{len(failing_tests[o] - everywhere)}" for o in OS_ORDER if failing_tests[o] - everywhere)
            w.writerow(row)
            verdicts[row["portability_verdict"].split(" (")[0]] += 1

    print(f"{len(results)} results, {len(by_repo)} repos")
    print("\noutcomes per OS:")
    for o in OS_ORDER:
        c = Counter(r["outcome"] for r in results if r["os_name"] == o)
        print(f"  {o:8}", dict(c.most_common()))
    print("\nportability verdicts:")
    for k, v in verdicts.most_common():
        print(f"  {v:5}  {k}")

    if args.emit_stage2:
        names = sorted(repo for repo, per in by_repo.items()
                       if per.get("ubuntu", {}).get("outcome") in COMPARABLE)
        with open(args.emit_stage2, "w", encoding="utf-8") as f:
            f.write("\n".join(names) + ("\n" if names else ""))
        print(f"\nstage-2 list: {len(names)} repos -> {args.emit_stage2}")


if __name__ == "__main__":
    main()
