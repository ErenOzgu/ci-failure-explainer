"""action.yml is code too: it has to parse, its inputs have to be wired, and its script has to run."""

import pathlib
import re
import subprocess

import pytest
import yaml

from .conftest import corpus_text

ROOT = pathlib.Path(__file__).resolve().parents[1]
ACTION = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
STEP = ACTION["runs"]["steps"][0]


def test_all_yaml_files_parse():
    files = [ROOT / "action.yml", *sorted((ROOT / ".github" / "workflows").glob("*.yml"))]
    assert len(files) >= 3
    for f in files:
        assert isinstance(yaml.safe_load(f.read_text(encoding="utf-8")), dict), f.name


def test_every_input_is_used_and_every_used_input_exists():
    declared = set(ACTION["inputs"])
    used = set(re.findall(r"inputs\.([\w-]+)", yaml.safe_dump(STEP["env"])))
    assert used == declared


def test_outputs_come_from_the_step_that_the_cli_writes_them_for():
    assert STEP["id"] == "explain"
    for name, out in ACTION["outputs"].items():
        assert out["value"] == f"${{{{ steps.explain.outputs.{name} }}}}"
    assert set(ACTION["outputs"]) == {"failed-jobs", "causes", "report-path"}


def test_inputs_reach_the_script_through_the_environment_only():
    script = STEP["run"]
    assert "${{" not in script, "an input interpolated into the script text is a script injection"


def _job(i, name, conclusion):
    return {
        "id": i,
        "name": name,
        "conclusion": conclusion,
        "status": "completed",
        "html_url": f"https://x/{i}",
        "steps": [],
    }


def run_action(tmp_path, gh, **overrides):
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "GITHUB_TOKEN": "t0ken",
        "GITHUB_API_URL": gh.url,
        "CIFE_RUN_ID": "1",
        "CIFE_REPO": "o/r",
        "CIFE_INCLUDE": "",
        "CIFE_EXCLUDE": "",
        "CIFE_RULES": "",
        "CIFE_LLM": "false",
        "CIFE_STRICT": "false",
        "ACTION_PATH": str(ROOT),
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        "GITHUB_OUTPUT": str(tmp_path / "output.txt"),
        "ANTHROPIC_API_KEY": "",
    }
    env.update(overrides)
    res = subprocess.run(
        ["bash", "-e", "-c", STEP["run"]], env=env, capture_output=True, text=True, timeout=60
    )
    outputs = {}
    out = tmp_path / "output.txt"
    if out.exists():
        outputs = dict(line.split("=", 1) for line in out.read_text().splitlines())
    return res, outputs


def test_the_script_runs_end_to_end_against_the_fake_api(tmp_path, make_github):
    gh = make_github(
        jobs=[_job(1, "build", "success"), _job(2, "unit tests", "failure"), _job(3, "explain", "failure")],
        logs={"2": corpus_text("disk-full"), "3": corpus_text("dns")},
    )
    res, outputs = run_action(tmp_path, gh, CIFE_EXCLUDE="explain, ,")
    assert res.returncode == 0, res.stderr
    assert outputs["failed-jobs"] == "1" and outputs["causes"] == "disk-full"
    assert pathlib.Path(outputs["report-path"]).read_text().startswith("## Why `unit tests` failed")
    assert "The runner ran out of disk space" in (tmp_path / "summary.md").read_text()


def test_include_filter_and_several_values(tmp_path, make_github):
    gh = make_github(
        jobs=[_job(2, "unit tests", "failure"), _job(3, "lint", "failure"), _job(4, "docs", "failure")],
        logs={"2": corpus_text("dns"), "3": corpus_text("disk-full"), "4": corpus_text("exit-code")},
    )
    res, outputs = run_action(tmp_path, gh, CIFE_INCLUDE="unit,lint")
    assert res.returncode == 0, res.stderr
    assert outputs["causes"] == "dns,disk-full"


def test_a_filter_with_a_space_or_a_quote_is_not_a_shell_problem(tmp_path, make_github):
    gh = make_github(jobs=[_job(2, "unit tests (3.12)", "failure")], logs={"2": corpus_text("dns")})
    res, outputs = run_action(tmp_path, gh, CIFE_INCLUDE="unit tests (3.12)")
    assert res.returncode == 0, res.stderr
    assert outputs["failed-jobs"] == "1"
    res, outputs = run_action(tmp_path, gh, CIFE_INCLUDE="x'; touch " + str(tmp_path / "pwned") + "; echo '")
    assert res.returncode == 0 and not (tmp_path / "pwned").exists()
    assert outputs["failed-jobs"] == "0"


def test_the_script_fails_loudly_when_the_api_refuses(tmp_path, make_github):
    gh = make_github(jobs=[])
    res, _ = run_action(tmp_path, gh, GITHUB_TOKEN="wrong")
    assert res.returncode == 1
    assert "token was not accepted" in res.stderr


@pytest.mark.parametrize("name", ["action.yml"])
def test_the_manifest_has_what_the_marketplace_wants(name):
    assert ACTION["name"] and ACTION["description"] and ACTION["runs"]["using"] == "composite"
