# commitlint：提交信息与变更范围规范检查

纯 Python 3 标准库实现，无第三方依赖。

## 文件

- `commitlint.py` — 检查脚本（check / evaluate 两个子命令）
- `rules.json` — 规则配置：类型、scope→路径映射、描述约束、豁免规则
- `labeled_commits.json` — 16 条真实风格提交 + 人工标注，用于对拍
- `selftest.py` — 23 个单元测试（unittest）
- `report_sample.txt` — 对拍报告样例（由 evaluate 生成）

## 运行命令

```bash
# 检查 git 仓库中的某个提交
python3 commitlint.py --rules rules.json check --git HEAD

# 直接检查给定信息与文件列表（CI 友好）
python3 commitlint.py check --message "feat(api): add thing" \
    --files src/api/thing.py --author-email dev@example.com

# 与人工标注对拍，输出误报/漏报并生成报告
python3 commitlint.py --rules rules.json evaluate \
    --dataset labeled_commits.json --report report_sample.txt

# 自测
python3 selftest.py -v
```

## 退出码

- `0` 检查通过 / 对拍全部一致
- `1` 存在违规 / 对拍存在不一致
- `2` 用法或配置错误

## 规则摘要（rules.json）

- 结构：`type(scope): description`，type 必须在声明列表内，scope 可空（`require_scope` 控制）
- 描述：非空、≤72 字符、仅 ASCII（`description.ascii_only`）
- 范围一致性：声明 scope 时，改动文件必须落在该 scope 的路径内；`shared_files`（如 README.md）对所有 scope 放行
- 空提交：`require_changes: true` 时判违规
- 豁免（均可审计，报告中输出命中规则与原因）：
  - `merge`：subject 以 `Merge ` 开头
  - `revert`：subject 匹配 `^Revert "` 且 body 含 `This reverts commit <sha>`
  - `bot`：作者邮箱/名字匹配配置模式（`[bot]` 已按字面量转义，避免误伤 Bob/Carol）

## 对拍结果

当前数据集 16 条全部一致：误报 0、漏报 0（见 `report_sample.txt`）。
注意：fnmatch 中 `[bot]` 是字符类，bot 模式必须写成 `*\[bot\]@*`，否则会把普通人名误判为机器人——`selftest.py` 中有对应回归测试。
