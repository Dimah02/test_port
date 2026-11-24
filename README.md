# Laravel cross-OS portability study

Runs the test suites of GitHub Laravel projects on **Ubuntu, Windows and macOS** with GitHub Actions,
records one `result.json` per (repo, OS), and tells you which projects behave differently per OS.

```
repos_verified.csv            5,353 repos, each classified (application / package / not Laravel ...)
scripts/verify_repos.py       classifier (re-run it any time; needs no token)
scripts/make_matrix.py        picks repos + a PHP version per repo -> job matrix (one job per repo x OS)
scripts/run_one.py            installs + tests ONE project on the current OS, writes result.json
scripts/collect_results.py    merges all result.json files, compares the OSes per repo
scripts/dispatch_batches.py   starts the workflow batch after batch with `gh`
.github/workflows/portability.yml
```

## What `repos_verified.csv` says about your list

| classification | repos | meaning |
|---|---:|---|
| application | 1,182 | has `artisan` and depends on laravel/* or illuminate/* |
| package | 3,509 | no `artisan`, depends on laravel/*, illuminate/* or orchestra/testbench |
| package_weak | 307 | "laravel" in name/keywords/dependency names, but no hard dependency (review by hand) |
| not_laravel | 150 | has composer.json, no Laravel signal |
| no_root_composer | 199 | no `composer.json` at the repo root (non-PHP, Laravel in a subfolder, or repo deleted/renamed) |
| application_unclear / framework_itself / invalid_composer | 4 / 1 / 1 | check by hand (`laravel/framework` is the framework monorepo itself) |

`has_tests_signal` = phpunit.xml(.dist) exists or phpunit/pest is in composer.json. **It is true for almost every
application, because Laravel's skeleton ships PHPUnit and an `ExampleTest`.** The real test count only appears when the
tests run: `result.json` has `tests` and `only_example_tests`, so filter those out afterwards.

## Steps

1. **Make a public repository** and copy this folder into it (public => standard runners are free).
   Use a dedicated repo with no secrets: the jobs run other people's code.
   ```bash
   gh repo create laravel-portability-study --public --clone
   cp -r <this folder>/. laravel-portability-study/ && cd laravel-portability-study
   git add -A && git commit -m "portability study" && git push
   ```
2. **Choose what to test** and see how big it is:
   ```bash
   python3 scripts/make_matrix.py repos_verified.csv --stats                      # applications + packages with a test signal
   python3 scripts/make_matrix.py repos_verified.csv --stats --min-stars 50
   python3 scripts/make_matrix.py repos_verified.csv --stats --classes application
   ```
3. **Smoke test** three repos on all three OSes (9 jobs) and read the results before scaling up:
   ```bash
   gh workflow run portability.yml -f only="spatie/laravel-permission,koel/koel,soheilkhaledabdi/shop-admin-panel"
   gh run list --workflow portability.yml --limit 1
   ```
4. **Pilot, then full run.** Batches run one after another (macOS is limited to 5 concurrent jobs on a free plan):
   ```bash
   python3 scripts/dispatch_batches.py --min-stars 50 --dry-run
   python3 scripts/dispatch_batches.py --min-stars 50 --max-batches 1      # pilot: 80 repos = 240 jobs
   python3 scripts/dispatch_batches.py --min-stars 50 --first-batch 1      # the rest
   ```
   Keep the filter flags identical between invocations, otherwise batches overlap or skip repos.
5. **Collect and compare:**
   ```bash
   for id in $(gh run list --workflow portability.yml --limit 200 --json databaseId -q '.[].databaseId'); do
     gh run download "$id" --pattern 'result-*' --dir results/"$id"
   done
   python3 scripts/collect_results.py results/ --out-prefix analysis/run1
   ```
   `analysis/run1_wide.csv` has one row per repo with a `portability_verdict`:
   `OUTCOME DIFFERS`, `test count differs`, `different tests fail`, `identical (...)`,
   `not comparable (...)` (the suite did not run on some OS) or `incomplete`.
6. **Optional, saves most of the Windows/macOS time (two-stage run):** run stage 1 on Ubuntu only
   (`-f oses=ubuntu-24.04`), then run Windows+macOS only for repos whose Ubuntu run executed tests:
   ```bash
   python3 scripts/collect_results.py results/ --out-prefix analysis/stage1 --emit-stage2 lists/stage2.txt
   git add lists/stage2.txt && git commit -m "stage 2 list" && git push
   gh workflow run portability.yml -f only_file=lists/stage2.txt -f oses=windows-2025,macos-15 -f count=80 -f start=0
   ```
   (repeat with `start=80, 160 ...`). A repo that does not even run on Linux cannot say anything about portability.

## What the workflow does per job

checkout target -> `setup-php` (version below) -> `composer install` (falls back to `--no-scripts`, then
`--ignore-platform-reqs`; the mode used is recorded in `install_mode`) -> copy `.env.example` -> random `APP_KEY` ->
tests with `--log-junit`. Tests run with `DB_CONNECTION=sqlite`, `DB_DATABASE=:memory:`, array cache/session,
sync queue, array mailer. Optional `-f frontend=true` also runs `npm ci && npm run build`.

## Decisions that affect your conclusions (write them in the paper)

- **Databases/services.** GitHub service containers (`services:`) only work on Linux runners. Everything here uses
  SQLite in memory so the three OSes are equal. Projects whose tests need MySQL/Postgres/Redis fail on *all* OSes
  (shows up as `identical (same failures everywhere)`), not as a portability difference.
- **PHP version.** The *lowest* version in 7.4-8.5 satisfying `require.php` (closest to the original environment).
  If there is no php constraint, the minimum PHP of the lowest Laravel major allowed, else 8.3. Override per repo
  with a `php_override` column in the CSV. Repos needing PHP < 7.4 are skipped (`make_matrix.py` prints them).
- **Runner images are pinned** (`ubuntu-24.04`, `windows-2025`, `macos-15` = Apple silicon). `*-latest` would move
  during a multi-day study; GitHub's current `macos-latest` is macOS 26.
- **Line endings.** Git for Windows normally defaults to `autocrlf=true` (check what your runner does; I did not verify it), which
  is what a typical Windows developer gets. To separate real OS differences from CRLF effects, add a step with
  `git config --global core.autocrlf false` before the checkout.
- **Case sensitivity.** Linux is case-sensitive, Windows and (by default) macOS are not: a genuine portability class, found via the failing tests.
- **`ext-pcntl`, `ext-posix`** (Horizon, Octane, queues) do not exist on Windows. `composer install` fails there, the
  `ignore_platform` fallback runs the tests anyway, and `install_mode` tells you it happened.
- **Skeleton tests.** Many applications only have `ExampleTest`; use `only_example_tests` / `tests` to drop them.
- **Time limits.** 60 min per job, 25 min for the test run (`--test-timeout`), after which the outcome is `timeout`.
- **Flaky tests.** One run per (repo, OS) cannot separate flaky from OS-specific failures. For the repos with an
  `OUTCOME DIFFERS` verdict, rerun them 2-3 times before drawing conclusions.

## Cost and time

All numbers are estimates. Standard GitHub-hosted runners are free for public repositories.
Free-plan limits: 20 concurrent jobs, **5 concurrent macOS jobs**, 256 jobs per matrix, 6 h per job.
With the default filter: 4,308 repos = 12,918 jobs (54 batches); with `--min-stars 50`: 1,418 repos = 4,254 jobs (18 batches).
If the average macOS job takes about 6 minutes, 5 concurrent macOS jobs finish about 50 jobs per hour, so 1,418 macOS jobs take
roughly 28 hours. Measure the pilot batch and extrapolate.

## Limits of the classifier

- It looks at the repo root only (`composer.json`, `artisan`, `phpunit.xml(.dist)` on the default branch at the time you run it).
  Laravel apps that live in a subfolder (e.g. `backend/`) land in `no_root_composer`.
- A deleted/renamed repo and a non-PHP repo both look like `no_root_composer`.
- `package_weak` and `application_unclear` need a manual look.

## Not verified yet

The scripts were tested with fake projects on Linux (every outcome path, the matrix builder, the collector) and the workflow
passes `actionlint`, but the workflow has **not** run on real runners, and the Windows/macOS code paths of `run_one.py`
were not executed. That is what the smoke test in step 3 is for.
"# test_port" 
