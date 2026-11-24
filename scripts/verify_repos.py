#!/usr/bin/env python3
"""Classify GitHub repositories as real Laravel projects (or not).

Reads a CSV with at least a `name` column (owner/repo) and, for each repo,
looks at a few files in the default branch via raw.githubusercontent.com:

    composer.json          (GET)   -> dependencies, PHP constraint, extensions
    artisan                (HEAD)  -> Laravel application entry point
    phpunit.xml / .dist    (HEAD)  -> test configuration

No GitHub API token is needed. Results are cached in a JSONL file, so an
interrupted run resumes where it stopped.

Classification (column `classification`):
    application      has `artisan` and depends on laravel/* or illuminate/*
    package          no artisan, but depends on laravel/*, illuminate/* or
                     orchestra/testbench (a Laravel package / library)
    package_weak     no hard Laravel dependency, but "laravel" appears in the
                     package name/keywords/dependency names, or it uses
                     filament/livewire/inertia (review by hand)
    framework_itself laravel/framework (monorepo; tested differently)
    application_unclear  has `artisan` but no Laravel dependency in composer.json
    not_laravel      has composer.json but no Laravel signal
    no_root_composer no composer.json at the repo root (non-PHP, subfolder
                     layout, or the repo was deleted/renamed - raw returns 404)
    invalid_composer composer.json is not valid JSON
    fetch_error      network / HTTP error that survived retries

Usage:
    python3 verify_repos.py repos.csv repos_verified.csv
    python3 verify_repos.py repos.csv out.csv --sample 100 --seed 1   # quick test
"""
import argparse
import csv
import json
import random
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

RAW = "https://raw.githubusercontent.com/{name}/HEAD/{path}"
UA = "laravel-portability-study/1.0"

OUT_FIELDS = [
    "classification", "has_tests_signal", "has_artisan", "has_phpunit_config",
    "phpunit_dep", "pest_dep", "testbench_dep", "laravel_constraint",
    "php_constraint", "ext_requires", "composer_name", "composer_type",
    "fetch_error",
]


def http(method, url, retries=5, timeout=30):
    """Return (status, body). status 0 means all retries failed."""
    delay = 2
    for _ in range(retries):
        req = urllib.request.Request(url, method=method, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, (resp.read() if method == "GET" else b"")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return 404, None
            if e.code in (429, 500, 502, 503, 504):
                retry_after = e.headers.get("Retry-After", "")
                time.sleep(int(retry_after) if retry_after.isdigit() else delay)
                delay = min(delay * 2, 60)
                continue
            return e.code, None
        except (urllib.error.URLError, OSError):
            time.sleep(delay)
            delay = min(delay * 2, 60)
    return 0, None


def as_dict(value):
    return value if isinstance(value, dict) else {}


def analyze(name):
    rec = {f: "" for f in OUT_FIELDS}
    status, body = http("GET", RAW.format(name=name, path="composer.json"))
    if status == 404:
        rec["classification"] = "no_root_composer"
        return rec
    if status != 200:
        rec["classification"] = "fetch_error"
        rec["fetch_error"] = f"HTTP {status}"
        return rec
    try:
        composer = json.loads(body.decode("utf-8-sig"))
        if not isinstance(composer, dict):
            raise ValueError("composer.json is not an object")
    except (ValueError, UnicodeDecodeError):
        rec["classification"] = "invalid_composer"
        return rec

    require = as_dict(composer.get("require"))
    require_dev = as_dict(composer.get("require-dev"))
    deps = {**require_dev, **require}

    laravel_dep = "laravel/framework" in deps
    illuminate = any(k.startswith("illuminate/") for k in deps)
    laravel_pkg = any(k.startswith("laravel/") for k in deps)
    testbench = any(k.startswith("orchestra/testbench") for k in deps)

    st_artisan, _ = http("HEAD", RAW.format(name=name, path="artisan"))
    has_artisan = st_artisan == 200
    st_a, _ = http("HEAD", RAW.format(name=name, path="phpunit.xml.dist"))
    st_b = 0
    if st_a != 200:
        st_b, _ = http("HEAD", RAW.format(name=name, path="phpunit.xml"))
    has_phpunit_config = st_a == 200 or st_b == 200

    phpunit_dep = "phpunit/phpunit" in deps
    pest_dep = "pestphp/pest" in deps

    keywords = composer.get("keywords") if isinstance(composer.get("keywords"), list) else []
    soft_signal = (
        "laravel" in str(composer.get("name", "")).lower()
        or any("laravel" in str(k).lower() for k in keywords)
        or any("laravel" in k or k.startswith(("filament/", "livewire/", "inertiajs/")) for k in deps)
    )

    if composer.get("name") == "laravel/framework":
        cls = "framework_itself"
    elif has_artisan and (laravel_dep or illuminate or laravel_pkg):
        cls = "application"
    elif laravel_dep or illuminate or testbench or laravel_pkg:
        cls = "package"
    elif has_artisan:
        cls = "application_unclear"
    elif soft_signal:
        cls = "package_weak"  # Laravel-flavoured, but no hard dependency on Laravel
    else:
        cls = "not_laravel"

    rec.update(
        classification=cls,
        has_tests_signal=has_phpunit_config or phpunit_dep or pest_dep,
        has_artisan=has_artisan,
        has_phpunit_config=has_phpunit_config,
        phpunit_dep=phpunit_dep,
        pest_dep=pest_dep,
        testbench_dep=testbench,
        laravel_constraint=deps.get("laravel/framework") or deps.get("illuminate/support") or "",
        php_constraint=deps.get("php", ""),
        ext_requires=" ".join(sorted(k[4:] for k in require if k.startswith("ext-"))),
        composer_name=composer.get("name", ""),
        composer_type=composer.get("type", ""),
    )
    return rec


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input_csv")
    ap.add_argument("output_csv")
    ap.add_argument("--cache", default=None, help="JSONL cache (default: <output_csv>.cache.jsonl)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--sample", type=int, default=0, help="only process a random sample of N repos")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--max-seconds", type=float, default=0, help="stop submitting new work after this many seconds (resume later)")
    args = ap.parse_args()
    cache_path = args.cache or args.output_csv + ".cache.jsonl"

    with open(args.input_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if args.sample:
        random.Random(args.seed).shuffle(rows)
        rows = rows[: args.sample]

    cache = {}
    try:
        with open(cache_path, encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                cache[rec["name"]] = rec
    except FileNotFoundError:
        pass

    todo = [r["name"] for r in rows if r["name"] not in cache]
    print(f"{len(rows)} repos, {len(cache)} cached, {len(todo)} to fetch", file=sys.stderr)

    lock = threading.Lock()
    started = time.time()
    done = 0
    with open(cache_path, "a", encoding="utf-8") as cf, ThreadPoolExecutor(args.workers) as pool:
        futures = {}
        it = iter(todo)

        def submit_next():
            if args.max_seconds and time.time() - started > args.max_seconds:
                return False
            n = next(it, None)
            if n is None:
                return False
            futures[pool.submit(analyze, n)] = n
            return True

        for _ in range(args.workers * 2):
            if not submit_next():
                break
        while futures:
            for fut in as_completed(list(futures)):
                n = futures.pop(fut)
                rec = fut.result()
                rec["name"] = n
                with lock:
                    cf.write(json.dumps(rec) + "\n")
                    cf.flush()
                cache[n] = rec
                done += 1
                if done % 100 == 0:
                    print(f"  {done}/{len(todo)}  ({done / (time.time() - started):.1f} repos/s)", file=sys.stderr)
                submit_next()
                break

    fieldnames = ["name", "url", "stars", "description"] + OUT_FIELDS
    counts = {}
    with open(args.output_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            rec = cache.get(r["name"])
            if rec is None:
                continue  # not processed yet (--max-seconds) -> resume later
            w.writerow({**r, **rec})
            counts[rec["classification"]] = counts.get(rec["classification"], 0) + 1
    print("classification counts:", json.dumps(counts, sort_keys=True), file=sys.stderr)


if __name__ == "__main__":
    main()
