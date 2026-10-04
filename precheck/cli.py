"""Command line interface.

  python3 -m precheck.cli run     --root R [--full | --changed F [F ...]]
  python3 -m precheck.cli compare --root R --changed F [F ...]
  python3 -m precheck.cli map     --root R [--files F [F ...]]

Exit codes: run -> 0 pass / 1 fail; compare -> 0 consistent / 1 inconsistent.
"""

import argparse
import sys

from .checks import PASS
from .config import load_config
from .impact import infer, normalize
from .runner import RunReport, execute, render


def _all_names(config):
    return [entry["name"] for entry in config["checks"]]


def build_report(root, changed=None, force_full=False):
    """Build and execute a RunReport for `changed` files (or a full run)."""
    config, config_error = load_config(root)
    notes = []
    if config_error:
        notes.append(f"{config_error}; impact inference is disabled, forcing full run")

    if changed is None or force_full:
        report = RunReport(mode="full", notes=notes)
        if force_full and changed is not None:
            report.changed = [normalize(c) for c in changed]
        report.affected = _all_names(config)
        report.results = execute(root, config, report.affected)
        return report

    changed = [normalize(c) for c in changed]
    if config_error:
        impact = infer([""], config)  # CONFIG_INVALID -> full
    else:
        impact = infer(changed, config)

    report = RunReport(
        mode="incremental",
        changed=changed,
        reasons=impact.reasons,
        fallback_reasons=impact.fallback_reasons,
        notes=notes,
    )
    if impact.mode == "none":
        report.mode = "none"
        return report
    if impact.mode == "full":
        report.mode = "incremental->full"
        report.affected = _all_names(config)
    else:
        report.affected = impact.affected
        report.skipped = [n for n in _all_names(config) if n not in impact.affected]
    report.results = execute(root, config, report.affected)
    return report


def cmd_run(args):
    changed = None if args.full else (args.changed or [])
    report = build_report(args.root, changed=changed, force_full=args.full)
    print(render(report))
    return 0 if report.verdict == PASS else 1


def cmd_compare(args):
    """Run incremental and full on the same change set and diff the results."""
    changed = [normalize(c) for c in args.changed]
    incr = build_report(args.root, changed=changed)
    full = build_report(args.root, changed=changed, force_full=True)

    full_by_name = {r.name: r for r in full.results}
    lines = [f"compare: changed=[{', '.join(changed)}]"]
    lines.append(f"mode: {incr.mode}")
    for note in incr.notes:
        lines.append(f"note: {note}")
    for reason in incr.fallback_reasons:
        lines.append(f"fallback: {reason}")
    lines.append(f"affected checks: {', '.join(incr.affected) or '(none)'}")
    consistent = True
    for result in incr.results:
        other = full_by_name[result.name]
        same = result.status == other.status and result.details == other.details
        consistent = consistent and same
        tag = "identical" if same else "DIFFERS"
        lines.append(f"  {result.name}: {tag} ({result.status})")
        if not same:
            lines.append(f"    incremental: {result.status} {result.details}")
            lines.append(f"    full:        {other.status} {other.details}")
    for name in incr.skipped:
        lines.append(f"  {name}: skipped by incremental, full-run status {full_by_name[name].status}")
    # Verdict over the affected set must match between the two modes; the
    # overall verdict matches whenever the skipped checks were already green.
    full_affected_verdict = (
        PASS if all(full_by_name[n].status == PASS for n in incr.affected) else "FAIL"
    )
    lines.append(f"verdict(affected): incremental={incr.verdict} full={full_affected_verdict}")
    lines.append(f"verdict(overall):  incremental={incr.verdict} full={full.verdict}")
    verdict_ok = incr.verdict == full_affected_verdict
    consistent = consistent and verdict_ok
    lines.append(f"result: {'CONSISTENT' if consistent else 'INCONSISTENT'}")
    print("\n".join(lines))
    return 0 if consistent else 1


def cmd_map(args):
    config, config_error = load_config(args.root)
    if config_error:
        print(f"warning: {config_error}")
    print("dependency map (check <- file patterns):")
    for entry in config["checks"]:
        print(f"  {entry['name']}")
        for pattern in entry.get("patterns", []):
            print(f"    <- {pattern}")
    print("ignored (no checks): " + ", ".join(config.get("ignore_patterns", [])))
    print("full-run triggers:   " + ", ".join(config.get("config_files", [])))
    if args.files:
        impact = infer(args.files, config)
        print("\ninference for given files:")
        for reason in impact.reasons + impact.fallback_reasons:
            print(f"  {reason}")
        print(f"  => mode={impact.mode} affected=[{', '.join(impact.affected)}]")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="precheck")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run checks (full or incremental)")
    p_run.add_argument("--root", default=".")
    p_run.add_argument("--full", action="store_true", help="ignore inference, run everything")
    p_run.add_argument("--changed", nargs="*", default=None, metavar="FILE")
    p_run.set_defaults(func=cmd_run)

    p_cmp = sub.add_parser("compare", help="prove incremental == full for a change set")
    p_cmp.add_argument("--root", default=".")
    p_cmp.add_argument("--changed", nargs="+", required=True, metavar="FILE")
    p_cmp.set_defaults(func=cmd_compare)

    p_map = sub.add_parser("map", help="print the dependency map / infer impact")
    p_map.add_argument("--root", default=".")
    p_map.add_argument("--files", nargs="*", default=None, metavar="FILE")
    p_map.set_defaults(func=cmd_map)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
