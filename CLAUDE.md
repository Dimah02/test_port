# Laravel cross-OS portability study: project brief for Claude

You are working in my (the user's) copy of the repository `laravel-portability-study`. This file is everything you
need to know. Read it fully, then read `README.md`, then act. Do not assume anything that is not written here:
check it.

## 1. Goal

I am a researcher. I want to know whether the test suites of real Laravel **applications** from GitHub behave the
same on **Ubuntu, Windows and macOS**. The result is data for a paper: for each repository and OS, did the tests
pass, and which repositories behave differently per OS for a real reason (not because of flaky tests and not
because of a broken environment).

You run the whole study end to end on my behalf, in the phases of section 6.

## 2. Hard rules

1. **Never clone, install or run any repository from the list on this machine.** Third-party code runs only inside
   GitHub Actions jobs. Reading the CSV, the scripts and the downloaded `result.json` files is fine.
2. **Do not change `scripts/`, the workflow, `repos_verified.csv` or the PHP/DB policy while a full run is in
   progress** (notes and `analysis/` files may be pushed). Each dispatched
   run checks out the repository's default branch at dispatch time, so a change would mix two methods in one data
   set. If a fix is unavoidable: stop dispatching, fix, write the change in `analysis/CHANGELOG.md`, and rerun every
   batch that ran with the old code.
3. **Keep the selection flags identical on every `make_matrix.py` / `dispatch_batches.py` call:**
   `--classes application` and no `--min-stars`. Different flags between batches make batches overlap or skip repos.
4. **Never use "Re-run jobs" or `gh run rerun`.** `upload-artifact@v4` refuses a second artifact with the same name in
   the same run. Rerun by dispatching a new run (`gh workflow run ...`).
5. **No secrets in this repository**, keep `permissions: contents: read` in the workflow, never add
   `pull_request_target`, never add a PAT. The jobs execute other people's code.
6. No force-push, no history rewriting, no deleting runs/artifacts/branches unless I ask.
7. Do not commit `results/` (large) or `jobs_*.json`. Make sure `.gitignore` has: `results/`, `jobs_*.json`,
   `__pycache__/`, `logs/`. Commit `analysis/*.csv`, `analysis/*.md` and `lists/*.txt`.
8. Do not invent numbers. Every number you report comes from a CSV in `analysis/` or from a command whose output
   you show. Say clearly what you verified and what you only assume.
9. Never claim success from a launched command. After every dispatch, check the run's status with `gh run view`.

## 3. State of the project (as handed to you)

- `repos_verified.csv`: 5,353 GitHub repos, each classified from its root `composer.json` / `artisan` /
  `phpunit.xml(.dist)` by `scripts/verify_repos.py`. Counts: application 1,182, package 3,509, package_weak 307,
  not_laravel 150, no_root_composer 199, application_unclear 4, framework_itself 1, invalid_composer 1.
- **Scope of this study: applications only** (`--classes application`). `make_matrix.py --stats --classes application`
  gives 1,171 repos selected, 1,169 with a usable PHP version (2 need PHP < 7.4 and are skipped), i.e. 3,507 jobs in
  15 batches of 80.
- `has_tests_signal` is true for nearly all applications because Laravel's skeleton ships PHPUnit and an
  `ExampleTest`. The real test count is only known after a run: `result.json` has `tests` and `only_example_tests`.
- A smoke test of 3 repos on 3 OSes was already run by me and I told you "all worked". Verify it yourself in Phase 1;
  do not just trust this sentence.
- The scripts were developed and tested only against **fake projects on Linux**. The Windows and macOS code paths of
  `run_one.py` had never executed before my smoke test, and only 3 real repos have run so far. When something
  unexpected happens, suspect the harness first, then the repository.

## 4. Repository map

| Path | Purpose |
|---|---|
| `.github/workflows/portability.yml` | `workflow_dispatch` only. Job `prepare` builds the matrix, job `test` runs one job per (repo, OS), uploads `result-<os>-<owner>__<repo>` artifact (30 days). Inputs: `start`, `count`, `classes`, `min_stars`, `oses`, `only`, `only_file`, `frontend`. |
| `scripts/make_matrix.py` | Filters + sorts by stars desc + slices (`--start/--count`), picks a PHP version per repo, prints the matrix. `--stats` shows sizes. `--only`, `--only-file` select explicit repos. |
| `scripts/run_one.py` | Runs inside each job: composer install (fallbacks `no_scripts`, `ignore_platform`), `.env`, random `APP_KEY`, tests with `--log-junit`; writes `result.json` (outcome, counts, failed test names, log tails). Never fails the job because the target's tests fail. |
| `scripts/dispatch_batches.py` | Dispatches batches one after another with `gh`, waits for each (`gh run watch`). Flags: `--classes --min-stars --batch-size --first-batch --max-batches --oses --dry-run`. |
| `scripts/collect_results.py` | Merges all `result.json` (keeps the NEWEST per repo+OS), writes `<prefix>_long.csv`, `<prefix>_wide.csv` with `portability_verdict`. `--emit-stage2 FILE` writes repos whose Ubuntu run executed tests. |
| `scripts/flaky_check.py` | Keeps EVERY run per repo+OS; verdicts `OS-SPECIFIC (stable)`, `FLAKY on <os>`, `no difference`, `needs more runs`. Args: `--repos-file --out-prefix --min-runs`. |
| `scripts/job_times.py` | Reads `gh run view <id> --json jobs` output, projects the duration of the full run. Only used for the ETA at the pilot gate. |
| `scripts/verify_repos.py` | The classifier. Do not rerun it unless I ask. |
| `lists/` | Repo lists for `only_file` runs (for example `lists/differs.txt`). Must be committed and pushed before a workflow can read them. |
| `results/<run_id>/result-<os>-<slug>/result.json` | Downloaded artifacts (not committed). |
| `analysis/` | All outputs, `PROGRESS.md`, `CHANGELOG.md`, `REPORT.md`. |

If `scripts/job_times.py` or `scripts/flaky_check.py` is missing, tell me and wait; I have them separately.

## 5. Design decisions (do not change silently; list them in the report)

- Databases: SQLite in memory (`DB_CONNECTION=sqlite`, `DB_DATABASE=:memory:`), array cache/session, sync queue,
  array mail. GitHub service containers exist only on Linux runners, so MySQL/Postgres/Redis are not used on any OS.
  Apps whose tests need them fail on all OSes; that is not a portability finding.
- PHP: the lowest version in 7.4 to 8.5 that satisfies `require.php` (fallback: minimum PHP of the lowest allowed
  Laravel major, else 8.3). Extensions: a base list plus `ext-*` from `composer.json`.
- Runners are pinned: `ubuntu-24.04`, `windows-2025`, `macos-15` (Apple silicon).
- Frontend build (`npm ci && npm run build`) is OFF. Feature tests that need a Vite manifest may fail on all OSes.
- Line endings: Windows checks out with the runner's Git default; I have NOT verified whether that is
  `core.autocrlf=true`. If the report discusses line-ending failures, say so.
- GitHub Free limits: 20 concurrent jobs, 5 concurrent macOS jobs, 256 jobs per matrix (so at most 85 repos per
  batch with 3 OSes; we use 80), 6 h per job. This repo's jobs have `timeout-minutes: 60`, tests `--test-timeout 1500`.

## 6. Pipeline

Keep a task list (TaskCreate/TaskUpdate) and keep `analysis/PROGRESS.md` current: commands you ran, run ids,
which batches are done, what is next. Update it before you end any session, so another session can resume from it.

### Phase 0: environment
1. `gh auth status`, `git remote -v`, `git status`, `python3 --version`. Confirm the repository is **public** and has
   Actions enabled (`gh repo view --json visibility,isPrivate`). Confirm `.github/workflows/portability.yml` is on the
   default branch (dispatch only works for workflows present there).
2. Confirm `repos_verified.csv` and all scripts exist. Run `python3 scripts/make_matrix.py repos_verified.csv --stats --classes application`
   and check it prints 1171 selected / 1169 usable / 3507 jobs / 15 batches. If it differs, stop and tell me.
3. Create `analysis/`, `lists/`, `.gitignore` entries from rule 7.
If `gh` is not logged in, or Actions is disabled, tell me exactly what to do and wait.

### Phase 1: review the smoke test
1. Find the smoke-test run(s): `gh run list --workflow portability.yml --limit 10`. Download with
   `gh run download <id> --pattern 'result-*' --dir results/<id>`.
2. `python3 scripts/collect_results.py results/ --out-prefix analysis/smoke`. Open `analysis/smoke_long.csv`.
3. Check for harness problems: any `harness_error`, any `php_setup_failed`, `install_mode` other than `normal`,
   Windows/macOS-only failures that look like script bugs (paths, `shutil.which`, quoting), missing artifacts.
   Read `install_tail*` and `tests_tail` of anything suspicious. Report what you find; fix real harness bugs
   (allowed now, because no full run is in progress), re-run the smoke test after any fix.

### Phase 2: pilot (one batch of 80 apps, 240 jobs)
1. `python3 scripts/dispatch_batches.py --classes application --max-batches 1`. Run it in the background and log
   to `logs/dispatch_pilot.log`; poll `gh run view`.
2. When finished: download, `python3 scripts/collect_results.py results/ --out-prefix analysis/pilot`.
3. Health check (rules of thumb, not proven thresholds):
   - `harness_error` must be 0. Otherwise: bug in `run_one.py` or the workflow. Fix, rerun the pilot batch.
   - Every job should have produced an artifact. Missing artifacts mean job-level failures: look at the job logs
     (`gh run view <id> --log-failed`).
   - If more than about a quarter of the Ubuntu jobs end in `install_failed`, look at `install_tail_first_attempt`
     before scaling (typical causes: Composer/GitHub API rate limits, wrong PHP version choice).
   - Windows-only `php_setup_failed` / `install_failed` is probably a real finding (extension or PHP version not
     available on Windows). Count and record it, do not "fix" it.
4. **STOP and report to me:** outcome counts per OS, what was found/fixed, the portability-verdict counts, and an
   ETA for the full run (`gh run view <id> --json jobs > jobs_pilot.json`, then
   `python3 scripts/job_times.py jobs_pilot.json --repos 1169`). Wait for my "go". (If my first message told you to
   continue without asking, skip the wait.)

### Phase 3: full run
1. Commit everything, then tag the exact method: `git tag full-run-1 && git push --tags`.
2. `nohup python3 scripts/dispatch_batches.py --classes application --first-batch 1 > logs/dispatch_full.log 2>&1 &`
   (the process must stay alive for a day or more; if it dies, find the first unfinished batch with `gh run list`
   and restart with `--first-batch N`).
3. Poll every 30 to 60 minutes (cheap: `gh run list --workflow portability.yml --limit 5`). Do not push changes to
   scripts, workflow or CSV (rule 2). Record progress in `PROGRESS.md`. If a batch run is cancelled or ends with many failed jobs, tell me
   and redispatch that batch (same `--first-batch` index, same flags).

### Phase 4: collect
```bash
for id in $(gh run list --workflow portability.yml --limit 300 --json databaseId -q '.[].databaseId'); do
  [ -d results/$id ] || gh run download "$id" --pattern 'result-*' --dir results/$id
done
python3 scripts/collect_results.py results/ --out-prefix analysis/run1
```
Artifacts expire after 30 days: download as soon as the batches finish. Check that the number of repos in
`analysis/run1_wide.csv` equals 1169 minus repos without results, and list repos with missing OS results.

### Phase 5: flaky check (a verdict is only final after this)
1. Repos to rerun = those whose first-run verdict is `OUTCOME DIFFERS`, `test count differs` or `different tests fail`:
   ```bash
   grep -E 'OUTCOME DIFFERS|test count differs|different tests fail' analysis/run1_wide.csv | cut -d, -f1 > lists/differs.txt
   git add lists/differs.txt && git commit -m "rerun list" && git push
   ```
   (Committing a list file is allowed during Phase 5: it changes no code.)
2. Three new dispatches per batch of 80:
   ```bash
   N=$(wc -l < lists/differs.txt)
   for start in $(seq 0 80 $((N-1))); do for i in 1 2 3; do
     gh workflow run portability.yml -f only_file=lists/differs.txt -f start=$start -f count=80
   done; done
   ```
3. Wait until all of them finish, download as in Phase 4, then
   `python3 scripts/flaky_check.py results/ --repos-file lists/differs.txt --out-prefix analysis/flaky --min-runs 3`.
   (Original run + 3 reruns = 4 runs per repo and OS.)
4. Final classification: `OS-SPECIFIC (stable)` = real portability finding. `FLAKY on <os>` = reported separately.
   Four identical runs lower the risk of flakiness but do not rule it out; say so.

### Phase 6: root causes and report
1. For every `OS-SPECIFIC (stable)` repo, read the failing test names (`failed_tests`) and `tests_tail` in its
   `result.json` files and assign one cause: case-sensitive file names, path separators / `DIRECTORY_SEPARATOR`,
   line endings (CRLF), missing PHP extension on that OS (`pcntl`, `posix`), symlinks or file permissions, shell
   commands (`exec`, `ls`, `grep`), locale/timezone/number formatting, file locking, long paths, install failure
   (composer platform requirements), PHP version unavailable on that OS, other/unknown. Put this in
   `analysis/os_specific_causes.csv` (columns: repo, os, cause, evidence, confidence). When unsure write `unknown`,
   do not guess.
2. Write `analysis/REPORT.md`: scope and method (tag `full-run-1`, dates, runner labels, PHP policy, SQLite policy),
   counts (repos in scope, results obtained, outcome per OS), comparable repos (suite ran on all three OSes),
   verdict counts before and after the flaky check, cause table with 3 to 5 concrete examples per cause,
   threats to validity (SQLite instead of the real DB, lowest-PHP policy, example-only test suites, 4 runs only,
   unverified autocrlf, 2 skipped repos, no frontend build, apps in subfolders missed by the classifier), and the file
   list. Numbers must trace to the CSVs.
3. Commit `analysis/` and `lists/`, push, and finish with a short summary for me.

## 7. Troubleshooting

- `gh workflow run` returns 404: the workflow file is not on the default branch, or Actions is disabled in the
  repository settings. Tell me.
- Runs stay "queued" for a long time: the free plan has 20 concurrent jobs and 5 macOS jobs. That is normal.
- `gh run download` says artifacts expired or gone: the 30-day retention passed; that batch has to be redispatched.
- `gh run list --limit` caps at the number you give: raise it if there are more runs than that.
- Anything that contradicts this file: stop, show me the evidence, wait.
