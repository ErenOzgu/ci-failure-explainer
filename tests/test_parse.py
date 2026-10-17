from ci_failure_explainer.logparse import parse

RAW = """\
2026-10-17T12:00:00.1234567Z Current runner version: '2.329.0'
2026-10-17T12:00:01.0000000Z ##[group]Runner Image
2026-10-17T12:00:01.0000000Z Image: ubuntu-24.04
2026-10-17T12:00:01.0000000Z ##[endgroup]
2026-10-17T12:00:05.0000000Z ##[group]Run pytest tests/
2026-10-17T12:00:05.0000000Z pytest tests/
2026-10-17T12:00:05.0000000Z shell: /usr/bin/bash -e {0}
2026-10-17T12:00:05.0000000Z ##[endgroup]
2026-10-17T12:00:06.0000000Z \x1b[31mFAILED\x1b[0m tests/test_a.py::test_x - AssertionError
2026-10-17T12:00:06.0000000Z ##[error]Process completed with exit code 1.
2026-10-17T12:00:07.0000000Z ##[group]Post Run actions/checkout@v4
2026-10-17T12:00:07.0000000Z git config
2026-10-17T12:00:07.0000000Z ##[endgroup]
"""


def test_timestamps_ansi_and_commands_are_removed():
    log = parse(RAW)
    texts = [ln.text for ln in log.lines]
    assert "FAILED tests/test_a.py::test_x - AssertionError" in texts
    assert all("##[" not in t and "\x1b" not in t and not t.startswith("2026-") for t in texts)


def test_a_step_is_its_command_plus_the_output_after_the_group():
    log = parse(RAW)
    titles = [s.title for s in log.segments]
    assert titles == ["Set up job", "Run pytest tests/", "Post Run actions/checkout@v4"]
    run = log.segments[1]
    assert run.command == "pytest tests/"
    assert [ln.text for ln in run.body][0].startswith("FAILED tests/test_a.py")


def test_information_groups_do_not_start_a_step():
    log = parse(RAW)
    setup = log.segments[0]
    assert [ln.text for ln in setup.body] == [
        "Current runner version: '2.329.0'",
        "Runner Image",
        "Image: ubuntu-24.04",
    ]


def test_the_failing_segment_is_the_one_with_the_error_annotation():
    log = parse(RAW)
    failing = log.failing_segments()
    assert [s.title for s in failing] == ["Run pytest tests/"]
    assert failing[0].errors[0].text == "Process completed with exit code 1."
    assert failing[0].errors[0].no == 10


def test_line_numbers_count_the_raw_log():
    log = parse(RAW)
    by_no = {ln.no: ln.text for ln in log.lines}
    assert by_no[9].startswith("FAILED")
    assert by_no[1].startswith("Current runner")


def test_a_log_without_timestamps_and_without_groups():
    log = parse("make: *** [all] Error 2\n##[error]Process completed with exit code 2.\n")
    assert [s.title for s in log.failing_segments()] == ["Set up job"]  # one anonymous segment


def test_multiline_commands_are_kept_whole():
    raw = (
        "##[group]Run set -e\nset -e\nmake build\nmake test\nshell: /usr/bin/bash -e {0}\n"
        "env:\n  CI: true\n##[endgroup]\nboom\n##[error]Process completed with exit code 2.\n"
    )
    seg = parse(raw).failing_segments()[0]
    assert seg.command == "set -e\nmake build\nmake test"


def test_empty_log():
    log = parse("")
    assert log.lines == [] and log.segments == []
