"""The built-in rules: what a failing log line looks like, what it usually means, what to try.

A rule fires when any of its patterns matches a line of the failing step's output. Named groups in
the pattern fill ``{placeholders}`` in the explanation and the fixes. The rule with the highest
``priority`` that fired is the primary cause: a missing module (70) beats the test summary it
caused (45), and a full disk (85) beats whatever broke because of it.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    priority: int
    patterns: tuple[re.Pattern[str], ...]
    explanation: str
    fixes: tuple[str, ...] = ()
    requires: re.Pattern[str] | None = None  # the step output must also match this
    source: str = "builtin"
    extra: dict[str, str] = field(default_factory=dict, compare=False)


def _r(
    id: str,
    title: str,
    priority: int,
    patterns: list[str],
    explanation: str,
    fixes: list[str],
    requires: str | None = None,
) -> Rule:
    return Rule(
        id=id,
        title=title,
        priority=priority,
        patterns=tuple(re.compile(p) for p in patterns),
        explanation=explanation,
        fixes=tuple(fixes),
        requires=re.compile(requires) if requires else None,
    )


BUILTIN: tuple[Rule, ...] = (
    # --- the runner and the workflow itself -------------------------------------------------
    _r(
        "action-not-found",
        "A step uses an action that cannot be resolved",
        90,
        [
            r"Unable to resolve action `(?P<action>[^`]+)`",
            r"Can't find 'action\.yml', 'action\.yaml' or 'Dockerfile' under",
            r"Missing download info for",
        ],
        "The job could not even start the step that uses `{action}`: the repository, the path or the "
        "ref after the `@` does not exist, or the action is blocked for this repository.",
        [
            "Check the spelling of owner/name and that the tag or branch after `@` exists.",
            "A private action needs a repository that allows access from this one "
            "(Settings > Actions > General).",
            "An organisation can restrict which actions are allowed; check that policy.",
        ],
    ),
    _r(
        "missing-input",
        "A required input of an action is empty",
        85,
        [r"Input required and not provided: (?P<input>\S+)"],
        "The action wants the input `{input}` and got nothing. When the value comes from a secret, "
        "the secret is empty or does not exist for this run.",
        [
            "Check the secret name for typos and that it exists at the right level "
            "(repository, environment or organisation).",
            "Pull requests from forks and Dependabot runs do not get repository secrets.",
        ],
    ),
    _r(
        "token-permissions",
        "The GITHUB_TOKEN is not allowed to do what the step tried",
        85,
        [
            r"Resource not accessible by integration",
            r"remote: Permission to \S+ denied to github-actions\[bot\]",
        ],
        "The token the workflow runs with has no permission for this call or push. The default "
        "token is often read-only, and it is always read-only for pull requests from forks.",
        [
            "Add a `permissions:` block to the job with the scope that is missing "
            "(for example `contents: write`, `pull-requests: write`, `issues: write`).",
            "Check Settings > Actions > General > Workflow permissions.",
            "For pull requests from forks use a separate `pull_request_target` workflow with care, "
            "or do the write step after merge.",
        ],
    ),
    _r(
        "setup-version-not-found",
        "A setup action cannot find the requested tool version",
        84,
        [
            r"The version '(?P<version>[^']+)' with architecture '(?P<arch>[^']+)' was not found",
            r"Version (?P<version>\S+) was not found in the local cache",
            r"Unable to find Go version '(?P<version>[^']+)'",
            r"No matching version found for SemVer '(?P<version>[^']+)'",
            r"Could not find satisfied version for SemVer '(?P<version>[^']+)'",
        ],
        "The setup action cannot provide version `{version}`: it does not exist, was misspelt "
        "(`3.10` written as the number `3.1` in YAML is a classic), or is not published for this "
        "operating system image.",
        [
            'Quote versions in YAML: `python-version: "3.10"`.',
            "Check the version against the list the setup action documents for this runner image.",
        ],
    ),
    _r(
        "runner-lost",
        "The runner went away during the job",
        80,
        [
            r"The runner has received a shutdown signal",
            r"lost communication with the server",
            r"The hosted runner encountered an error",
        ],
        "The machine running the job was shut down or lost its connection. This is not a problem "
        "in your code.",
        [
            "Re-run the job.",
            "On self-hosted runners check the machine (spot or preemptible instance, network, disk).",
        ],
    ),
    _r(
        "job-timeout",
        "The job ran out of time or was cancelled",
        75,
        [
            r"exceeded the maximum execution time",
            r"The operation was canceled\.",
        ],
        "The job hit `timeout-minutes` (360 by default) or was cancelled, for example by a newer "
        "push when `concurrency: cancel-in-progress` is set.",
        [
            "Look at which step was running when it stopped; a hang usually means a command waiting "
            "for input or a service that never came up.",
            "Set a per-step `timeout-minutes` so that a hang fails early and says where.",
        ],
    ),
    # --- the machine ------------------------------------------------------------------------
    _r(
        "disk-full",
        "The runner ran out of disk space",
        85,
        [r"No space left on device", r"\bENOSPC\b"],
        "The disk is full. Docker layers, build caches and unpacked toolchains fill the roughly "
        "14 GB of free space on a standard runner quickly.",
        [
            "Remove what the job does not need before the big step "
            "(for example `/usr/share/dotnet`, `/opt/ghc`, `/usr/local/lib/android`).",
            "Run `docker system prune -af` between image builds, or use a smaller build context.",
            "Use a larger runner.",
        ],
    ),
    _r(
        "out-of-memory",
        "A process was killed, most likely for lack of memory",
        80,
        [
            r"Process completed with exit code 137\b",
            r"\bOOMKilled\b",
            r"JavaScript heap out of memory",
            r"\bCannot allocate memory\b",
            r"^Killed$",
            r"^MemoryError\b",
        ],
        "Exit code 137 is SIGKILL; on a CI machine that is most often the kernel killing the "
        "biggest process because memory ran out. A Node.js heap error and Python's `MemoryError` "
        "are the same problem seen from inside.",
        [
            "Lower the parallelism (`-j`, `--workers`, `--maxWorkers`).",
            "For Node.js raise the heap with `NODE_OPTIONS=--max-old-space-size=...` only if the machine "
            "has the memory.",
            "Use a larger runner, or split the job.",
        ],
    ),
    # --- network and registries -------------------------------------------------------------
    _r(
        "rate-limit",
        "A rate limit was hit",
        70,
        [
            r"(?i)API rate limit exceeded",
            r"toomanyrequests",
            r"429 Too Many Requests",
            r"You have reached your (?:unauthenticated )?pull rate limit",
        ],
        "A service refused the request because the caller made too many. Docker Hub limits "
        "anonymous pulls per IP address, and shared runner addresses use that allowance up.",
        [
            "Log in to the registry in the workflow, or pull from a mirror such as `public.ecr.aws` "
            "or `ghcr.io`.",
            "Pass the `GITHUB_TOKEN` to tools that call the GitHub API.",
            "Cache what you download.",
        ],
    ),
    _r(
        "dns",
        "A host name could not be resolved",
        60,
        [
            r"Could not resolve host: (?P<host>\S+)",
            r"Temporary failure in name resolution",
            r"getaddrinfo (?:ENOTFOUND|EAI_AGAIN) (?P<host>\S+)",
            r"Name or service not known",
            r"dial tcp: lookup (?P<host>\S+).*no such host",
        ],
        "A name lookup failed (`{host}`). It is either a typo or a transient DNS problem on the "
        "runner, or the service is only reachable from inside a private network.",
        [
            "Check the host name; open it from your own machine.",
            "If it worked yesterday, re-run; add retries to the download.",
            "A host on a private network is not reachable from a hosted runner.",
        ],
    ),
    _r(
        "network-transient",
        "A network request timed out or was cut",
        55,
        [
            r"\bETIMEDOUT\b",
            r"\bECONNRESET\b",
            r"Connection timed out",
            r"TLS handshake timeout",
            r"connection reset by peer",
            r"\b50[234] (?:Bad Gateway|Service Unavailable|Gateway Time-?out)",
            r"Read timed out",
            r"SSL: UNEXPECTED_EOF",
        ],
        "A download or API call did not complete. These are usually temporary problems at the "
        "other end, and the same job often passes on a second try.",
        [
            "Re-run the failed job.",
            "Add retries where the job downloads things (`pip --retries 5`, `curl --retry 5`, "
            "a retry wrapper for the step).",
            "Cache dependencies so the job does not depend on the network at all.",
        ],
    ),
    _r(
        "connection-refused",
        "Nothing was listening where the step tried to connect",
        52,
        [
            r"connect ECONNREFUSED (?P<address>\S+)",
            r"Connection refused",
            r"could not connect to server",
            r"Can't connect to MySQL server",
        ],
        "The step connected to a port where no service answered (`{address}`). Usually the service "
        "container is still starting, or the host name or port is wrong.",
        [
            "Give the service a health check (`options: --health-cmd ...`); the job waits for it "
            "before the first step.",
            "A job that runs on the runner reaches a service container at `localhost:<mapped port>`; "
            "a job that runs in a container reaches it by the service name.",
            "Wait in a loop for the port before running the tests.",
        ],
    ),
    # --- git and credentials ----------------------------------------------------------------
    _r(
        "git-auth",
        "Git could not authenticate",
        80,
        [
            r"fatal: could not read Username",
            r"Permission denied \(publickey\)",
            r"Authentication failed for",
            r"Support for password authentication was removed",
            r"remote: Repository not found",
            r"fatal: repository '[^']+' not found",
        ],
        "A git operation was rejected. The `GITHUB_TOKEN` only covers the repository the workflow "
        "runs in, so a private submodule or a second private repository needs its own credential.",
        [
            "Give `actions/checkout` a `token:` (a fine-grained personal access token or a GitHub App "
            "token with read access to the other repository), or use a deploy key.",
            "For a submodule set `submodules: recursive` together with that token.",
        ],
    ),
    _r(
        "terraform-credentials",
        "Terraform has no cloud credentials",
        82,
        [
            r"No valid credential sources found",
            r"could not find default credentials",
            r"error configuring Terraform AWS Provider",
        ],
        "The provider looked for credentials and found none. In CI they have to be passed in "
        "explicitly; nothing is inherited from a developer machine.",
        [
            "Authenticate in an earlier step (OIDC with `aws-actions/configure-aws-credentials` or "
            "`google-github-actions/auth` avoids long-lived keys).",
            "The job needs `permissions: id-token: write` for OIDC.",
            "For `terraform validate` and `plan` of a config you only want to check, use "
            "`-backend=false` and dummy credentials where the provider allows it.",
        ],
        requires=r"(?i)terraform|\.tf\b",
    ),
    _r(
        "terraform-lock",
        "Terraform cannot get the state lock",
        80,
        [r"Error acquiring the state lock"],
        "Another run holds the state lock, or an earlier run died and left it behind.",
        [
            "Check whether another plan or apply is running; wait for it.",
            "If a run was killed, `terraform force-unlock <id>` (the id is in the message) releases it. "
            "Be sure nothing is running first.",
        ],
    ),
    # --- dependency installation ------------------------------------------------------------
    _r(
        "npm-ci-lock",
        "package.json and the lock file disagree",
        88,
        [r"can only install packages when your package\.json and package-lock\.json"],
        "`npm ci` refuses to install when the lock file does not match `package.json`, "
        "which means someone changed one without the other.",
        [
            "Run `npm install` locally and commit the updated `package-lock.json`.",
            "Make sure the Node and npm versions in CI are close to the ones used locally; "
            "different npm versions write different lock files.",
        ],
    ),
    _r(
        "npm-eresolve",
        "npm cannot find a set of compatible dependency versions",
        78,
        [r"npm (?:ERR!|error) code ERESOLVE", r"Could not resolve dependency"],
        "Two packages ask for versions of a third that cannot both be satisfied (usually a peer dependency).",
        [
            "Read the lines after `Could not resolve dependency`: they name the packages and the ranges.",
            "Align the versions, or as a stop-gap install with `--legacy-peer-deps`.",
        ],
    ),
    _r(
        "npm-missing-script",
        "package.json has no such script",
        72,
        [r"npm (?:ERR!|error) Missing script: (?P<script>.+)"],
        "The workflow runs `npm run {script}` but `package.json` has no script by that name.",
        [
            "Add the script, or fix the name in the workflow.",
            "Check that the step runs in the right `working-directory`.",
        ],
    ),
    _r(
        "node-module-missing",
        "Node.js cannot find a module",
        70,
        [
            r"Error \[ERR_MODULE_NOT_FOUND\]: Cannot find (?:package|module) '(?P<module>[^']+)'",
            r"Cannot find module '(?P<module>[^']+)'",
        ],
        "`{module}` is imported but not installed (or built).",
        [
            "Add it to `dependencies` (or `devDependencies` for build tools) and commit the lock file.",
            "If it is a local file, check the path and the letter case; the CI file system is "
            "case-sensitive.",
            "If it is a build output, make sure the build step ran first.",
        ],
    ),
    _r(
        "pip-no-matching",
        "pip finds no installable release",
        75,
        [
            r"No matching distribution found for (?P<package>\S+)",
            r"Could not find a version that satisfies the requirement (?P<package>\S+)",
            r"Ignored the following versions that require a different Python version",
        ],
        "pip has no release of `{package}` that fits this Python version and platform. The name may "
        "be misspelt, the version pinned too high, or the package has no wheel for this Python yet.",
        [
            "Check the package name and the pinned version on PyPI.",
            "Compare the `python-version` of the job with the one the package supports.",
            "For a private index, check `--index-url` and the credentials.",
        ],
    ),
    _r(
        "pip-conflict",
        "pip cannot satisfy all version requirements at once",
        75,
        [r"ResolutionImpossible", r"conflicting dependencies", r"The conflict is caused by"],
        "Two requirements need versions of the same package that exclude each other.",
        [
            "The lines under `The conflict is caused by` name the packages and the ranges.",
            "Loosen or align the pins; split tools with conflicting needs into separate environments.",
        ],
    ),
    _r(
        "pip-build-fail",
        "A Python package failed to build from source",
        70,
        [
            r"Failed building wheel for (?P<package>\S+)",
            r"Could not build wheels for (?P<package>\S+)",
            r"error: subprocess-exited-with-error",
        ],
        "pip had no ready-made wheel for `{package}` and tried to compile it, which needs a compiler "
        "and the C headers of the library it wraps.",
        [
            "Look further up for the first compiler error: it names the missing header or library.",
            "Install the system package first (`apt-get install -y libfoo-dev`), or pin a version that "
            "has a wheel for this Python.",
        ],
    ),
    _r(
        "apt-missing-package",
        "apt cannot find a package",
        76,
        [
            r"E: Unable to locate package (?P<package>\S+)",
            r"E: Package '(?P<package>[^']+)' has no installation candidate",
        ],
        "apt does not know `{package}`. The package index may be stale, or the package was renamed "
        "or dropped in the Ubuntu release the runner image now uses.",
        [
            "Run `sudo apt-get update` before `apt-get install`.",
            "If `ubuntu-latest` moved to a new release, check the package name there, or pin the "
            "image (`ubuntu-24.04`).",
        ],
    ),
    _r(
        "apt-lock",
        "apt is locked by another process",
        70,
        [r"Could not get lock /var/lib/(?:dpkg|apt)/", r"Unable to acquire the dpkg frontend lock"],
        "Another package manager process is running. On a fresh hosted runner that is usually an "
        "automatic update that started with the machine.",
        [
            "Wait and retry, or use `-o DPkg::Lock::Timeout=300` so apt waits for the lock.",
        ],
    ),
    _r(
        "apt-fetch",
        "apt could not download packages",
        62,
        [
            r"E: Failed to fetch (?P<url>\S+)",
            r"Hash Sum mismatch",
            r"E: Some index files failed to download",
        ],
        "A mirror did not deliver a package or index, or served files that changed in between. "
        "Mirrors do this for a few minutes at a time.",
        ["Re-run; run `apt-get update` right before the install."],
    ),
    _r(
        "go-version",
        "go.mod needs a newer Go than the job has",
        82,
        [
            r"requires go >= (?P<need>[\d.]+) \(running go (?P<have>[\d.]+)",
            r"go\.mod requires go >= (?P<need>[\d.]+)",
        ],
        "A module needs Go {need} or newer, and the job runs an older one.",
        [
            "Raise `go-version` in the `setup-go` step, or use `go-version-file: go.mod`.",
        ],
    ),
    _r(
        "go-mod-tidy",
        "go.mod or go.sum is out of date",
        76,
        [
            r"go: updates to go\.mod needed",
            r"missing go\.sum entry for module providing package (?P<package>\S+)",
            r"go: inconsistent vendoring",
        ],
        "The module files do not match the imports in the code.",
        ["Run `go mod tidy` (and `go mod vendor` if you vendor) locally and commit the result."],
    ),
    # --- the code and the tests -------------------------------------------------------------
    _r(
        "python-syntax",
        "Python could not parse a file",
        70,
        [r"^\s*SyntaxError: (?P<message>.*)", r"^\s*IndentationError: (?P<message>.*)"],
        "A file has a syntax error: `{message}`. If it passes on your machine, the job probably runs "
        "a different Python version than you do.",
        [
            "The traceback above names the file and line.",
            "Compare `python-version` in the workflow with your local version "
            "(for example `match` needs 3.10, `except*` needs 3.11).",
        ],
    ),
    _r(
        "python-module-missing",
        "Python cannot import a module that is not installed",
        70,
        [r"ModuleNotFoundError: No module named '(?P<module>[^']+)'"],
        "`{module}` is imported but not installed in the job's environment.",
        [
            "Add it to the requirements the install step uses (or to `pyproject.toml`).",
            "If it is your own package, install it (`pip install -e .`) or set `PYTHONPATH`.",
            "Check that the install step ran, and ran with the same Python as the failing step.",
        ],
    ),
    _r(
        "python-import-error",
        "Python imports a name that the installed version does not have",
        65,
        [r"ImportError: cannot import name '(?P<name>[^']+)' from '(?P<module>[^']+)'"],
        "`{name}` is not in `{module}` as installed: a version mismatch, a circular import, or a "
        "local file shadowing the real module.",
        [
            "Compare the installed version of `{module}` with the one you develop against.",
            "Pin the version, or adapt the import.",
        ],
    ),
    _r(
        "pytest-timeout",
        "A test exceeded its timeout",
        66,
        [r"Failed: Timeout >(?P<seconds>[\d.]+)s", r"\+{5,} Timeout \+{5,}"],
        "A test ran longer than {seconds} s (pytest-timeout). The traceback printed after the "
        "timeout banner shows where it was waiting.",
        [
            "A test that waits for a service, a lock or input is usually the cause, not a slow machine.",
            "Add a deadline to the call the test is waiting in.",
        ],
    ),
    _r(
        "pytest-collection",
        "pytest could not even collect the tests",
        47,
        [
            r"^ERROR collecting (?P<file>\S+)",
            r"Interrupted: \d+ errors? during collection",
            r"^ERROR (?P<file>\S+\.py)(?: - .*)?$",
        ],
        "pytest failed while importing `{file}`, before running anything. The error under the "
        "`ERROR collecting` banner says why (usually an import or a syntax error).",
        ["Run `pytest --collect-only` locally in a clean environment."],
    ),
    _r(
        "pytest-failures",
        "Tests failed",
        45,
        [
            r"^FAILED (?P<test>\S+::\S+)(?: - (?P<message>.*))?$",
            r"^=+ (?P<count>\d+) failed",
        ],
        "A test assertion failed (first: `{test}`, {message}). The failure section above the summary "
        "shows the values that were compared.",
        [
            "Reproduce with `pytest {test}`.",
            "If it only fails in CI, compare Python and dependency versions, the locale and time "
            "zone, the working directory and the environment variables.",
        ],
    ),
    _r(
        "unittest-failures",
        "Tests failed (unittest)",
        42,
        [r"^(?:FAIL|ERROR): (?P<test>\w+ \([\w.]+\))", r"^FAILED \((?:failures|errors)=\d+"],
        "A unittest test failed (first: `{test}`).",
        ["Run it alone with `python -m unittest -v`."],
    ),
    _r(
        "python-lint",
        "The Python linter reported problems",
        50,
        [r"^Found (?P<count>\d+) errors?\.$", r"^\S+\.py:\d+:\d+: [A-Z]{1,3}\d{3,4} "],
        "The lint step found {count} problems. Each line has `file:line:column: CODE message`.",
        ["Run the same linter locally (with the same version) and fix or configure what it reports."],
    ),
    _r(
        "mypy-errors",
        "mypy reported type errors",
        52,
        [r"^Found (?P<count>\d+) errors? in (?P<files>\d+) files?"],
        "mypy found {count} errors in {files} files.",
        ["Run `mypy` locally with the same version and config; type stubs differ between versions."],
    ),
    _r(
        "typescript-error",
        "The TypeScript compiler reported errors",
        55,
        [r"error TS(?P<code>\d+): (?P<message>.*)"],
        "The build stopped on `TS{code}`: {message}",
        [
            "Run `npx tsc --noEmit` locally.",
            "If it only fails in CI, compare the TypeScript version and the installed `@types/*`.",
        ],
    ),
    _r(
        "jest-failures",
        "Tests failed (Jest)",
        46,
        [
            r"^Tests:\s+(?P<count>\d+) failed",
            r"^FAIL\s+(?P<file>\S+\.(?:js|jsx|ts|tsx|mjs|cjs))",
            r"^\s*●\s+.+ › ",
        ],
        "A Jest test failed (first file: `{file}`). The `●` blocks above the summary show the expected "
        "and received values.",
        ["Run `npx jest {file}` locally; compare Node versions and the timezone."],
    ),
    _r(
        "eslint-errors",
        "ESLint reported errors",
        50,
        [r"✖ (?P<count>\d+) problems? \((?P<errors>\d+) errors?"],
        "ESLint found {count} problems ({errors} errors).",
        ["Run `npx eslint .` locally; `--fix` handles many of them."],
    ),
    _r(
        "go-test-timeout",
        "A Go test timed out",
        68,
        [r"^panic: test timed out after (?P<duration>\S+)"],
        "The test binary was killed after {duration}. The goroutine dump after the panic shows "
        "what every goroutine was waiting for.",
        ["Find the test that is running in the dump (`Test...`) and what it blocks on."],
    ),
    _r(
        "data-race",
        "The Go race detector found a data race",
        72,
        [r"WARNING: DATA RACE"],
        "Two goroutines touched the same memory without synchronisation. The report lists both "
        "stacks with file and line.",
        ["Fix the race (a mutex, a channel, `sync/atomic`); a passing run without `-race` proves nothing."],
    ),
    _r(
        "go-compile",
        "Go code does not compile",
        62,
        [
            r"^(?P<file>\S+\.go):\d+:\d+: (?:undefined|cannot use|declared and not used|"
            r"imported and not used|missing return|syntax error)",
            r"\[build failed\]",
        ],
        "A compile error in `{file}`.",
        ["Build locally with the same Go version (`go build ./...` and `go vet ./...`)."],
    ),
    _r(
        "go-test-fail",
        "Tests failed (Go)",
        50,
        [r"^--- FAIL: (?P<test>\S+)", r"^FAIL\s+(?P<package>\S+)"],
        "A Go test failed (first: `{test}`).",
        ["Run `go test -run '^{test}$' -v ./...` locally; add `-race` and `-count=1` as CI does."],
    ),
    _r(
        "rust-compile",
        "Rust code does not compile",
        55,
        [
            r"^error\[E(?P<code>\d+)\]: (?P<message>.*)",
            r"error: could not compile `(?P<crate>[^`]+)`",
        ],
        "The build of `{crate}` stopped on an error: {message}",
        ["Run `cargo build` locally with the same toolchain (`rust-toolchain.toml`)."],
    ),
    _r(
        "maven-gradle",
        "The Java build failed",
        55,
        [
            r"\[ERROR\] Failed to execute goal (?P<goal>\S+)",
            r"FAILURE: Build failed with an exception",
            r"^> Task (?P<goal>\S+) FAILED",
        ],
        "The build tool stopped at `{goal}`. The first `[ERROR]` or `What went wrong` block says why.",
        ["Run the same task locally with the same JDK."],
    ),
    _r(
        "docker-pull-denied",
        "Docker could not pull an image",
        72,
        [
            r"pull access denied for (?P<image>[^,\s]+)",
            r"denied: requested access to the resource is denied",
            r"manifest (?:for \S+ )?(?:not found|unknown)",
        ],
        "The registry refused or does not have the image (`{image}`): a typo in the name or tag, "
        "a private image without a login, or a tag that was deleted.",
        [
            "Check the image name and tag by pulling it on your machine.",
            "Log in with `docker/login-action` before the pull.",
        ],
    ),
    _r(
        "docker-build",
        "A Docker build step failed",
        58,
        [
            r"ERROR: failed to solve: (?P<message>.*)",
            r"did not complete successfully: exit code: (?P<code>\d+)",
        ],
        "One instruction of the Dockerfile failed (exit code {code}). The output of that `RUN` is "
        "just above this line.",
        ["Run `docker build` locally with `--progress=plain` to see the whole output."],
    ),
    _r(
        "command-not-found",
        "A command is not installed on the runner",
        70,
        [
            r"(?P<command>[\w./+-]+): command not found",
            r"^/bin/sh: \d+: (?P<command>\S+): not found",
        ],
        "The shell cannot find `{command}`.",
        [
            "Install it in an earlier step, or use a runner image that has it.",
            "If it was installed with `pip --user` or `npm -g`, check that the directory is on `PATH` "
            '(`echo "$HOME/.local/bin" >> "$GITHUB_PATH"`).',
        ],
    ),
    _r(
        "script-not-executable",
        "A script is not executable",
        66,
        [r"(?:^bash: |: line \d+: )(?P<file>[^:\s]+): Permission denied"],
        "`{file}` lost its executable bit (git keeps it, but files created on some systems do not have it).",
        [
            "`chmod +x {file}` and commit the mode change: `git update-index --chmod=+x {file}`.",
            "Or run it as `bash {file}`.",
        ],
    ),
    _r(
        "shell-syntax",
        "A shell script has an error",
        60,
        [
            r"syntax error near unexpected token",
            r"unexpected EOF while looking for matching",
            r"unbound variable",
        ],
        "The shell stopped on a syntax error or an unset variable (`set -u`).",
        [
            "Check the quoting and the line the message names; run `shellcheck` on the script.",
            "Remember that `run:` steps use `bash -e` (and `-o pipefail` when `shell: bash` is set).",
        ],
    ),
    _r(
        "shellcheck",
        "ShellCheck reported problems",
        56,
        [r"^In (?P<file>\S+) line (?P<line>\d+):", r"\bSC(?P<code>\d{4})\b"],
        "ShellCheck flagged `{file}`; the code is SC{code} (https://www.shellcheck.net/wiki/SC{code}).",
        [
            "Run `shellcheck` locally. The runner's version can be older or newer than yours and "
            "report different things; `shellcheck -S warning` limits it to the serious ones.",
        ],
    ),
    _r(
        "terraform-error",
        "Terraform reported an error",
        55,
        [
            r"^[│╷╵\s]*Error: (?P<message>.+)$",
            r"^[│╷╵\s]*on (?P<file>\S+\.tf) line (?P<line>\d+)",
        ],
        "Terraform stopped with: {message} (in `{file}`, line {line}).",
        ["Run `terraform validate` and `terraform plan` locally with the same provider versions."],
        requires=r"(?i)terraform|\.tf\b",
    ),
    _r(
        "upload-no-files",
        "An upload step found no files",
        74,
        [r"No files were found with the provided path: (?P<path>[^.]+)"],
        "The upload step looked for `{path}` and found nothing: the build did not produce it, or "
        "the path is relative to a different directory.",
        [
            "Add a step that lists the directory (`ls -R`) just before the upload.",
            "Check `working-directory` and the path (paths are relative to the workspace).",
        ],
    ),
    _r(
        "artifact-missing",
        "A download step cannot find the artifact",
        76,
        [
            r"Artifact not found for name: (?P<name>\S+)",
            r"Unable to find any artifacts for the associated workflow",
        ],
        "The artifact `{name}` does not exist in this run: the job that uploads it failed or did "
        "not run yet, or the name differs.",
        ["Add `needs:` on the uploading job, and compare the names in both steps."],
    ),
    _r(
        "env-var-missing",
        "An environment variable is not set",
        55,
        [
            r"KeyError: '(?P<name>[A-Z][A-Z0-9_]{2,})'",
            r"(?P<name>[A-Z][A-Z0-9_]{2,}) (?:is not set|must be set|is not defined)",
        ],
        "The step expects `{name}` and it is not in the environment.",
        [
            "Pass it with `env:` on the step; for a secret use `${{ secrets.NAME }}`.",
            "Secrets are not available to pull requests from forks.",
        ],
    ),
    _r(
        "python-exception",
        "A Python exception ended the program",
        35,
        [r"^(?P<exception>[A-Za-z_][\w.]*(?:Error|Exception)): (?P<message>.*)$"],
        "The program raised `{exception}`: {message}",
        ["The traceback above names the file and line where it was raised."],
        requires=r"Traceback \(most recent call last\)",
    ),
    _r(
        "exit-code",
        "A command exited with a non-zero status",
        5,
        [r"^Process completed with exit code (?P<code>\d+)\."],
        "The step's command exited with status {code}, and the log contains nothing this tool "
        "recognises. The last lines of its output are shown below.",
        ["Read the output above the exit-code line; the first error in it is usually the cause."],
    ),
)


def load_rules_file(path: str) -> list[Rule]:
    """Read extra rules from a TOML file::

    [[rule]]
    id = "vpn-down"
    title = "The VPN is down"
    priority = 90
    patterns = ['connect to vpn\\.corp failed']
    explanation = "The runner could not reach the VPN gateway."
    fixes = ["Restart the vpn service on the runner."]
    """
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    out: list[Rule] = []
    for i, item in enumerate(data.get("rule", []), start=1):
        try:
            out.append(
                Rule(
                    id=str(item["id"]),
                    title=str(item["title"]),
                    priority=int(item.get("priority", 50)),
                    patterns=tuple(re.compile(p) for p in item["patterns"]),
                    explanation=str(item.get("explanation", "")),
                    fixes=tuple(str(f) for f in item.get("fixes", [])),
                    requires=re.compile(item["requires"]) if item.get("requires") else None,
                    source=path,
                )
            )
        except KeyError as exc:
            raise ValueError(f"{path}: rule #{i} has no {exc.args[0]!r}") from exc
        except re.error as exc:
            raise ValueError(f"{path}: rule #{i}: bad pattern: {exc}") from exc
    return out


def merge(builtin: tuple[Rule, ...], custom: list[Rule]) -> list[Rule]:
    """Custom rules replace built-in ones with the same id and are added otherwise."""
    by_id = {r.id: r for r in builtin}
    for r in custom:
        by_id[r.id] = r
    return list(by_id.values())
