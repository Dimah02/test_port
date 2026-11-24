#!/usr/bin/env python3
"""Install and test ONE checked-out Laravel project on the current OS and write result.json.

Runs identically on Linux, macOS and Windows (only Python + php + composer needed).
Never exits non-zero because the *target's* tests failed; the outcome is recorded in
the JSON instead, so one bad project cannot hide the results of the others.

Stages (result["stage_reached"]):  checkout -> install -> env -> frontend -> tests -> done

result["outcome"]:
    passed           tests ran, none failed
    tests_failed     tests ran, >=1 failure/error
    nonzero_exit     all executed tests passed, but the runner exited != 0 (e.g. failOnWarning)
    no_tests         runner started but zero test cases were executed
    no_runner        neither phpunit nor pest is installed after composer install
    install_failed   composer install failed even after the fallbacks
    checkout_failed  the repository could not be checked out (deleted/renamed/private)
    php_setup_failed setup-php could not provide the requested PHP version on this OS
    no_composer      no composer.json at the checkout root
    timeout          the test run exceeded --test-timeout
    runner_crashed   non-zero exit and no JUnit report was produced
    harness_error    bug in this script (look at result["error"])
"""
import argparse
import base64
import datetime
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

TAIL_CHARS = 6000
TEST_ENV_DEFAULTS = {
    "APP_ENV": "testing",
    "APP_DEBUG": "true",
    "DB_CONNECTION": "sqlite",
    "DB_DATABASE": ":memory:",
    "CACHE_DRIVER": "array",
    "CACHE_STORE": "array",
    "SESSION_DRIVER": "array",
    "QUEUE_CONNECTION": "sync",
    "MAIL_MAILER": "array",
    "BROADCAST_DRIVER": "log",
    "BROADCAST_CONNECTION": "log",
    "LOG_CHANNEL": "stderr",
}


def tail(path, n=TAIL_CHARS):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - n))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def kill_tree(proc):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        proc.kill()


class Runner:
    def __init__(self, cwd, logs_dir, env):
        self.cwd, self.logs_dir, self.env = cwd, logs_dir, env
        self.phases = {}
        os.makedirs(logs_dir, exist_ok=True)

    def run(self, name, cmd, timeout, env=None):
        log_path = os.path.join(self.logs_dir, f"{name}.log")
        kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        start, timed_out = time.time(), False
        with open(log_path, "wb") as log:
            try:
                proc = subprocess.Popen(
                    cmd, cwd=self.cwd, env=env or self.env, stdout=log, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL, **kwargs,
                )
                try:
                    rc = proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    kill_tree(proc)
                    proc.wait()
                    rc, timed_out = -9, True
            except OSError as e:
                log.write(f"could not start {cmd}: {e}\n".encode())
                rc = 127
        info = {"rc": rc, "timed_out": timed_out, "secs": round(time.time() - start, 1), "tail": tail(log_path)}
        self.phases[name] = info
        return info


def parse_junit(path):
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None
    out = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "assertions": 0}
    failed, classes = [], set()
    for tc in root.iter("testcase"):
        out["tests"] += 1
        cls = tc.get("class") or tc.get("classname") or ""
        classes.add(cls)
        kids = {c.tag for c in tc}
        if "failure" in kids or "error" in kids:
            out["failures" if "failure" in kids else "errors"] += 1
            failed.append(f"{cls}::{tc.get('name', '')}")
        elif "skipped" in kids:
            out["skipped"] += 1
        try:
            out["assertions"] += int(tc.get("assertions") or 0)
        except ValueError:
            pass
    out["only_example_tests"] = bool(classes) and all(c.endswith("ExampleTest") for c in classes)
    out["failed_tests"] = sorted(failed)[:300]
    return out


def php_version(php, env):
    try:
        return subprocess.run([php, "-r", "echo PHP_VERSION;"], capture_output=True, text=True, env=env, timeout=30).stdout.strip()
    except Exception:
        return ""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--target", required=True, help="directory with the checked-out project")
    ap.add_argument("--out", required=True, help="result.json to write")
    ap.add_argument("--os-name", default=platform.system().lower())
    ap.add_argument("--php-requested", default="")
    ap.add_argument("--php-reason", default="")
    ap.add_argument("--classification", default="")
    ap.add_argument("--checkout-outcome", default="success", help="outcome of the checkout step (success|failure)")
    ap.add_argument("--php-setup-outcome", default="success", help="outcome of the setup-php step (success|failure|skipped)")
    ap.add_argument("--install-timeout", type=int, default=1200)
    ap.add_argument("--test-timeout", type=int, default=1500)
    ap.add_argument("--frontend", action="store_true", help="try npm ci/install + npm run build before the tests")
    args = ap.parse_args()

    t0 = time.time()
    result = {
        "repo": args.repo, "os_name": args.os_name, "runner_os": platform.system(), "runner_machine": platform.machine(),
        "classification": args.classification, "php_requested": args.php_requested, "php_reason": args.php_reason,
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "stage_reached": "checkout", "outcome": "harness_error",
    }
    try:
        run_all(args, result)
    except Exception as e:  # never fail the job because of a bug here
        result["outcome"] = "harness_error"
        result["error"] = f"{type(e).__name__}: {e}"
    result["total_secs"] = round(time.time() - t0, 1)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)
    print(f"[run_one] {args.repo} on {args.os_name}: {result['outcome']} "
          f"(tests={result.get('tests')}, failures={result.get('failures')}, errors={result.get('errors')})")


def run_all(args, result):
    if args.checkout_outcome != "success" or not os.path.isdir(args.target):
        result["outcome"] = "checkout_failed"
        return
    target = os.path.abspath(args.target)
    if not os.path.isfile(os.path.join(target, "composer.json")):
        result["outcome"] = "no_composer"
        return
    if args.php_setup_outcome == "failure":
        result["outcome"] = "php_setup_failed"  # e.g. the requested PHP/extension is unavailable on this OS
        return

    env = os.environ.copy()
    env.update({
        "COMPOSER_NO_INTERACTION": "1", "COMPOSER_MEMORY_LIMIT": "-1",
        "COMPOSER_PROCESS_TIMEOUT": str(args.install_timeout), "COMPOSER_ALLOW_SUPERUSER": "1",
        "GIT_TERMINAL_PROMPT": "0",
    })
    php, composer = shutil.which("php"), shutil.which("composer")
    if not php or not composer:
        raise RuntimeError(f"php={php} composer={composer} not found on PATH")
    result["php_actual"] = php_version(php, env)
    logs_dir = os.path.join(os.path.dirname(os.path.abspath(args.out)), "logs")
    r = Runner(target, logs_dir, env)

    # ---- install (with fallbacks, so the *reason* for trouble is recorded) -------------------
    result["stage_reached"] = "install"
    base = [composer, "install", "--no-interaction", "--no-progress", "--prefer-dist"]
    ladder = [("normal", base), ("no_scripts", base + ["--no-scripts"]),
              ("ignore_platform", base + ["--no-scripts", "--ignore-platform-reqs"])]
    result["install_attempts"] = []
    install_secs = 0.0
    for mode, cmd in ladder:
        info = r.run(f"composer_{mode}", cmd, args.install_timeout)
        install_secs += info["secs"]
        result["install_attempts"].append({"mode": mode, "rc": info["rc"]})
        if info["rc"] == 0:
            result["install_mode"] = mode
            break
    result["install_secs"] = round(install_secs, 1)
    if "install_mode" not in result:
        result["outcome"] = "install_failed"
        result["install_tail"] = r.phases[f"composer_{ladder[-1][0]}"]["tail"]
        result["install_tail_first_attempt"] = r.phases["composer_normal"]["tail"]
        return

    # ---- environment (apps) --------------------------------------------------------------------
    result["stage_reached"] = "env"
    env_file, env_example = os.path.join(target, ".env"), os.path.join(target, ".env.example")
    if not os.path.exists(env_file) and os.path.exists(env_example):
        shutil.copyfile(env_example, env_file)
    test_env = dict(env)
    for k, v in TEST_ENV_DEFAULTS.items():
        test_env.setdefault(k, v)
    test_env.setdefault("APP_KEY", "base64:" + base64.b64encode(os.urandom(32)).decode())
    result["has_artisan"] = os.path.exists(os.path.join(target, "artisan"))

    # ---- optional frontend build ---------------------------------------------------------------
    if args.frontend and os.path.exists(os.path.join(target, "package.json")) and shutil.which("npm"):
        result["stage_reached"] = "frontend"
        npm = shutil.which("npm")
        has_lock = os.path.exists(os.path.join(target, "package-lock.json"))
        a = r.run("npm_install", [npm, "ci" if has_lock else "install", "--no-audit", "--no-fund"], 900)
        b = r.run("npm_build", [npm, "run", "build"], 900) if a["rc"] == 0 else {"rc": "skipped"}
        result["frontend"] = {"install_rc": a["rc"], "build_rc": b["rc"]}

    # ---- tests ----------------------------------------------------------------------------------
    result["stage_reached"] = "tests"
    pest = os.path.join(target, "vendor", "pestphp", "pest", "bin", "pest")
    phpunit = os.path.join(target, "vendor", "phpunit", "phpunit", "phpunit")
    if os.path.exists(pest):
        runner, script = "pest", pest
    elif os.path.exists(phpunit):
        runner, script = "phpunit", phpunit
    else:
        result["outcome"] = "no_runner"
        return
    result["runner"] = runner
    junit = os.path.join(os.path.dirname(os.path.abspath(args.out)), "junit.xml")
    if os.path.exists(junit):
        os.remove(junit)
    info = r.run("tests", [php, "-d", "memory_limit=-1", script, "--colors=never", "--log-junit", junit],
                 args.test_timeout, env=test_env)
    result["test_secs"] = info["secs"]
    result["test_rc"] = info["rc"]
    result["tests_tail"] = info["tail"]
    parsed = parse_junit(junit)
    if parsed:
        result.update(parsed)

    if info["timed_out"]:
        result["outcome"] = "timeout"
    elif parsed is None:
        result["outcome"] = "runner_crashed" if info["rc"] != 0 else "no_tests"
    elif parsed["tests"] == 0:
        result["outcome"] = "no_tests"
    elif parsed["failures"] or parsed["errors"]:
        result["outcome"] = "tests_failed"
    elif info["rc"] != 0:
        result["outcome"] = "nonzero_exit"  # every test passed but the runner still exited != 0 (e.g. failOnWarning)
    else:
        result["outcome"] = "passed"
    if result["outcome"] in ("passed", "tests_failed", "nonzero_exit", "no_tests"):
        result["stage_reached"] = "done"


if __name__ == "__main__":
    main()
