"""Shared scenario definitions for legacy-vs-refactored differential testing.

Each scenario is one environment applied to both implementations.
`kind` states the expected relationship:

  same_output       both succeed and every reader output is identical
  new_raises        refactored aborts at startup naming `path` (hardening);
                    legacy outcome is captured for the report
  new_raises_all    refactored aggregates all listed paths in one error
  blank_default_ok  blank optional input: new falls back to the default and
                    matches the clean baseline; legacy errors or drifts
  bool_blank_reject blank bool input: new rejects at startup; legacy silently
                    coerces blank to False
  output_diff       both succeed but outputs differ for a documented reason
"""

BASELINE = {
    "DB_HOST": "db.internal",
    "CACHE_PREFIX": "svc:",
    "JWT_SECRET": "s3cret",
}

SCENARIOS = [
    # --- equivalent on all valid inputs -----------------------------------
    {"name": "valid/baseline-required-only", "env": {}, "kind": "same_output"},
    {"name": "valid/every-key-explicit", "kind": "same_output", "env": {
        "DB_HOST": "db.internal", "DB_PORT": "6543", "DB_USER": "svcacct",
        "DB_POOL_SIZE": "25", "DB_SSL": "false",
        "CACHE_PREFIX": "svc:", "CACHE_TTL": "900",
        "QUEUE_BROKER": "amqp://mq", "QUEUE_CONCURRENCY": "8",
        "QUEUE_DURABLE": "0",
        "JWT_SECRET": "s3cret", "JWT_TTL_MINUTES": "120", "JWT_ISSUER": "idp",
        "APP_WORKERS": "6", "APP_PORT": "9090", "LOG_LEVEL": "debug",
    }},
    {"name": "valid/partial-override-yes-no-bools", "kind": "same_output", "env": {
        "DB_POOL_SIZE": "20", "DB_SSL": "yes", "APP_WORKERS": "4",
        "LOG_LEVEL": "warning", "QUEUE_DURABLE": "no", "JWT_ISSUER": "idp",
    }},
    {"name": "valid/numeric-boundary-values", "kind": "same_output", "env": {
        "DB_PORT": "1", "APP_PORT": "65535", "DB_POOL_SIZE": "1",
        "QUEUE_CONCURRENCY": "64", "CACHE_TTL": "0", "APP_WORKERS": "32",
        "JWT_TTL_MINUTES": "1",
    }},

    # --- missing / blank required keys ------------------------------------
    {"name": "required/missing-db-host", "env": {"__remove__": ["DB_HOST"]},
     "kind": "new_raises", "path": "db.host",
     "legacy": "proceeds with host=None (empty value used downstream)"},
    {"name": "required/blank-db-host", "env": {"DB_HOST": "  "},
     "kind": "new_raises", "path": "db.host",
     "legacy": "proceeds with host='  ' (whitespace treated as a host)"},
    {"name": "required/missing-cache-prefix",
     "env": {"__remove__": ["CACHE_PREFIX"]},
     "kind": "new_raises", "path": "cache.prefix",
     "legacy": "KeyError deferred to cache.reader() call site"},
    {"name": "required/blank-cache-prefix", "env": {"CACHE_PREFIX": ""},
     "kind": "new_raises", "path": "cache.prefix",
     "legacy": "proceeds with prefix='' (empty prefix collides with other services)"},
    {"name": "required/missing-jwt-secret",
     "env": {"__remove__": ["JWT_SECRET"]},
     "kind": "new_raises", "path": "auth.secret",
     "legacy": "silently defaults to '' and issues unsigned/empty-key tokens"},
    {"name": "required/blank-jwt-secret", "env": {"JWT_SECRET": ""},
     "kind": "new_raises", "path": "auth.secret",
     "legacy": "silently coerces blank secret to falsy, still starts"},

    # --- type mismatches ---------------------------------------------------
    {"name": "type/db-port-not-int", "env": {"DB_PORT": "abc"},
     "kind": "new_raises", "path": "db.port",
     "legacy": "ValueError deferred to db.reader() call site"},
    {"name": "type/db-port-tail-junk", "env": {"DB_PORT": "5432x"},
     "kind": "new_raises", "path": "db.port",
     "legacy": "ValueError deferred to db.reader() call site"},
    {"name": "type/db-ssl-not-bool", "env": {"DB_SSL": "maybe"},
     "kind": "new_raises", "path": "db.ssl",
     "legacy": "silently maps any unrecognised token to False"},

    # --- range / choice violations -----------------------------------------
    {"name": "range/db-port-too-high", "env": {"DB_PORT": "70000"},
     "kind": "new_raises", "path": "db.port",
     "legacy": "proceeds with port=70000, connection fails later"},
    {"name": "range/db-port-zero", "env": {"DB_PORT": "0"},
     "kind": "new_raises", "path": "db.port",
     "legacy": "proceeds with port=0"},
    {"name": "range/db-pool-size-zero", "env": {"DB_POOL_SIZE": "0"},
     "kind": "new_raises", "path": "db.pool_size",
     "legacy": "proceeds with pool_size=0"},
    {"name": "range/queue-concurrency-too-high",
     "env": {"QUEUE_CONCURRENCY": "128"},
     "kind": "new_raises", "path": "queue.concurrency",
     "legacy": "proceeds with concurrency=128"},
    {"name": "range/app-port-too-high", "env": {"APP_PORT": "70000"},
     "kind": "new_raises", "path": "runtime.port",
     "legacy": "proceeds with port=70000"},
    {"name": "range/jwt-ttl-negative", "env": {"JWT_TTL_MINUTES": "-5"},
     "kind": "new_raises", "path": "auth.ttl_minutes",
     "legacy": "proceeds with ttl=-5"},
    {"name": "choice/log-level-unknown", "env": {"LOG_LEVEL": "trace"},
     "kind": "new_raises", "path": "observability.log_level",
     "legacy": "proceeds and pushes level='TRACE' into logging"},

    # --- bool blanks: new rejects, legacy coerces to False -----------------
    {"name": "blank-bool/db-ssl", "env": {"DB_SSL": ""},
     "kind": "bool_blank_reject", "path": "db.ssl"},
    {"name": "blank-bool/queue-durable", "env": {"QUEUE_DURABLE": "   "},
     "kind": "bool_blank_reject", "path": "queue.durable"},

    # --- blank optional strings: new treats as unset, legacy keeps '' ------
    {"name": "blank-default/db-user", "env": {"DB_USER": ""},
     "kind": "blank_default_ok", "path": "db.user"},
    {"name": "blank-default/queue-broker", "env": {"QUEUE_BROKER": ""},
     "kind": "blank_default_ok", "path": "queue.broker"},
    {"name": "blank-default/jwt-issuer", "env": {"JWT_ISSUER": ""},
     "kind": "blank_default_ok", "path": "auth.issuer"},

    # --- parser dialect drift (documented, not a hardening error) ----------
    {"name": "drift/bool-on-legacy-only-false", "env": {"DB_SSL": "on"},
     "kind": "output_diff", "path": "db.ssl",
     "reason": "legacy accepts only 1/true/yes as truthy and maps 'on' to "
               "False; the unified parser documents 1/true/yes/on as truthy"},

    # --- aggregated startup errors -----------------------------------------
    {"name": "aggregate/three-faults-at-once",
     "env": {"DB_HOST": "", "DB_PORT": "abc", "LOG_LEVEL": "trace"},
     "kind": "new_raises_all",
     "paths": ["db.host", "db.port", "observability.log_level"],
     "legacy": "fails one at a time at unrelated call sites, if at all"},
]


# Blank for every optional int key: legacy does int('') at the call site,
# the unified loader falls back to the declared default. Generated
# programmatically so a newly declared int key is covered automatically.
def _blank_int_scenarios(spec_paths):
    for spec in spec_paths:
        if spec.type != "int" or spec.required or spec.default is None:
            continue
        yield {
            "name": f"blank-default-int/{spec.env.lower()}",
            "env": {spec.env: ""},
            "kind": "blank_default_ok",
            "path": spec.path,
        }
