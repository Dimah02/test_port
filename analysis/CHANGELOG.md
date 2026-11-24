# Method changelog

Every change to `scripts/`, the workflow, `repos_verified.csv` or the PHP/DB policy, with the reason and which
runs used the old code. Runs are identified by their GitHub Actions run id.

## 2026-11-24 - change 1 (before the pilot; no full run in progress)

**Found in:** smoke-test run 45403055873 (commit 3beb6d4).

**Problems**
1. The Composer output of a failed install attempt was only kept when *all* attempts failed. In the smoke test all
   3 Windows jobs fell back to `ignore_platform` (normal attempt rc=2), and the reason could not be read.
2. The last fallback `--ignore-platform-reqs` also ignores the PHP version. For a project without `composer.lock`
   (spatie/laravel-permission on Windows), Composer then resolved a PHPUnit release that needs a newer PHP than the
   job's PHP 8.3: `PHP Parse error: syntax error, unexpected token "->" in vendor/phpunit/phpunit/src/Runner/Version.php`
   -> outcome `runner_crashed`. That is an artifact of the harness, not of the project or the OS.

**Change** (`scripts/run_one.py`, README updated accordingly)
- New fallback step `ignore_ext` = `composer install --no-scripts --ignore-platform-req=ext-*` between `no_scripts`
  and `ignore_platform`: missing extensions (e.g. `pcntl`/`posix` on Windows) are ignored, the PHP constraint is kept.
- Every failed install attempt now stores its output tail in `install_attempts[i].tail` (last 3,000 characters).
  `install_tail` / `install_tail_first_attempt` are unchanged.

**Runs with the old code:** 45403055873 (smoke test only). It is not part of the study data set; the smoke test is
rerun with the new code.

## 2026-11-24 - change 2 (before the pilot; no full run in progress)

**Found in:** smoke-test run 45407796200 (commit adcccf5; Ubuntu + Windows jobs only, the 3 macOS jobs stayed
queued for ~20 min and the run was cancelled at the user's request). The new `install_attempts[].tail` showed:
- koel/koel windows: `php-standard-library/php-standard-library 4.3.0 requires ext-sodium * -> it is missing from your
  system` and `veewee/xml 3.7.0 requires ext-xsl * -> it is missing from your system` (`C:\tools\php\php.ini`).
- spatie/laravel-permission windows: `lcobucci/jwt[5.1.0, ..., 5.6.x-dev] require ext-sodium * -> it is missing`.
Ubuntu installed both repos in mode `normal`. These extensions are enabled by default in setup-php's Linux/macOS PHP
but not on Windows, and they are *transitive* requirements, so they never appear in the repo's own `composer.json`
(`ext_requires`). Windows therefore ran with a smaller PHP than the other OSes: a harness inequality, not a property
of the OS.

**Change**
- `scripts/make_matrix.py`: `BASE_EXTENSIONS` += `sodium, xsl, gmp, sockets, iconv, xmlreader, xmlwriter, calendar`
  (all available on Windows). `pcntl`/`posix` stay out: they do not exist on Windows (genuine difference).
- `scripts/run_one.py`: records `php_modules` (output of `php -m`, lower-case) in `result.json`, so remaining
  extension differences between the OSes can be measured instead of assumed.

**Runs with the old code:** 45403055873, 45407796200 (smoke tests only, not part of the data set).
