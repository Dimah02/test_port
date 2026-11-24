#!/usr/bin/env python3
"""Turn repos_verified.csv into a GitHub Actions job matrix (one job per repo x OS).

Selection is deterministic (filter, then sort by stars desc, then slice), so
batches started with --start/--count never overlap as long as the filter flags
stay the same between batches.

PHP version policy: the LOWEST version in 7.4..8.5 that satisfies the repo's
`require.php` constraint (closest to the environment the project was written
for). Repos without a php constraint fall back to the minimum PHP of the lowest
Laravel major their constraint allows, else 8.3. Override per repo with a
`php_override` column in the CSV.

Usage:
    python3 make_matrix.py repos_verified.csv --stats
    python3 make_matrix.py repos_verified.csv --start 0 --count 80 --github-output "$GITHUB_OUTPUT"
"""
import argparse
import csv
import json
import re
import sys

CANDIDATES = [(7, 4), (8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5)]
# minimum PHP for each Laravel major (clamped to CANDIDATES)
LARAVEL_MIN_PHP = {5: (7, 4), 6: (7, 4), 7: (7, 4), 8: (7, 4), 9: (8, 0), 10: (8, 1), 11: (8, 2), 12: (8, 2), 13: (8, 3)}
DEFAULT_PHP = (8, 3)
BASE_EXTENSIONS = [
    "mbstring", "intl", "pdo_sqlite", "sqlite3", "gd", "zip", "bcmath", "fileinfo",
    "curl", "openssl", "dom", "xml", "ctype", "tokenizer", "simplexml", "libxml", "exif",
    # enabled by default on Ubuntu/macOS but not in setup-php's Windows PHP; often required by dependencies
    # (e.g. lcobucci/jwt -> sodium, veewee/xml -> xsl). Without them Windows gets a different PHP than the others.
    "sodium", "xsl", "gmp", "sockets", "iconv", "xmlreader", "xmlwriter", "calendar",
]
EXT_BLOCKLIST = {"xdebug", "pcov", "json", "hash", "standard", "core", "spl", "date", "pcre", "reflection"}


# ---------------------------------------------------------------- constraints
def _parse(ver):
    """'8.1.*' -> ((8, 1, 0), nparts=2, wildcard=True); '^8' -> ((8,0,0), 1, False)"""
    ver = ver.strip().lstrip("vV")
    parts = re.split(r"\.", ver)
    nums, wildcard = [], False
    for p in parts[:3]:
        if p in ("*", "x", "X"):
            wildcard = True
            break
        m = re.match(r"^(\d+)", p)
        if not m:
            break
        nums.append(int(m.group(1)))
    if not nums:
        return None
    n = len(nums)
    return tuple(nums + [0] * (3 - n)), n, wildcard


def _term_ok(term, v):
    term = re.sub(r"@\w+$", "", term.strip())
    if term in ("", "*", "x", "X") or term.startswith("dev-"):
        return True
    m = re.match(r"^(\^|~>|~|>=|<=|<>|!=|==|=|>|<)?(.+)$", term)
    op, ver = (m.group(1) or ""), m.group(2)
    parsed = _parse(ver)
    if parsed is None:
        return True  # unparseable -> do not exclude
    lo, n, wildcard = parsed
    nxt_major = (lo[0] + 1, 0, 0)
    nxt_minor = (lo[0], lo[1] + 1, 0)
    if op == "^":
        return lo <= v < (nxt_major if lo[0] > 0 else nxt_minor)
    if op in ("~", "~>"):
        return lo <= v < (nxt_minor if n >= 3 else nxt_major)
    if op == ">=":
        return v >= lo
    if op == ">":
        return v > lo
    if op == "<":
        return v < lo
    if op == "<=":
        if n < 3 or wildcard:
            return v < (nxt_minor if n == 2 else nxt_major)
        return v <= lo
    if op in ("!=", "<>"):
        return not _prefix_match(lo, n, wildcard, v)
    return _prefix_match(lo, n, wildcard, v)  # '', '=', '=='


def _prefix_match(lo, n, wildcard, v):
    if wildcard or n < 3:
        return v[:n] == lo[:n]
    return v == lo


def satisfies(constraint, v):
    """True if version tuple v satisfies a Composer constraint string."""
    constraint = re.sub(r"(\S+)\s+-\s+(\S+)", r">=\1 <=\2", constraint.strip())
    for group in re.split(r"\s*\|\|?\s*", constraint):
        group = re.sub(r"(>=|<=|<>|!=|==|>|<|=|\^|~>|~)\s+", r"\1", group.strip())
        terms = [t for t in re.split(r"[\s,]+", group) if t]
        if all(_term_ok(t, v) for t in terms):
            return True
    return False


def _any_patch(constraint, major, minor):
    return satisfies(constraint, (major, minor, 0)) or satisfies(constraint, (major, minor, 99))


def pick_php(php_constraint, laravel_constraint):
    """Return (version_string or None, reason)."""
    if php_constraint.strip():
        for mj, mn in CANDIDATES:
            if _any_patch(php_constraint, mj, mn):
                return f"{mj}.{mn}", "php_constraint"
        return None, "no_supported_php"
    if laravel_constraint.strip():
        for major in sorted(LARAVEL_MIN_PHP):
            if satisfies(laravel_constraint, (major, 0, 0)) or satisfies(laravel_constraint, (major, 99, 99)):
                mj, mn = LARAVEL_MIN_PHP[major]
                return f"{mj}.{mn}", f"laravel_{major}"
    return f"{DEFAULT_PHP[0]}.{DEFAULT_PHP[1]}", "default"


# --------------------------------------------------------------------- matrix
def build_extensions(ext_requires):
    exts = list(BASE_EXTENSIONS)
    for e in ext_requires.split():
        e = e.lower()
        if e not in EXT_BLOCKLIST and e not in exts:
            exts.append(e)
    return ", ".join(exts)


def select_rows(rows, classes, min_stars, require_tests):
    out = []
    for r in rows:
        if r["classification"] not in classes:
            continue
        if int(r.get("stars") or 0) < min_stars:
            continue
        if require_tests and r.get("has_tests_signal") != "True":
            continue
        out.append(r)
    out.sort(key=lambda r: (-int(r.get("stars") or 0), r["name"].lower()))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("verified_csv")
    ap.add_argument("--classes", default="application,package")
    ap.add_argument("--min-stars", type=int, default=0)
    ap.add_argument("--include-no-tests", action="store_true", help="do not require a phpunit/pest signal")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--count", type=int, default=80, help="repos per batch (x OSes must stay <= 256 jobs)")
    ap.add_argument("--oses", default="ubuntu-24.04,windows-2025,macos-15")
    ap.add_argument("--only", default="", help="comma-separated owner/repo list (smoke tests); ignores class/star/test filters")
    ap.add_argument("--only-file", default="", help="text file, one owner/repo per line, order kept (e.g. stage-2 list); ignores filters")
    ap.add_argument("--github-output", default=None, help="path of $GITHUB_OUTPUT; otherwise JSON goes to stdout")
    ap.add_argument("--stats", action="store_true", help="print how many repos/jobs the filter selects and exit")
    args = ap.parse_args()

    oses = [o.strip() for o in args.oses.split(",") if o.strip()]
    if args.count * len(oses) > 256:
        sys.exit(f"{args.count} repos x {len(oses)} OSes = {args.count * len(oses)} jobs > 256 (GitHub matrix limit)")

    with open(args.verified_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    wanted = [n.strip() for n in args.only.split(",") if n.strip()]
    if args.only_file:
        with open(args.only_file, encoding="utf-8") as f:
            wanted += [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    if wanted:
        by_name = {r["name"].lower(): r for r in rows}
        missing = [n for n in wanted if n.lower() not in by_name]
        if missing:
            sys.exit(f"not in {args.verified_csv}: {missing}")
        selected = [by_name[n.lower()] for n in wanted]
    else:
        selected = select_rows(rows, set(args.classes.split(",")), args.min_stars, not args.include_no_tests)

    if args.stats:
        usable = skipped = 0
        for r in selected:
            php, _ = pick_php(r["php_constraint"], r["laravel_constraint"])
            usable += php is not None
            skipped += php is None
        print(f"selected repos: {len(selected)}  (usable PHP: {usable}, no supported PHP: {skipped})")
        print(f"jobs for all of them on {len(oses)} OSes: {usable * len(oses)}")
        print(f"batches of {args.count}: {-(-len(selected) // args.count)}")
        return

    batch = selected[args.start: args.start + args.count]
    include = []
    for r in batch:
        php = (r.get("php_override") or "").strip()
        reason = "php_override"
        if not php:
            php, reason = pick_php(r["php_constraint"], r["laravel_constraint"])
        if php is None:
            print(f"skip {r['name']}: {reason} ({r['php_constraint']})", file=sys.stderr)
            continue
        for os_label in oses:
            include.append({
                "repo": r["name"],
                "slug": r["name"].replace("/", "__"),
                "os": os_label,
                "os_name": os_label.split("-")[0],
                "php": php,
                "php_reason": reason,
                "extensions": build_extensions(r["ext_requires"]),
                "classification": r["classification"],
            })

    matrix = json.dumps({"include": include}, separators=(",", ":"))
    n_repos = len({i["repo"] for i in include})
    print(f"batch start={args.start} count={args.count}: {n_repos} repos, {len(include)} jobs", file=sys.stderr)
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as f:
            f.write(f"matrix={matrix}\nn={n_repos}\n")
    else:
        print(matrix)


if __name__ == "__main__":
    main()
