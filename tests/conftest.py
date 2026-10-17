from __future__ import annotations

import pathlib

import pytest

from .fakes import FakeAnthropic, FakeGitHub

CORPUS = pathlib.Path(__file__).parent / "corpus"


def corpus_text(name: str) -> str:
    return (CORPUS / f"{name}.log").read_text(encoding="utf-8")


@pytest.fixture
def anthropic():
    srv = FakeAnthropic()
    yield srv
    srv.close()


@pytest.fixture
def make_github():
    servers: list[FakeGitHub] = []

    def make(token="t0ken", jobs=None, logs=None, **kw):
        srv = FakeGitHub(token, jobs or [], logs or {}, **kw)
        servers.append(srv)
        return srv

    yield make
    for s in servers:
        s.close()
