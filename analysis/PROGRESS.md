# Progress log

Resume point for any session. Latest state first in "Now"; history below. Repo: `Dimah02/test_port` (public).

## Now
- Phase 1, BLOCKED on a user decision: PHP extension sets differ per OS (Ubuntu 83 / macOS 64 / Windows 42 modules,
  koel smoke 3). Asked the user how to equalize. Pilot not started.

## Task list
- [x] Phase 0: environment check
- [ ] Phase 1: smoke-test review (+ fixes, rerun)
- [ ] Phase 2: pilot batch 0 (80 apps, 240 jobs) -> STOP, report, wait for "go"
- [ ] Phase 3: tag `full-run-1`, batches 1..14
- [ ] Phase 4: download + collect -> `analysis/run1_*`
- [ ] Phase 5: flaky check (3 reruns of differing repos)
- [ ] Phase 6: root causes + `analysis/REPORT.md`

## Phase 0 (2026-11-24) - done
- `gh auth status`: logged in as Dimah02, scopes gist, read:org, repo, workflow.
- `git remote -v`: origin https://github.com/Dimah02/test_port.git; branch main, up to date with origin.
- `python3 --version`: Python 3.14.3 (local).
- `gh repo view --json visibility,isPrivate`: PUBLIC, isPrivate=false. Actions permissions: enabled, all actions allowed.
- Workflow `portability` (id 429077106) active, on main.
- `make_matrix.py repos_verified.csv --stats --classes application`: 1171 selected, 1169 usable PHP, 2 no supported
  PHP, 3507 jobs, 15 batches of 80 (matches CLAUDE.md).
- Created `.gitignore` (results/, jobs_*.json, __pycache__/, logs/, results_excluded/), `analysis/`, `logs/`.
- `scripts/flaky_check.py`, `scripts/job_times.py` were untracked; committed in adcccf5. `CLAUDE.md` is still untracked (left to the user).

## Phase 1 (2026-11-24)
### Smoke test 1: run 45403055873, commit 3beb6d4 (user's run)
- 9/9 jobs succeeded, 9/9 artifacts. Results: `analysis/smoke_old_long.csv`, `analysis/smoke_old_wide.csv`.
  Raw files moved to `results_excluded/45403055873/` so the Phase 4/5 collectors (which glob `results/`) do not mix
  them into the study data.
- NOT "all worked":
  - Windows: all 3 jobs needed `install_mode=ignore_platform` (normal attempt rc=2); Ubuntu/macOS installed `normal`
    for koel and spatie. Reason unknown: the harness did not keep the failed attempt's output.
  - spatie/laravel-permission (package, out of scope) windows: `runner_crashed`, `PHP Parse error ... vendor/phpunit/
    phpunit/src/Runner/Version.php line 48` -> `--ignore-platform-reqs` installed a PHPUnit for a newer PHP. Harness artifact.
  - koel/koel windows: `runner_crashed`, `PHP Fatal error: Maximum execution time of 300 seconds exceeded in
    target\app\Helpers.php line 218` after ~1281/2635 tests; output full of `INFO: Could not find files for the given
    pattern(s).` (Windows `where.exe`). PHP's max_execution_time is wall-clock on Windows, CPU time on Linux/macOS.
    Looks like a real Windows difference (not a harness bug); recorded, not "fixed".
  - shop-admin-panel: `ignore_platform` on all 3 OSes, 4 tests / 3 errors on all 3 -> identical.
  - No `harness_error`, no `php_setup_failed`. setup-php on Windows enabled every extension (job log checked).
- Fix: change 1 in `analysis/CHANGELOG.md` (commit adcccf5).

### Smoke test 2: run 45407796200, commit adcccf5
- dispatched: `gh workflow run portability.yml -f only="spatie/laravel-permission,koel/koel,soheilkhaledabdi/shop-admin-panel"`
- Ubuntu+Windows jobs finished; 3 macOS jobs queued ~20 min (githubstatus: Actions operational). User asked to skip
  macOS for now -> run cancelled (`gh run cancel`), 6 artifacts downloaded, moved to `results_excluded/45407796200/`.
- New install tails: Windows lacks ext-sodium / ext-xsl (transitive requirements). -> change 2 (commit 113a4c5).

### Smoke test 3: commit 113a4c5
- run 45410333614 = `-f oses=ubuntu-24.04,windows-2025` (6/6 success); run 45410368758 = `-f oses=macos-15` (macOS
  queue slow: 1 of 3 jobs done after ~10 min).
- koel + spatie now install `normal` on Windows. spatie windows: 1 failing test (chmod read-only dir, file permissions;
  package, out of scope). koel windows still `runner_crashed` on max_execution_time 300 s (wall clock on Windows).
- `php_modules`: Ubuntu 83, macOS 64, Windows 42. Ubuntu-only: amqp apcu ast ds enchant igbinary imagick imap memcache
  memcached mongodb msgpack pdo_firebird pdo_sqlsrv redis snmp sqlsrv yaml zmq (+ all below). Ubuntu+macOS, not
  Windows: bz2 dba ffi ftp gettext ldap mysqli odbc pcntl pdo_dblib pdo_mysql pdo_odbc pdo_pgsql pgsql posix pspell
  shmop soap sysvmsg sysvsem sysvshm tidy. Contradicts the stated extension policy -> asked the user.
