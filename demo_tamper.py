"""篡改用例演示：对同一签名链接施加各种篡改，打印检出结果与差异。

运行：python3 demo_tamper.py
"""

import signed_links as sl

SECRET = b"demo-secret-do-not-use-in-prod"
RESOURCE = "/files/report-2026q3.pdf"
NOT_BEFORE = 1_000
EXPIRES = 2_000

issuer = sl.SignedLinkIssuer(SECRET, clock=lambda: 1_500.0, clock_skew=0.0)
original_params = [("user", "alice"), ("disposition", "attachment"),
                   ("tag", "a"), ("tag", "b"), ("note", "")]
link = issuer.sign(RESOURCE, original_params, not_before=NOT_BEFORE, expires=EXPIRES)

print(f"原始链接: https://cdn.example.com{link.resource}?{link.query()}\n")

cases = []


def add_case(name, *, params=None, resource=RESOURCE, expires=EXPIRES,
             not_before=NOT_BEFORE, signature=None, now=None):
    cases.append((name, dict(
        params=link.params if params is None else params,
        resource=resource, expires=expires, not_before=not_before,
        signature=link.signature if signature is None else signature,
        now=now,
    )))


add_case("未篡改（基线，应通过）")
add_case("改参数值 user=alice->bob",
         params=[(k, "bob" if k == "user" else v) for k, v in link.params])
add_case("新增参数 admin=true", params=link.params + [("admin", "true")])
add_case("删除参数 disposition",
         params=[p for p in link.params if p[0] != "disposition"])
add_case("删除重复参数之一 tag=b",
         params=[p for p in link.params if p != ("tag", "b")])
add_case("参数改名 user->role",
         params=[("role" if k == "user" else k, v) for k, v in link.params])
add_case("改资源标识", resource="/files/other.pdf")
add_case("延长过期时间 +3600s", expires=EXPIRES + 3600)
add_case("提前生效时间 -500s", not_before=NOT_BEFORE - 500)
add_case("截断签名", signature=link.signature[:-2])
add_case("参数重排（合法，应通过）", params=list(reversed(link.params)))
add_case("空值参数 note= 被删除",
         params=[p for p in link.params if p[0] != "note"])
add_case("已过期时刻访问 now=expires", now=EXPIRES)
add_case("尚未生效时刻访问 now=not_before-1", now=NOT_BEFORE - 1)

print(f"{'用例':<28} {'结果':<6} {'原因':<14} 差异")
print("-" * 100)
for name, kw in cases:
    result = issuer.verify(
        kw["resource"], kw["params"],
        not_before=kw["not_before"], expires=kw["expires"],
        signature=kw["signature"], now=kw["now"],
    )
    if result.ok:
        verdict, reason, diff = "通过", "-", "-"
    else:
        verdict, reason = "拒绝", result.reason
        if result.reason == "bad_signature":
            diff = sl.diff_params(link.params, kw["params"]).describe()
            if kw["resource"] != RESOURCE:
                diff += f"; 资源被改为 {kw['resource']}"
            if kw["expires"] != EXPIRES:
                diff += f"; expires 被改为 {kw['expires']}"
            if kw["not_before"] != NOT_BEFORE:
                diff += f"; not_before 被改为 {kw['not_before']}"
            if kw["signature"] != link.signature:
                diff += "; 签名本身被改动"
        else:
            diff = result.detail
    print(f"{name:<28} {verdict:<6} {reason:<14} {diff}")
