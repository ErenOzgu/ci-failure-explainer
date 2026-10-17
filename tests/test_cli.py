import json

from ci_failure_explainer.cli import main

from .conftest import CORPUS, corpus_text


def job(i, name, conclusion, steps=None):
    return {
        "id": i,
        "name": name,
        "conclusion": conclusion,
        "status": "completed",
        "html_url": f"https://github.example/o/r/actions/runs/1/job/{i}",
        "steps": steps or [],
    }


def test_explain_a_log_file(capsys):
    assert main(["explain", "--log", str(CORPUS / "pytest-failures.log")]) == 0
    out = capsys.readouterr().out
    assert "Most likely cause: Tests failed" in out
    assert "test_total_discount" in out
    assert "| FAILED tests/test_orders.py::test_total_discount" in out  # evidence with a line number
    assert "pytest tests/test_orders.py::test_total_discount" in out  # the fix names the test


def test_json_output(capsys):
    assert main(["explain", "--log", str(CORPUS / "disk-full.log"), "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["primary"]["rule"] == "disk-full"
    assert data[0]["primary"]["fixes"] and data[0]["ai"] is None


def test_unknown_failures_say_so_and_show_the_end_of_the_log(capsys, tmp_path):
    f = tmp_path / "x.log"
    f.write_text("##[group]Run ./x\n./x\n##[endgroup]\nweird output\n##[error]boom\n")
    assert main(["explain", "--log", str(f)]) == 0
    out = capsys.readouterr().out
    assert "No known cause" in out and "weird output" in out


def test_a_run_through_the_api(capsys, monkeypatch, make_github):
    gh = make_github(
        jobs=[
            job(1, "lint", "success"),
            job(2, "test (3.12)", "failure", [{"name": "Run tests", "conclusion": "failure"}]),
            job(3, "build", "failure"),
            job(4, "docs", "skipped"),
        ],
        logs={"2": corpus_text("python-module-missing"), "3": corpus_text("docker-build")},
    )
    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")
    assert main(["explain", "--repo", "o/r", "--run-id", "1", "--api-url", gh.url]) == 0
    out = capsys.readouterr().out
    assert "Why `test (3.12)` failed" in out and "failed step: `Run tests`" in out
    assert "Python cannot import a module that is not installed" in out
    assert "Why `build` failed" in out and "A Docker build step failed" in out
    assert "lint" not in out.replace("lint step", "")  # the job that passed is not in the report
    assert "[job log](https://github.example/o/r/actions/runs/1/job/2)" in out


def test_the_api_url_comes_from_the_environment_in_actions(capsys, monkeypatch, make_github):
    gh = make_github(jobs=[job(2, "t", "failure")], logs={"2": corpus_text("dns")})
    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")
    monkeypatch.setenv("GITHUB_API_URL", gh.url)
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_RUN_ID", "1")
    assert main(["explain"]) == 0
    assert "A host name could not be resolved" in capsys.readouterr().out


def test_job_filters(capsys, monkeypatch, make_github):
    gh = make_github(
        jobs=[job(2, "unit", "failure"), job(3, "explain", "failure")],
        logs={"2": corpus_text("dns"), "3": corpus_text("dns")},
    )
    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")
    assert (
        main(["explain", "--repo", "o/r", "--run-id", "1", "--api-url", gh.url, "--exclude-job", "explain"])
        == 0
    )
    out = capsys.readouterr().out
    assert "Why `unit` failed" in out and "Why `explain` failed" not in out


def test_a_run_without_failures(capsys, monkeypatch, make_github):
    gh = make_github(jobs=[job(1, "build", "success")])
    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")
    assert main(["explain", "--repo", "o/r", "--run-id", "1", "--api-url", gh.url]) == 0
    assert "No failed jobs" in capsys.readouterr().out


def test_api_errors_are_a_message_and_exit_1(capsys, monkeypatch, make_github):
    gh = make_github(jobs=[])
    monkeypatch.setenv("GITHUB_TOKEN", "wrong")
    assert main(["explain", "--repo", "o/r", "--run-id", "1", "--api-url", gh.url]) == 1
    assert "token was not accepted" in capsys.readouterr().err


def test_usage_errors(capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    assert main(["explain"]) == 2
    assert "give --log FILE, or --repo" in capsys.readouterr().err
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert main(["explain", "--log", str(CORPUS / "dns.log"), "--llm"]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err
    assert main(["explain", "--log", str(CORPUS / "dns.log"), "--rules", "/nonexistent.toml"]) == 2


def test_summary_and_outputs_for_actions(capsys, monkeypatch, tmp_path):
    summary, outputs, report = tmp_path / "summary.md", tmp_path / "out.txt", tmp_path / "report.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    args = [
        "explain",
        "--log",
        str(CORPUS / "disk-full.log"),
        "--summary",
        "--github-output",
        "--output",
        str(report),
    ]
    assert main(args) == 0
    capsys.readouterr()
    assert "The runner ran out of disk space" in summary.read_text()
    assert summary.read_text() == report.read_text() + "\n"
    lines = dict(line.split("=", 1) for line in outputs.read_text().splitlines())
    assert lines == {"failed-jobs": "1", "causes": "disk-full", "report-path": str(report)}


def test_with_the_model_step(capsys, monkeypatch, anthropic):
    anthropic.reply = "The disk filled up while pip installed torch."
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", anthropic.url)
    assert main(["explain", "--log", str(CORPUS / "disk-full.log"), "--llm"]) == 0
    out = capsys.readouterr().out
    assert "The runner ran out of disk space" in out  # the rule-based answer is still there
    assert "AI explanation" in out and "The disk filled up while pip installed torch." in out


def test_a_failing_model_does_not_fail_the_command(capsys, monkeypatch, anthropic):
    anthropic.status = 500
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", anthropic.url)
    assert main(["explain", "--log", str(CORPUS / "disk-full.log"), "--llm", "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["primary"]["rule"] == "disk-full"
    assert "500" in data[0]["ai"]["error"]


def test_rules_listing(capsys):
    assert main(["rules"]) == 0
    out = capsys.readouterr().out
    assert "disk-full" in out and "exit-code" in out
