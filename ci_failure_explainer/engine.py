"""Run the rules over a parsed log and pick the primary cause."""

from __future__ import annotations

from dataclasses import dataclass, field

from .logparse import Line, ParsedLog, Segment
from .rules import BUILTIN, Rule


class _Safe(dict):
    def __missing__(self, key: str) -> str:
        return "?"


def fill(template: str, fields: dict[str, str]) -> str:
    """Fill ``{name}`` placeholders; a name the pattern did not capture becomes ``?``."""
    try:
        return template.format_map(_Safe(fields))
    except (ValueError, IndexError):  # a stray brace in a user rule
        return template


@dataclass
class Match:
    rule: Rule
    hits: list[Line]  # the lines that matched, in log order
    fields: dict[str, str]  # named groups of the first hit

    @property
    def explanation(self) -> str:
        return fill(self.rule.explanation, self.fields)

    @property
    def fixes(self) -> list[str]:
        return [fill(f, self.fields) for f in self.rule.fixes]


@dataclass
class Diagnosis:
    """What is known about one failed job."""

    step_title: str  # "Run pytest tests/"; "(no error annotation found)" if unknown
    command: str
    primary: Match | None
    also: list[Match] = field(default_factory=list)
    scope: list[Line] = field(default_factory=list)  # the lines the rules ran over
    first_error: Line | None = None
    fallback: bool = False  # no ##[error] line: the tail of the log was used


def scope_of(log: ParsedLog, tail: int = 80) -> tuple[list[Segment], list[Line], bool]:
    segs = log.failing_segments()
    if segs:
        lines = [ln for s in segs for ln in s.body]
        return segs, lines, False
    return [], log.lines[-tail:], True


def match_rules(lines: list[Line], rules: list[Rule]) -> list[Match]:
    text = "\n".join(ln.text for ln in lines)
    out: list[Match] = []
    for rule in rules:
        if rule.requires is not None and not rule.requires.search(text):
            continue
        hits: list[Line] = []
        fields: dict[str, str] = {}
        for ln in lines:
            for pat in rule.patterns:
                m = pat.search(ln.text)
                if m:
                    hits.append(ln)
                    if not fields:
                        fields = {k: v for k, v in m.groupdict().items() if v is not None}
                    break
        if hits:
            out.append(Match(rule=rule, hits=hits, fields=fields))
    # highest priority first; among equals the one that appears earliest in the log
    out.sort(key=lambda m: (-m.rule.priority, m.hits[0].no))
    return out


def diagnose(log: ParsedLog, rules: list[Rule] | None = None, also: int = 3) -> Diagnosis:
    rules = list(BUILTIN) if rules is None else rules
    segs, lines, fallback = scope_of(log)
    matches = match_rules(lines, rules)
    first_error = next((ln for ln in lines if ln.kind == "error"), None)
    seg = segs[0] if segs else None
    return Diagnosis(
        step_title=(seg.title if seg else "(no error annotation found)"),
        command=(seg.command if seg else ""),
        primary=matches[0] if matches else None,
        also=matches[1 : 1 + also],
        scope=lines,
        first_error=first_error,
        fallback=fallback,
    )


def excerpt(d: Diagnosis, max_lines: int = 60, max_chars: int = 8000) -> list[Line]:
    """The lines worth showing or sending to a model.

    The last ``max_lines`` lines before (and including) the first error, plus the lines the primary
    rule matched when they lie further up.
    """
    lines = d.scope
    if d.first_error is not None:
        cut = max(i for i, ln in enumerate(lines) if ln is d.first_error) + 1
        window = lines[max(0, cut - max_lines) : cut]
    else:
        window = lines[-max_lines:]
    keep = {ln.no: ln for ln in window}
    if d.primary:
        for ln in d.primary.hits[:3]:
            keep.setdefault(ln.no, ln)
    out = [keep[n] for n in sorted(keep)]
    # trim from the top until it fits
    while out and sum(len(ln.text) + 1 for ln in out) > max_chars:
        out.pop(0)
    return out
