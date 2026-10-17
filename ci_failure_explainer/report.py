"""Render a diagnosis as markdown or JSON."""

from __future__ import annotations

from dataclasses import dataclass, field

from .engine import Diagnosis, excerpt
from .logparse import Line


@dataclass
class JobInfo:
    name: str
    conclusion: str = ""
    url: str = ""
    failed_steps: list[str] = field(default_factory=list)
    duration_s: int | None = None


def _block(lines: list[Line], width: int = 220) -> str:
    out = []
    for ln in lines:
        text = ln.text if len(ln.text) <= width else ln.text[: width - 1] + "…"
        out.append(f"{ln.no:>5} | {text}")
    return "\n".join(out)


def _evidence(d: Diagnosis, context: int = 1, limit: int = 3) -> list[Line]:
    if not d.primary:
        return []
    idx = {ln.no: i for i, ln in enumerate(d.scope)}
    keep: dict[int, Line] = {}
    for hit in d.primary.hits[:limit]:
        i = idx.get(hit.no)
        if i is None:
            continue
        for ln in d.scope[max(0, i - context) : i + context + 1]:
            keep[ln.no] = ln
    return [keep[n] for n in sorted(keep)]


def markdown(job: JobInfo, d: Diagnosis, ai_section: str = "", heading_level: int = 2) -> str:
    h = "#" * heading_level
    out: list[str] = []
    title = f"{h} Why `{job.name}` failed"
    out.append(title)
    meta = []
    if job.conclusion:
        meta.append(f"conclusion: **{job.conclusion}**")
    if job.failed_steps:
        meta.append("failed step: " + ", ".join(f"`{s}`" for s in job.failed_steps))
    elif d.step_title and not d.fallback and not d.command:
        meta.append(f"failing step: `{d.step_title.removeprefix('Run ')}`")
    if d.command:
        first = d.command.splitlines()[0]
        meta.append(f"command: `{first[:100]}`")
    if job.url:
        meta.append(f"[job log]({job.url})")
    if meta:
        out.append(" · ".join(meta))
    out.append("")
    if d.primary:
        p = d.primary
        out.append(f"{h}# Most likely cause: {p.rule.title}")
        out.append("")
        out.append(p.explanation)
        if p.fixes:
            out.append("")
            out.append("What to try:")
            out.append("")
            for i, fix in enumerate(p.fixes, 1):
                out.append(f"{i}. {fix}")
        ev = _evidence(d)
        if ev:
            out.append("")
            out.append(f"Evidence (log lines {ev[0].no} to {ev[-1].no}):")
            out.append("")
            out.append("```text")
            out.append(_block(ev))
            out.append("```")
        if d.also:
            out.append("")
            out.append("Also in the log: " + "; ".join(f"{m.rule.title} (`{m.rule.id}`)" for m in d.also))
    else:
        out.append(f"{h}# No known cause")
        out.append("")
        if d.fallback:
            out.append(
                "The log has no error annotation (the job may have been cancelled or lost), and no rule "
                "recognised its last lines."
            )
        else:
            out.append("No rule recognised the output of the failing step. The end of it:")
        tail = excerpt(d, max_lines=25)
        if tail:
            out.append("")
            out.append("```text")
            out.append(_block(tail))
            out.append("```")
    if ai_section:
        out.append("")
        out.append(ai_section.rstrip())
    out.append("")
    return "\n".join(out)


def as_json(job: JobInfo, d: Diagnosis, ai: dict | None = None) -> dict:
    def m(match):
        return {
            "rule": match.rule.id,
            "title": match.rule.title,
            "priority": match.rule.priority,
            "explanation": match.explanation,
            "fixes": match.fixes,
            "lines": [ln.no for ln in match.hits[:10]],
        }

    return {
        "job": job.name,
        "conclusion": job.conclusion,
        "url": job.url,
        "failed_steps": job.failed_steps,
        "step": d.step_title,
        "command": d.command,
        "fallback": d.fallback,
        "primary": m(d.primary) if d.primary else None,
        "also": [m(x) for x in d.also],
        "ai": ai,
    }
