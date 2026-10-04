"""运行演示：python3 demo.py

输出四类情形的访问结果（覆盖足够 / 缺一项 / 含通配 / 资源不匹配），
以及令牌吊销前后的对比和结构化拒绝记录。
"""

import json

from scope_check import TokenRegistry, make_scope


def show(title, decision):
    status = "放行 ALLOW" if decision.allowed else "拒绝 DENY"
    print(f"[{status}] {title}")
    print(f"  reason   : {decision.reason}")
    print(f"  subject  : {decision.subject}")
    print(f"  required : {list(decision.required)}")
    if decision.missing:
        print(f"  missing  : {list(decision.missing)}")
    if decision.matched_by:
        print(f"  matched  : {decision.matched_by}")
    print()


def main():
    registry = TokenRegistry()

    # 1. 覆盖作用域足够
    alice = registry.issue("alice", ["read:invoice", "write:invoice"])
    show("作用域足够：读+写发票", registry.authorize(alice.token_id, ["read:invoice"]))

    # 2. 缺少某一项
    show("缺少作用域：只读令牌请求删除",
         registry.authorize(alice.token_id, ["read:invoice", "delete:invoice"]))

    # 3. 含通配
    admin = registry.issue("admin", ["read:*", "write:*"])
    show("通配覆盖：read:* 读取用户", registry.authorize(admin.token_id, ["read:user"]))
    show("通配不跨段：read:* 请求 write:user",
         registry.authorize(admin.token_id, ["write:user", "delete:user"]))

    # 4. 资源级限定
    bob = registry.issue("bob", [make_scope("read", "invoice", "inv-1001")])
    show("资源级：访问被授权资源",
         registry.authorize(bob.token_id, [make_scope("read", "invoice", "inv-1001")]))
    show("资源不匹配：访问另一张发票",
         registry.authorize(bob.token_id, [make_scope("read", "invoice", "inv-1002")]))

    # 5. 吊销前后对比（同一令牌，未过期）
    carol = registry.issue("carol", ["read:invoice"])
    print("=== 吊销前后对比（令牌始终未过期）===")
    show("吊销前", registry.authorize(carol.token_id, ["read:invoice"]))
    registry.revoke(carol.token_id)
    show("吊销后立即再访问", registry.authorize(carol.token_id, ["read:invoice"]))

    # 6. 拒绝记录（结构化审计日志）
    print("=== 拒绝记录（decision_log 中 reason != ok 的条目）===")
    denied = [d for d in registry.decision_log if not d.allowed]
    for d in denied:
        print(json.dumps({
            "allowed": d.allowed,
            "reason": d.reason,
            "token_id": d.token_id[:8] + "..." if d.token_id else None,
            "subject": d.subject,
            "required": list(d.required),
            "missing": list(d.missing),
            "timestamp": round(d.timestamp, 3),
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
