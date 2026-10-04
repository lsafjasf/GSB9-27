"""Impact inference: changed files -> affected checks.

The dependency map in `precheck.json` declares, per check, the set of file
patterns it depends on. A changed file "hits" every check whose patterns match
it. Files explicitly ignored (docs, VCS metadata) hit nothing. Files that are
part of the trusted tool configuration, or whose impact cannot be inferred,
force a full run.
"""

from dataclasses import dataclass, field

from .globmatch import any_match


@dataclass
class Impact:
    mode: str  # "none" | "subset" | "full"
    affected: list = field(default_factory=list)  # check names, config order
    reasons: list = field(default_factory=list)   # per-file decisions
    fallback_reasons: list = field(default_factory=list)

    @property
    def needs_full_run(self):
        return self.mode == "full"


def normalize(path):
    path = path.replace("\\", "/").strip()
    while path.startswith("./"):
        path = path[2:]
    return path.rstrip("/") or path


def infer(changed_files, config):
    """Compute the Impact of a change set.

    Fallback (mode == "full") triggers when ANY changed file:
      1. is a trusted config / dependency-map file (CONFIG_CHANGED), or
      2. is malformed/absent config discovered elsewhere (CONFIG_INVALID,
         supplied by the caller via changed path ""), or
      3. matches no check pattern and no ignore pattern (UNKNOWN_IMPACT).
    """
    checks = config["checks"]
    ignore_patterns = config.get("ignore_patterns", [])
    config_patterns = config.get("config_files", [])
    all_names = [entry["name"] for entry in checks]

    affected = set()
    reasons = []
    fallback_reasons = []

    for raw in changed_files:
        path = normalize(raw)
        if not path:
            fallback_reasons.append("CONFIG_INVALID: dependency map cannot be loaded")
            continue
        if any_match(path, config_patterns):
            fallback_reasons.append(
                f"CONFIG_CHANGED: {path} is part of the tool configuration / dependency map"
            )
            continue
        if any_match(path, ignore_patterns):
            reasons.append(f"{path}: ignored (no check depends on it)")
            continue
        hits = [
            entry["name"]
            for entry in checks
            if any_match(path, entry.get("patterns", []))
        ]
        if hits:
            affected.update(hits)
            reasons.append(f"{path} -> {', '.join(hits)}")
        else:
            fallback_reasons.append(
                f"UNKNOWN_IMPACT: {path} matches no declared dependency pattern"
            )

    if fallback_reasons:
        mode = "full"
        affected_names = all_names
    elif not affected:
        mode = "none"
        affected_names = []
    else:
        mode = "subset"
        affected_names = [name for name in all_names if name in affected]

    return Impact(
        mode=mode,
        affected=affected_names,
        reasons=reasons,
        fallback_reasons=fallback_reasons,
    )
