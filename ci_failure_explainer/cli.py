"""Command line: explain a failed run (through the API) or a log file."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .engine import diagnose
from .github import ApiError, GitHub, select_failed
from .llm import AiResult, explain_with_model
from .logparse import parse
from .report import JobInfo, as_json, markdown
from .rules import BUILTIN, load_rules_file, merge


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ci-failure-explainer",
        description="Explain why a GitHub Actions job failed. Rules first; a model only if you ask.",
    )
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("explain", help="explain the failed jobs of a run, or a log file")
    src = e.add_argument_group("where the log comes from")
    src.add_argument("--log", metavar="FILE", help="a raw job log file ('-' for stdin); no API needed")
    src.add_argument("--repo", metavar="OWNER/REPO", default=os.environ.get("GITHUB_REPOSITORY"))
    src.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID"))
    src.add_argument("--token-env", default="GITHUB_TOKEN", help="environment variable holding the token")
    src.add_argument("--api-url", help="API base URL (default $GITHUB_API_URL or https://api.github.com)")
    src.add_argument(
        "--job", action="append", default=[], metavar="TEXT", help="only jobs whose name contains TEXT"
    )
    src.add_argument("--exclude-job", action="append", default=[], metavar="TEXT")
    e.add_argument("--rules", metavar="FILE", help="extra rules (TOML); same id replaces a built-in rule")
    e.add_argument("--format", choices=["md", "json"], default="md")
    e.add_argument("--output", metavar="FILE", help="also write the report to FILE")
    e.add_argument("--summary", action="store_true", help="append the report to $GITHUB_STEP_SUMMARY")
    e.add_argument("--github-output", action="store_true", help="write outputs to $GITHUB_OUTPUT")
    ai = e.add_argument_group("optional model step")
    ai.add_argument("--llm", action="store_true", help="add a model's explanation (needs ANTHROPIC_API_KEY)")
    ai.add_argument(
        "--strict-llm", action="store_true", help="drop the answer if it names things not in the log"
    )
    ai.add_argument("--model")
    ai.add_argument("--max-lines", type=int, default=60, help="log lines sent to the model (default 60)")

    sub.add_parser("rules", help="list the built-in rules")
    return p


def _job_info(job: dict) -> JobInfo:
    steps = [s["name"] for s in job.get("steps", []) if s.get("conclusion") == "failure"]
    return JobInfo(
        name=job.get("name", "job"),
        conclusion=job.get("conclusion") or "",
        url=job.get("html_url") or "",
        failed_steps=steps,
    )


def _emit_outputs(path: str | None, kv: dict[str, str]) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        for k, v in kv.items():
            fh.write(f"{k}={v}\n")


def _explain(args: argparse.Namespace) -> int:
    rules = list(BUILTIN)
    if args.rules:
        try:
            rules = merge(BUILTIN, load_rules_file(args.rules))
        except (OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if args.llm and not api_key:
        print("error: --llm needs ANTHROPIC_API_KEY in the environment", file=sys.stderr)
        return 2

    jobs: list[tuple[JobInfo, str]] = []
    if args.log:
        raw = (
            sys.stdin.read()
            if args.log == "-"
            else Path(args.log).read_text(encoding="utf-8", errors="replace")
        )
        jobs.append((JobInfo(name=Path(args.log).name if args.log != "-" else "stdin"), raw))
    else:
        token = os.environ.get(args.token_env, "")
        if not (args.repo and args.run_id and token):
            print(
                f"error: give --log FILE, or --repo, --run-id and a token in ${args.token_env}",
                file=sys.stderr,
            )
            return 2
        gh = GitHub(token, api_url=args.api_url)
        try:
            failed = select_failed(gh.jobs(args.repo, args.run_id), args.job, args.exclude_job)
            for job in failed:
                jobs.append((_job_info(job), gh.job_log(args.repo, job["id"])))
        except ApiError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    reports: list[str] = []
    docs: list[dict] = []
    causes: list[str] = []
    for info, raw in jobs:
        d = diagnose(parse(raw), rules)
        ai: AiResult | None = None
        if args.llm:
            ai = explain_with_model(
                info.name, d, api_key, model=args.model, strict=args.strict_llm, max_lines=args.max_lines
            )
        reports.append(markdown(info, d, ai.markdown() if ai else ""))
        docs.append(as_json(info, d, ai_json(ai)))
        causes.append(d.primary.rule.id if d.primary else "unknown")

    if not jobs:
        reports.append("## No failed jobs\n\nThe run has no failed job to explain.\n")

    md = "\n".join(reports)
    print(md if args.format == "md" else json.dumps(docs, indent=2, ensure_ascii=False))
    if args.output:
        Path(args.output).write_text(md, encoding="utf-8")
    if args.summary and os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(md + "\n")
    if args.github_output:
        _emit_outputs(
            os.environ.get("GITHUB_OUTPUT"),
            {"failed-jobs": str(len(jobs)), "causes": ",".join(causes), "report-path": args.output or ""},
        )
    return 0


def ai_json(ai: AiResult | None) -> dict | None:
    if ai is None:
        return None
    return {
        "model": ai.model,
        "text": "" if ai.withheld else ai.text,
        "withheld": ai.withheld,
        "error": ai.error,
        "checked": ai.verification.checked,
        "not_in_log": ai.verification.missing,
    }


def _rules() -> int:
    for r in sorted(BUILTIN, key=lambda r: (-r.priority, r.id)):
        print(f"{r.priority:>3}  {r.id:<26} {r.title}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _rules() if args.cmd == "rules" else _explain(args)
    except BrokenPipeError:  # `... | head`
        sys.stderr.close()
        return 0


if __name__ == "__main__":
    sys.exit(main())
