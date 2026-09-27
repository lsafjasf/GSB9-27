"""Minimal file-integrity monitor (stdlib only).

Public API:
    fim.fmonitor.FixedMonitor   - fixed monitor (hash content + explicit rules)
    fim.baseline.NaiveMonitor   - pre-fix implementation used for comparison
    fim.rules.load_rules        - load the declarative exclusion rules
"""
