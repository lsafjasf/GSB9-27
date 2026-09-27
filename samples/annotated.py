"""Hand-annotated sample functions for the metric-vs-human ranking check.

Each function was independently scored for "difficulty to understand and
modify safely" on a 1-10 scale by human judgement BEFORE the metric was
run; scores live in annotations.json. The check in
scripts/compare_annotations.py verifies the metric reproduces the human
ranking (Spearman / Kendall correlation).
"""


def f01_empty():
    pass


def f02_expr_only(x):
    return x * 2 + 1


def f03_long_straight(cfg):
    # 40+ straight-line statements: tedious, but no branching at all.
    host = cfg["host"]
    port = cfg["port"]
    user = cfg["user"]
    password = cfg["password"]
    database = cfg["database"]
    timeout = cfg["timeout"]
    retries = cfg["retries"]
    charset = cfg["charset"]
    ssl = cfg["ssl"]
    pool_size = cfg["pool_size"]
    host = host.strip()
    user = user.strip()
    database = database.strip()
    charset = charset.lower()
    port = int(port)
    timeout = int(timeout)
    retries = int(retries)
    pool_size = int(pool_size)
    dsn_host = "host=" + host
    dsn_port = "port=" + str(port)
    dsn_user = "user=" + user
    dsn_pass = "password=" + password
    dsn_db = "database=" + database
    dsn_timeout = "timeout=" + str(timeout)
    dsn_retries = "retries=" + str(retries)
    dsn_charset = "charset=" + charset
    dsn_ssl = "ssl=" + str(ssl)
    dsn_pool = "pool=" + str(pool_size)
    parts = [dsn_host, dsn_port, dsn_user, dsn_pass, dsn_db]
    parts = parts + [dsn_timeout, dsn_retries, dsn_charset, dsn_ssl, dsn_pool]
    dsn = ";".join(parts)
    length = len(dsn)
    checksum = length % 256
    header = "DSN:" + str(checksum)
    trailer = ":END"
    result = header + dsn + trailer
    result = result.strip()
    result = result.replace("  ", " ")
    return result


def f04_single_if(x):
    if x > 0:
        return x
    return -x


def f05_if_elif_chain(code):
    if code == 200:
        return "ok"
    elif code == 404:
        return "missing"
    elif code >= 500:
        return "server error"
    else:
        return "other"


def f06_guard_returns(value):
    if value is None:
        return "none"
    if isinstance(value, str):
        return "text"
    if isinstance(value, (int, float)):
        return "number"
    return "object"


def f07_loop_with_if(items, needle):
    for index, item in enumerate(items):
        if item == needle and index > 0:
            return index
    return -1


def f08_short_circuit(user, cfg):
    if user.is_active and not user.is_banned or cfg.allow_guests and cfg.open_signup:
        return grant_access(user)
    return deny_access(user)


def f09_try_except(payload):
    try:
        data = parse(payload)
        if data is None:
            raise ValueError("empty")
        return data
    except KeyError:
        return "missing key"
    except ValueError:
        return "bad value"
    except Exception:
        return "unknown error"


def f10_nested_ifs(order, user, stock):
    if order is not None:
        if user is not None and user.is_verified:
            if stock > order.quantity:
                if order.total <= user.credit:
                    return "accepted"
                else:
                    return "credit exceeded"
            else:
                return "out of stock"
        else:
            return "unverified"
    return "no order"


def f11_nested_loops(matrix, target):
    found = []
    for row in matrix:
        for cell in row:
            if cell == target:
                found.append(cell)
            elif cell < 0 and target > 0:
                continue
            else:
                found.append(-cell)
    return found


def f12_match_dispatch(event):
    match event:
        case {"type": "click", "x": x, "y": y}:
            return handle_click(x, y)
        case {"type": "key", "key": str() as k}:
            return handle_key(k)
        case {"type": "scroll"}:
            return handle_scroll(event)
        case {"type": "resize", "w": w, "h": h}:
            return handle_resize(w, h)
        case {"type": str() as t}:
            return handle_unknown(t)
        case _:
            return None


def f13_mixed_monster(jobs, limit, fallback):
    results = []
    errors = 0
    for job in jobs:
        if job is None or job.get("skip"):
            continue
        retries = 0
        while retries < 3:
            try:
                outcome = run(job)
                if outcome.ok and outcome.value is not None:
                    results.append(outcome.value)
                    break
                elif outcome.ok:
                    retries += 1
                else:
                    errors += 1
                    retries += 1
            except TimeoutError:
                retries += 1
            except RuntimeError as exc:
                log(exc)
                return fallback if fallback is not None else []
        if len(results) >= limit:
            return results
    return results if results else (fallback if fallback is not None else [])


def f14_comprehension_heavy(grid, threshold):
    return [
        [cell for cell in row if cell > threshold if cell % 2 == 0]
        for row in grid
        if row
    ]
