import re

import pytest

from ci_failure_explainer.engine import diagnose, excerpt, fill
from ci_failure_explainer.logparse import parse
from ci_failure_explainer.rules import BUILTIN, Rule, load_rules_file, merge

from .conftest import corpus_text


def test_highest_priority_wins_not_the_first_line():
    d = diagnose(parse(corpus_text("python-module-missing")))
    assert d.primary.rule.id == "python-module-missing"
    assert {m.rule.id for m in d.also} >= {"pytest-collection"}
    assert d.primary.fields == {"module": "httpx"}
    assert "`httpx`" in d.primary.explanation


def test_placeholders_that_were_not_captured_become_question_marks():
    assert fill("a {x} b {y}", {"x": "1"}) == "a 1 b ?"
    assert fill("broken {", {}) == "broken {"  # a stray brace in a user rule does not crash


def test_requires_keeps_generic_patterns_in_their_own_tool():
    text = "##[error]Process completed with exit code 1.\n"
    # a node-style "Error: ..." line must not be called a Terraform error
    d = diagnose(parse("Error: something broke in the app\n" + text))
    assert d.primary.rule.id == "exit-code"
    d = diagnose(parse("terraform plan\nError: Unsupported argument\n" + text))
    assert d.primary.rule.id == "terraform-error"


def test_no_error_annotation_falls_back_to_the_tail():
    raw = "".join(f"line {i}\n" for i in range(200)) + "The runner has received a shutdown signal.\n"
    d = diagnose(parse(raw))
    assert d.fallback is True
    assert d.primary.rule.id == "runner-lost"
    assert len(d.scope) == 80


def test_unknown_failure_has_no_primary_but_an_excerpt():
    raw = "##[group]Run ./x\n./x\n##[endgroup]\nweird output\n##[error]Something nobody wrote a rule for\n"
    d = diagnose(parse(raw))
    assert d.primary is None
    assert [ln.text for ln in excerpt(d)] == ["weird output", "Something nobody wrote a rule for"]


def test_excerpt_is_bounded_and_ends_at_the_first_error():
    body = "".join(f"out {i}\n" for i in range(500))
    raw = f"##[group]Run ./x\n./x\n##[endgroup]\n{body}##[error]Process completed with exit code 1.\nafter\n"
    d = diagnose(parse(raw))
    ex = excerpt(d, max_lines=10)
    assert len(ex) == 10
    assert ex[-1].text == "Process completed with exit code 1."
    assert all(ln.text != "after" for ln in ex)
    small = excerpt(d, max_lines=500, max_chars=100)
    assert sum(len(ln.text) + 1 for ln in small) <= 100


def test_the_excerpt_keeps_the_lines_that_proved_the_cause():
    middle = "".join(f"noise {i}\n" for i in range(300))
    raw = (
        "##[group]Run ./x\n./x\n##[endgroup]\nFATAL: No space left on device\n"
        + middle
        + "##[error]Process completed with exit code 1.\n"
    )
    d = diagnose(parse(raw))
    texts = [ln.text for ln in excerpt(d, max_lines=20)]
    assert "FATAL: No space left on device" in texts


def test_custom_rules_from_toml(tmp_path):
    f = tmp_path / "rules.toml"
    f.write_text(
        '[[rule]]\nid = "vpn-down"\ntitle = "The VPN is down"\npriority = 99\n'
        "patterns = ['connect to vpn\\.corp failed: (?P<why>.+)']\n"
        'explanation = "The runner cannot reach the gateway ({why})."\nfixes = ["Restart the vpn service."]\n'
        '\n[[rule]]\nid = "disk-full"\ntitle = "Our own disk text"\npriority = 85\npatterns = ["ENOSPC"]\n'
    )
    rules = merge(BUILTIN, load_rules_file(str(f)))
    d = diagnose(
        parse("connect to vpn.corp failed: timeout\n##[error]Process completed with exit code 1.\n"), rules
    )
    assert d.primary.rule.id == "vpn-down"
    assert d.primary.explanation == "The runner cannot reach the gateway (timeout)."
    assert [r.id for r in rules].count("disk-full") == 1
    assert next(r for r in rules if r.id == "disk-full").title == "Our own disk text"


def test_bad_rule_files_say_what_is_wrong(tmp_path):
    f = tmp_path / "r.toml"
    f.write_text('[[rule]]\nid = "x"\npatterns = ["a"]\n')
    with pytest.raises(ValueError, match="rule #1 has no 'title'"):
        load_rules_file(str(f))
    f.write_text('[[rule]]\nid = "x"\ntitle = "t"\npatterns = ["("]\n')
    with pytest.raises(ValueError, match="bad pattern"):
        load_rules_file(str(f))


def test_every_builtin_pattern_is_anchored_or_specific_enough_to_not_match_empty_text():
    for r in BUILTIN:
        for p in r.patterns:
            assert not p.search(""), f"{r.id}: {p.pattern!r} matches an empty line"


def test_explanations_only_use_names_their_patterns_capture():
    for r in BUILTIN:
        captured = set()
        for p in r.patterns:
            captured |= set(p.groupindex)
        used = set(re.findall(r"\{(\w+)\}", r.explanation + " ".join(r.fixes)))
        assert used <= captured, f"{r.id} uses {used - captured}, which no pattern captures"


def test_rule_dataclass_is_hashable_for_sets():
    assert isinstance(BUILTIN[0], Rule)
    assert len({r.id for r in BUILTIN}) == len(BUILTIN)
