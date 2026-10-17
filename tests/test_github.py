import pytest

from ci_failure_explainer.github import ApiError, GitHub, select_failed

from .conftest import corpus_text


def job(i, name, conclusion, steps=None):
    return {
        "id": i,
        "name": name,
        "conclusion": conclusion,
        "status": "completed",
        "html_url": f"https://github.example/o/r/actions/runs/1/job/{i}",
        "steps": steps or [],
    }


def test_the_token_goes_to_the_api_and_never_to_the_log_host(make_github):
    gh = make_github(jobs=[job(11, "test", "failure")], logs={"11": "the log\n"})
    client = GitHub("t0ken", api_url=gh.url)
    assert client.job_log("o/r", 11) == "the log\n"
    api = [r for r in gh.requests if r["path"].endswith("/logs")]
    assert api and api[0]["headers"]["authorization"] == "Bearer t0ken"
    assert gh.blob.requests, "the log host was never contacted"
    for r in gh.blob.requests:
        assert "authorization" not in r["headers"], "the token leaked to the redirect target"


def test_jobs_are_paged(make_github):
    gh = make_github(jobs=[job(i, f"job {i}", "success") for i in range(1, 251)])
    jobs = GitHub("t0ken", api_url=gh.url).jobs("o/r", 1)
    assert len(jobs) == 250
    pages = [r["path"] for r in gh.requests if "/jobs?" in r["path"]]
    assert len(pages) == 3 and "filter=latest" in pages[0]


def test_wrong_token_is_explained(make_github):
    gh = make_github(jobs=[])
    with pytest.raises(ApiError, match="401.*token was not accepted"):
        GitHub("wrong", api_url=gh.url).jobs("o/r", 1)


def test_expired_logs_are_explained(make_github):
    gh = make_github(jobs=[], logs={})
    with pytest.raises(ApiError, match="404.*expired"):
        GitHub("t0ken", api_url=gh.url).job_log("o/r", 99)


def test_a_server_error_is_retried(make_github):
    gh = make_github(jobs=[job(1, "a", "failure")])
    gh.fail_next = [502, 503]
    waits = []
    client = GitHub("t0ken", api_url=gh.url, sleep=waits.append)
    assert len(client.jobs("o/r", 1)) == 1
    assert waits == [1, 2]


def test_a_server_that_keeps_failing_gives_up_with_the_status(make_github):
    gh = make_github(jobs=[])
    gh.fail_next = [500, 500, 500]
    with pytest.raises(ApiError, match="500"):
        GitHub("t0ken", api_url=gh.url, sleep=lambda s: None).jobs("o/r", 1)


def test_a_huge_log_keeps_its_tail(make_github):
    big = "".join(f"line {i:06d}\n" for i in range(50_000)) + "THE END\n"
    gh = make_github(jobs=[], logs={"7": big})
    text = GitHub("t0ken", api_url=gh.url, max_log_bytes=100_000).job_log("o/r", 7)
    assert len(text) <= 100_000 and text.endswith("THE END\n")


def test_select_failed_prefers_failures_over_cancellations():
    jobs = [
        job(1, "build", "success"),
        job(2, "test (3.11)", "failure"),
        job(3, "test (3.12)", "cancelled"),
        job(4, "lint", "timed_out"),
        job(5, "docs", "skipped"),
    ]
    assert [j["id"] for j in select_failed(jobs)] == [2, 4]


def test_select_failed_falls_back_to_cancelled_when_nothing_failed():
    jobs = [job(1, "build", "success"), job(3, "deploy", "cancelled")]
    assert [j["id"] for j in select_failed(jobs)] == [3]


def test_select_failed_filters_by_name():
    jobs = [job(2, "test (3.11)", "failure"), job(4, "lint", "failure"), job(6, "explain", "failure")]
    assert [j["id"] for j in select_failed(jobs, include=["TEST", "lint"])] == [2, 4]
    assert [j["id"] for j in select_failed(jobs, exclude=["explain"])] == [2, 4]


def test_the_run_in_progress_is_not_a_failed_job():
    running = {"id": 9, "name": "explain", "status": "in_progress", "conclusion": None}
    assert select_failed([running]) == []


def test_corpus_text_is_what_the_client_returns(make_github):
    text = corpus_text("pytest-failures")
    gh = make_github(logs={"5": text})
    assert GitHub("t0ken", api_url=gh.url).job_log("o/r", 5) == text
