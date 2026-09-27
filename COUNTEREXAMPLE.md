# 最小反例记录

对拍主库 vs 参照实现（`python3 duel.py`）：13 万个随机用例
（多组种子）结果完全一致，无反例。

对拍过程中曾抓到并已修复的反例（同名参数重复出现时的语义分歧）：

```json
{
  "accept": "text/html;level=1;level=2",
  "server_offers": ["text/html;level=2;format=raw"],
  "main_result": null,
  "reference_result": "text/html;level=2;format=raw"
}
```

根因：主库把重复参数当多值处理导致匹配失败，参照实现按 dict
「后者覆盖」。修复：规则明确为同名参数 last-wins（见 RULES.md
第 1 节），主库在解析阶段归一化。

## 演示用反例（主库 vs 故意有缺陷的变体）

命令：`python3 duel.py --impl buggy --seed 7`

```json
{
  "accept": "*/*;q=0, text/*",
  "server_offers": ["text/png"],
  "main_result": "text/png",
  "buggy_result": null
}
```

分析：`buggy_variant.py` 的缺陷是「客户端列表中第一个能匹配的
条目决定权重」，无视具体度。本例中 `*/*;q=0` 先匹配，缺陷实现
误判为拒绝；正确规则（RULES.md 第 4 节）取最具体的匹配条目
`text/*`（q 缺省为 1），应接受 `text/png`。

该反例已无法再缩小：删去任一客户端条目或唯一的服务端能力后，
两个实现的分歧即消失；对条目做「去权重 / 去参数」简化同样使
分歧消失。
