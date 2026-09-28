"""Scenario runner: borrow/reclaim data + isolation comparison.

Run: python3 simulation.py
"""

from quota_pool import QuotaPool, QuotaError

TICK_MS = 10


def table(headers, rows):
    rows = [[str(c) for c in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h)
              for i, h in enumerate(headers)]
    line = lambda r: "  ".join(c.rjust(w) for c, w in zip(r, widths))
    out = [line(headers), line(["-" * w for w in widths])]
    out += [line(r) for r in rows]
    return "\n".join(out)


def jain_index(values):
    values = [v for v in values if v > 0] or [0]
    num = sum(values) ** 2
    den = len(values) * sum(v * v for v in values)
    return num / den if den else 1.0


def scenario_single_tenant():
    print("=" * 72)
    print("场景 1: 单租户 —— 空闲容量可被借用，写死配额则浪费")
    print("=" * 72)
    pool = QuotaPool(100, tick_ms=TICK_MS)
    pool.add_tenant("solo", guarantee=20, limit=100)
    for _ in range(10):
        pool.tick({"solo": 100})
    got = pool.stats["solo"]["granted"]
    static = 20 * 10  # hard-quota scheme caps at the guarantee
    print(table(["方案", "10 tick 获得算力", "池利用率"],
                [["静态硬配额 (limit=guarantee)", static, "20%"],
                 ["配额+借用 (本库)", got, "100%"]]))
    print("结论: 独占时空闲的 80 单位全部可借出，上限 100 仍是硬约束。\n")


def scenario_all_saturated():
    print("=" * 72)
    print("场景 2: 全租户同时打满 —— 保障额度必须兑现")
    print("=" * 72)
    pool = QuotaPool(100, tick_ms=TICK_MS)
    for i in range(4):
        pool.add_tenant("t%d" % i, guarantee=25, limit=100)
    grants = pool.tick({"t%d" % i: 100 for i in range(4)})
    rows = [[tid, 25, 100, 100, grants[tid]] for tid in sorted(grants)]
    rows.append(["合计", 100, "-", 400, sum(grants.values())])
    print(table(["租户", "保障", "上限", "需求", "实得"], rows))
    print("不变量: 每人实得 == 保障 25, 总量 100 <= 池容量 100, 无人超上限。\n")


def scenario_borrow_reclaim():
    print("=" * 72)
    print("场景 3: 借用中被回收 —— owner 回来时 borrower 立即让出")
    print("=" * 72)
    pool = QuotaPool(100, tick_ms=TICK_MS)
    pool.add_tenant("owner", guarantee=50, limit=100)
    pool.add_tenant("borrower", guarantee=30, limit=100)
    history = []
    for tick in range(12):
        demands = {"borrower": 100}
        if tick >= 8:
            demands["owner"] = 50  # owner 在第 8 tick 回来
        grants = pool.tick(demands)
        history.append((tick, demands.get("owner", 0), grants.get("owner", 0),
                        demands["borrower"], grants["borrower"]))
    print(table(["tick", "owner需求", "owner实得", "borrower需求", "borrower实得"],
                history[5:11]))
    summary = pool.reclaim_summary()
    event = pool.reclaim_events[0]
    print("\n回收事件: tick=%d, 从 borrower 收回 %d 单位" % (event.tick, event.revoked_units))
    print(table(["回收时延指标", "数值"],
                [["事件数", summary["events"]],
                 ["最大时延 (tick)", summary["max_latency_ticks"]],
                 ["最大时延 (ms)", summary["max_latency_ms"]],
                 ["平均时延 (ms)", summary["avg_latency_ms"]],
                 ["理论上界 (1 tick)", "%.0f ms" % summary["bound_ms"]],
                 ["收回借用单位", summary["total_revoked_units"]]]))
    print("结论: 授权是 1-tick 租约，owner 的保障在提出需求的同一 tick 恢复，")
    print("回收时延有确定性上界 = 1 个调度量子 (%d ms)。\n" % TICK_MS)


def scenario_dynamic_join():
    print("=" * 72)
    print("场景 4: 租户动态加入 —— 准入控制 + 重新均衡")
    print("=" * 72)
    pool = QuotaPool(100, tick_ms=TICK_MS)
    pool.add_tenant("a", guarantee=40, limit=100)
    pool.add_tenant("b", guarantee=40, limit=100)
    rows = []
    grants = pool.tick({"a": 100, "b": 100})
    rows.append(["tick0  a,b 各保障40", "-", dict(grants)])
    pool.add_tenant("c", guarantee=20, limit=100)  # 40+40+20 = 100, 准入通过
    try:
        pool.add_tenant("d", guarantee=1, limit=100)
        rows.append(["d 申请保障 1", "准入失败?", "BUG"])
    except QuotaError as exc:
        rows.append(["d 申请保障 1", "被拒绝", str(exc)])
    grants = pool.tick({"a": 100, "b": 100, "c": 100})
    rows.append(["tick1  c 加入后", "-", dict(grants)])
    pool.remove_tenant("b")
    grants = pool.tick({"a": 100, "c": 100})
    rows.append(["tick2  b 退出后", "-", dict(grants)])
    print(table(["事件", "结果", "分配"], rows))
    print("结论: 保障总量永不超池容量（准入拒绝），加入/退出后保障依然兑现。\n")


def scenario_isolation_comparison():
    print("=" * 72)
    print("场景 5: 突发流量隔离效果对比（本库 vs 无隔离 vs 静态配额）")
    print("=" * 72)
    # web/batch 是正常租户，burst 在 tick 30-69 突发打满。
    specs = {"web": (30, 100), "batch": (20, 100), "burst": (10, 100)}
    base_demand = {"web": 30, "batch": 20, "burst": 10}

    def demand_at(tick):
        d = dict(base_demand)
        if 30 <= tick < 70:
            d["burst"] = 100
        return d

    def run(policy):
        pool = QuotaPool(100, tick_ms=TICK_MS)
        for tid, (g, l) in specs.items():
            if policy == "static":
                pool.add_tenant(tid, g, g)      # 写死配额: limit = guarantee
            else:
                pool.add_tenant(tid, g, l)
        granted = {tid: 0 for tid in specs}
        demanded = {tid: 0 for tid in specs}
        burst_grants = {tid: 0 for tid in specs}
        guarantee_violations = 0
        util = 0
        burst_util = 0
        for tick in range(100):
            demands = demand_at(tick)
            if policy == "no-isolation":
                # FCFS、无每租户上限，突发租户排在最前（最坏情况）。
                grants = {tid: 0 for tid in specs}
                left = 100
                for tid in ["burst", "web", "batch"]:
                    grants[tid] = min(demands[tid], left)
                    left -= grants[tid]
            else:
                grants = pool.tick(demands)
            for tid in specs:
                granted[tid] += grants[tid]
                demanded[tid] += demands[tid]
                if 30 <= tick < 70:
                    burst_grants[tid] += grants[tid]
                if demands[tid] >= specs[tid][0] and grants[tid] < specs[tid][0]:
                    guarantee_violations += 1
            util += sum(grants.values())
            if 30 <= tick < 70:
                burst_util += sum(grants.values())
        return (granted, demanded, burst_grants, guarantee_violations,
                util / 100.0, burst_util / 40.0)

    rows = []
    for label, policy in [("无隔离 (FCFS, burst 优先)", "no-isolation"),
                          ("静态硬配额", "static"),
                          ("配额+借用 (本库)", "borrow")]:
        granted, demanded, burst_grants, violations, util, burst_util = run(policy)
        rows.append([
            label,
            "%d/%d" % (burst_grants["web"], demanded["web"] // 100 * 40),
            "%d/%d" % (burst_grants["batch"], demanded["batch"] // 100 * 40),
            burst_grants["burst"],
            violations,
            "%.0f%%" % burst_util,
            "%.0f%%" % util,
            "%.3f" % jain_index([granted[t] for t in specs]),
        ])
    print("突发窗口 = tick 30-69 (40 tick)。web/batch 列为突发期的实得/需求。")
    print(table(["方案", "web 实得/需求", "batch 实得/需求", "burst 实得",
                 "保障违约次数", "突发期利用率", "全程利用率", "Jain 公平指数"], rows))
    print("""
解读:
* 无隔离: burst 吃光整池，web/batch 在突发期吞吐为 0 —— 被拖死。
* 静态配额: 受害者安全，但 burst 被钉死在 10，突发期 40% 的池子空转。
* 本库: web/batch 全程拿满保障（违约 0 次），burst 仍能借到空闲额度，
  突发期利用率 100% —— 隔离与利用率兼得。
  （全程利用率 <100% 是因为非突发期真实需求只有 60，并非配额浪费。）
""")


if __name__ == "__main__":
    scenario_single_tenant()
    scenario_all_saturated()
    scenario_borrow_reclaim()
    scenario_dynamic_join()
    scenario_isolation_comparison()
