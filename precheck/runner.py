"""Execution engine: runs a selected set of checks and renders a canonical,
deterministic report so full and incremental outputs can be diffed."""

from dataclasses import dataclass, field

from .checks import PASS, run_check


@dataclass
class RunReport:
    mode: str                       # "full" | "incremental" | "none"
    changed: list = field(default_factory=list)
    affected: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    reasons: list = field(default_factory=list)
    fallback_reasons: list = field(default_factory=list)
    results: list = field(default_factory=list)  # list[CheckResult]
    notes: list = field(default_factory=list)

    @property
    def verdict(self):
        if any(r.status != PASS for r in self.results):
            return "FAIL"
        return "PASS"

    @property
    def total_check_ms(self):
        return sum(r.duration_ms for r in self.results)


def execute(root, config, check_names):
    entries = {entry["name"]: entry for entry in config["checks"]}
    return [run_check(root, entries[name]) for name in check_names]


def render(report):
    lines = [f"mode: {report.mode}"]
    for note in report.notes:
        lines.append(f"note: {note}")
    if report.changed:
        lines.append("changed: " + ", ".join(report.changed))
    for reason in report.reasons:
        lines.append(f"infer: {reason}")
    for reason in report.fallback_reasons:
        lines.append(f"fallback: {reason}")
    if report.affected:
        lines.append("affected: " + ", ".join(report.affected))
    if report.skipped:
        lines.append("skipped: " + ", ".join(report.skipped) + " (inputs unchanged)")
    lines.append(f"checks-run: {len(report.results)}")
    for result in report.results:
        lines.append(f"[{result.status}] {result.name} ({result.duration_ms:.1f}ms)")
        for detail in result.details:
            lines.append(f"       {detail}")
    lines.append(f"total-check-ms: {report.total_check_ms:.1f}")
    lines.append(
        f"verdict: {report.verdict} "
        f"({sum(1 for r in report.results if r.status == PASS)}/{len(report.results)} passed)"
    )
    return "\n".join(lines)
