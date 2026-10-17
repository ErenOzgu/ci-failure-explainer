"""Every log in tests/corpus must be explained by the rule named in its file name.

The logs are synthetic: written to look like the output of each tool, inside the structure of a real
job log, with a passing step that prints words the rules look for (``Connection refused``, a rate limit)
to prove that only the failing step is read.
"""

import pathlib

import pytest

from ci_failure_explainer.engine import diagnose
from ci_failure_explainer.logparse import parse
from ci_failure_explainer.rules import BUILTIN

CORPUS = pathlib.Path(__file__).parent / "corpus"
FILES = sorted(CORPUS.glob("*.log"))
NOISE = ("retrying after Connection refused", "rate limit remaining", "optional_speedup")


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_primary_cause(path):
    expected = path.name.split(".")[0]
    d = diagnose(parse(path.read_text(encoding="utf-8")))
    assert d.primary is not None, "nothing matched"
    assert d.primary.rule.id == expected


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_steps_that_passed_are_not_read(path):
    d = diagnose(parse(path.read_text(encoding="utf-8")))
    for m in ([d.primary] if d.primary else []) + d.also:
        for hit in m.hits:
            assert not any(n in hit.text for n in NOISE), f"{m.rule.id} matched noise from a passing step"


def test_every_builtin_rule_has_a_log():
    have = {p.name.split(".")[0] for p in FILES}
    assert {r.id for r in BUILTIN} <= have


def test_rule_ids_are_unique_and_priorities_make_sense():
    ids = [r.id for r in BUILTIN]
    assert len(ids) == len(set(ids))
    by = {r.id: r.priority for r in BUILTIN}
    # a cause must outrank the symptom it produces
    assert by["python-module-missing"] > by["pytest-failures"]
    assert by["disk-full"] > by["pytest-failures"]
    assert by["pip-no-matching"] > by["docker-build"]
    assert by["connection-refused"] > by["pytest-collection"] > by["pytest-failures"]
    assert by["exit-code"] == min(by.values())
