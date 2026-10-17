"""Turn a raw GitHub Actions job log into lines and segments.

A raw job log has a timestamp in front of every line, ANSI colours, and workflow commands such as
``##[group]``, ``##[endgroup]`` and ``##[error]``. A step that runs a command looks like this::

    ##[group]Run pytest tests/
    pytest tests/
    shell: /usr/bin/bash -e {0}
    ##[endgroup]
    ...the output of the command...
    ##[error]Process completed with exit code 1.

So the command is in the group, and its output follows the group, up to the next step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_TS = re.compile(r"^﻿?\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z ?")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_CMD = re.compile(r"^##\[(group|endgroup|error|warning|notice|debug|command)\](.*)$")


@dataclass
class Line:
    no: int  # 1-based, in the cleaned log
    text: str  # timestamp, ANSI and the ##[...] prefix removed
    kind: str = "out"  # out | error | warning | notice | group | endgroup


@dataclass
class Segment:
    """One step of the job: the command in its ``##[group]`` and the output after it."""

    title: str  # "Run pytest tests/", "Post Run actions/checkout@v4", "Set up job"
    header: list[Line] = field(default_factory=list)  # lines inside the group (command, shell, env)
    body: list[Line] = field(default_factory=list)  # output after ##[endgroup]

    @property
    def lines(self) -> list[Line]:
        return self.header + self.body

    @property
    def errors(self) -> list[Line]:
        return [ln for ln in self.body if ln.kind == "error"]

    @property
    def command(self) -> str:
        """The command the step ran, from the lines in the group; empty for set-up steps."""
        out: list[str] = []
        for ln in self.header[1:] if self.header else []:
            if re.match(r"^(shell|env|with|working-directory):", ln.text) or ln.text.startswith("  "):
                break
            out.append(ln.text)
        return "\n".join(out).strip()


@dataclass
class ParsedLog:
    lines: list[Line]
    segments: list[Segment]

    @property
    def errors(self) -> list[Line]:
        return [ln for ln in self.lines if ln.kind == "error"]

    def failing_segments(self) -> list[Segment]:
        return [s for s in self.segments if s.errors]


def clean(text: str) -> str:
    text = _ANSI.sub("", text)
    return text.rstrip("\r")


def parse(raw: str) -> ParsedLog:
    lines: list[Line] = []
    segments: list[Segment] = []
    current = Segment(title="Set up job")
    in_group = False
    group_is_step = False
    for no, raw_line in enumerate(raw.splitlines(), start=1):
        text = clean(_TS.sub("", raw_line, count=1))
        m = _CMD.match(text)
        kind = "out"
        if m:
            cmd, rest = m.group(1), m.group(2)
            if cmd == "group":
                kind = "group"
                title = rest.strip()
                if title.startswith(("Run ", "Post Run ")):
                    # a new step starts here
                    if current.lines or segments:
                        segments.append(current)
                    current = Segment(title=title)
                    in_group, group_is_step = True, True
                    current.header.append(Line(no, title, kind))
                    continue
                # an information group inside a step ("Runner Image", "Fetching the repository")
                in_group, group_is_step = True, False
                current.body.append(Line(no, title, kind))
                continue
            if cmd == "endgroup":
                in_group = False
                continue
            if cmd in ("error", "warning", "notice"):
                kind = cmd
                text = rest.strip()
            else:
                continue  # ##[debug], ##[command]
        line = Line(no, text, kind)
        lines.append(line)
        if in_group and group_is_step:
            current.header.append(line)
        else:
            current.body.append(line)
    if current.lines:
        segments.append(current)
    # header lines of "Run ..." groups also belong to the flat list, in order, so numbers stay stable
    flat = sorted({id(ln): ln for s in segments for ln in s.lines}.values(), key=lambda ln: ln.no)
    return ParsedLog(lines=flat, segments=segments)
