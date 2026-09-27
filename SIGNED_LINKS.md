# 签名链接库（signed_links）

Python 3 标准库实现，无任何第三方依赖。用于签发/校验"临时授权下载链接"：
签名覆盖资源标识、生效/过期时间与全部业务参数，泄露的链接仅在时间窗口内可用，
任何参数增删改都会校验失败并可定位差异。

## 运行

```bash
python3 -m unittest test_signed_links -v   # 全部自测（含时间边界、恒定时间）
python3 demo_tamper.py                     # 篡改用例与检出结果演示
```

## 用法

```python
import signed_links as sl

issuer = sl.SignedLinkIssuer(b"secret", clock_skew=5.0)  # clock 可注入
link = issuer.sign("/files/a.pdf", [("user", "alice")],
                   not_before=1_000, expires=2_000)
url = f"https://cdn.example.com{link.resource}?{link.query()}"

result = issuer.verify_query("/files/a.pdf", "user=alice&_nb=1000&_exp=2000&_sig=...")
result.ok        # True/False
result.reason    # bad_signature | not_yet_valid | expired | invalid_params

# 校验失败时定位篡改点：
diff = sl.diff_params(原始参数, 实际参数)
print(diff.describe())
```

## 关键语义

- **规范化**：参数视为"名字 -> 值多重集合"，顺序无关、重复值计数；
  名/值 UTF-8 后 percent-encode，排序拼接。`a` 与 `a=` 等价（空值），
  `a=` 与缺失 `a` 不等价。`_sig/_nb/_exp` 为保留名。
- **时间窗口**：`not_before - skew <= now < expires + skew`，下界闭、上界开。
  `now == expires`（skew=0）即视为过期；skew 同时放宽两侧边界。
- **恒定时间**：唯一比较点是 `hmac.compare_digest`（C 实现、不提前退出）。
  验证方式：(1) 测试 monkeypatch `compare_digest` 证明校验路径确实调用它；
  (2) 统计冒烟测试比较"首位不同"与"末位不同"签名的耗时差异在噪声范围内。
  统计测试不能证明恒定时间，根本保证来自 `compare_digest` 本身。
- **确定性**：同（密钥, 资源, 参数多重集, 时间窗）必得同签名，参数顺序无关。
- **限制（fail-closed）**：参数 ≤64 个、名 ≤128 字符、值 ≤2048 字符、
  规范化载荷 ≤16384 字节；签发抛 `ParamLimitError`，校验返回 `invalid_params`。
- **泄露说明**：无状态 HMAC 无法防重放，泄露链接在窗口内可被任何人使用；
  应配合短窗口 + HTTPS。若需一次性链接，可在业务层维护已用签名集合（jti）。
