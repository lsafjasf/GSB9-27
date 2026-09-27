# 文件完整性监控（FIM）噪声治理：修复方案与数据

## 1. 问题根因

修复前的实现（`fim/baseline.py` 中的 `NaiveMonitor`，按“现状”复现）有四个缺陷：

1. **用 stat 推断内容**：只比对 `(size, mtime, mode)`，不计算内容哈希。
   攻击者替换内容后只要保持长度不变并还原 mtime，监控完全无感知（漏报）。
2. **没有排除规则**：编辑器临时文件、构建产物、日志轮转全部产生“文件被修改/创建”告警（误报）。
3. **权限变化与内容篡改同一告警级别**：普通 `chmod` 也按高严重度上报。
4. **无准入、无审计**：合法发布没有显式声明通道；告警事后无法解释。

## 2. 修复设计（`fim/fmonitor.py`）

1. **SHA-256 内容指纹**：状态库存哈希，`size/mtime` 仅作取证字段。哈希变化即内容变化，
   不依赖时间戳；告警附带 `size_same/mtime_same/stat_forged`，直接标记“伪造 stat 掩盖篡改”。
2. **显式、可审计的排除规则**：规则在 `fim/rules.json` 中声明（稳定 ID、类别、说明、正则），
   对“相对监控根的 POSIX 路径”及 basename 匹配。规则命中**不告警，但必然写一条
   `rule_hit` 审计记录到 `audit.log`（含规则 ID、路径、阶段），杜绝静默吞事件。
3. **合法更新准入（admit）**：发布前必须调用 `admit()`/CLI `admit` 声明路径、操作人、
   原因/工单及新内容的预期 SHA-256；准入一次性消费、落盘持久化、全程审计。
   实际内容与声明哈希不符时记录 `admission_warning`。
4. **权限分级**：仅权限变化降级为审计事件 `perms_changed`；新增 setuid/setgid/sticky
   或变成全局可写则升级为真实告警 `tamper_perms_dangerous`。
5. **原子替换/删除重建**：状态按路径跟踪、不依赖 inode。内容相同的原子替换只记
   `identical_replace`（含新旧 inode）；哈希变化无论是否换 inode 一律检出。
6. **仅标准库**（Python 3.12，`hashlib/json/os/stat/re/unittest` 等）。

## 3. 排除规则说明（fim/rules.json）

| 规则 ID | 类别 | 覆盖内容（示例） |
|---|---|---|
| `R001-temp-files` | 临时文件 | `tmp/`、`temp/`、`cache/` 目录；`.a.swp/.swo`；`*~`、`*.bak`；`#x#`、`.#x` |
| `R002-generated-artifacts` | 生成产物 | `__pycache__/`、`*.pyc/*.pyo`、`dist/`、`build/`、`*.egg-info` |
| `R003-log-rotation` | 日志轮转 | `logs/`；`*.log/.err/.out` 及轮转后缀 `*.log.1`、`*.log.2026-09-28.gz` |

修改规则只需编辑 JSON（或用 `--rules` 指定自定义文件），无需改代码。每条命中都有
`rule_hit` 审计记录，可用 `grep rule_hit state/audit.log` 回放。

## 4. 误报/漏报数据（scripts/reproduce.py，确定性工作负载）

工作负载：**13 次合法变动**（临时文件 4、生成产物 3、日志轮转 3、准入发布 2、
纯权限调整 2 个事件、删除重建缓存 1）+ **3 次真实篡改**，三种篡改均保持文件大小不变
并把 mtime 伪造回原值：

- T1 `etc/hosts`：原地覆写，等长，mtime 还原；
- T2 `etc/cron.d/job`：删除后重建，等长，mtime 还原；
- T3 `usr/bin/healthcheck`：`os.replace` 原子替换，inode 变化，mtime 还原。

| 指标 | 修复前 NaiveMonitor | 修复后 FixedMonitor |
|---|---|---|
| 总告警 | 15 | 3 |
| 误报（FP） | 15 | 0 |
| 真实检出（TP） | 0 | 3 |
| 漏报（FN） | 3 | 0 |
| **误报率**（噪声告警/总告警） | **100%** | **0%** |
| **漏报率**（漏检/篡改总数） | **100%** | **0%** |

修复前 15 条噪声构成：临时文件 4、生成产物 3、日志轮转 3、合法发布 2、权限变化 2、
删除重建 1；3 个篡改全部淹没（stat 未变）。修复后 3 条告警全部为
`tamper_content` 且带 `stat_forged: true`；另有 22 条审计事件（规则命中 15、
准入更新 3、权限变化 2、扫描信息 2），噪声可审计但不再占用告警通道。
机器可读结果见 `report-data.json`（由 `python3 scripts/reproduce.py --json` 重新生成）。

## 5. 关键检出用例（回归测试 fim/tests/test_fim.py，共 15 个）

- `test_same_size_forged_mtime_tamper_is_detected`：**等长替换 + mtime 伪造 → 必须检出**（核心回归）。
- `test_naive_baseline_misses_forged_tamper`：证明修复前实现对此场景漏报。
- `test_atomic_replace_different_content_detected`：原子替换、inode 变化仍检出。
- `test_atomic_replace_identical_content_is_audit_only`：内容相同的原子替换只审计不告警。
- `test_delete_and_recreate_tamper_detected` / `test_unexpected_deletion_alerts`：删除重建、删除。
- `test_permission_only_change_is_audit_not_alert` / `test_dangerous_permission_change_escalates`：权限分级。
- `test_excluded_paths_never_alert_and_are_audited`：排除路径零告警且有 `rule_hit` 审计。
- `test_admitted_update_does_not_alert`（含一次性消费）、`test_admitted_delete_recreate_is_audit_only`、
  `test_admission_wrong_digest_is_warned_but_audited`、`test_unexpected_creation_alerts`、
  `test_state_persists_across_restarts`、`test_rules_loaded_from_manifest`。

## 6. 运行命令

```bash
cd /home/administrator/gsb/uid324/A

# 复现并打印修复前后误报/漏报对比（--json 输出机器可读结果，--keep 保留现场目录）
python3 scripts/reproduce.py
python3 scripts/reproduce.py --json

# 回归测试
python3 -m unittest discover -s fim/tests -v

# 实际使用（CLI）
python3 -m fim --root /监控目录 --state ./state/fim.json baseline
python3 -m fim --root /监控目录 --state ./state/fim.json admit etc/app.conf \
    --operator alice --reason "配置发布" --ticket CR-1001 --expected-file /tmp/new.conf
python3 -m fim --root /监控目录 --state ./state/fim.json scan   # 有告警时退出码为 1
grep rule_hit ./state/audit.log                                # 审计规则命中
```

## 7. 文件清单

- `fim/fmonitor.py`：修复后的监控器（哈希、规则、准入、权限分级、审计）。
- `fim/baseline.py`：修复前的 stat-only 实现，仅用于前后对比。
- `fim/rules.json`：显式排除规则清单（可审计、可外部替换）。
- `fim/rules.py`：规则加载/匹配；`fim/fsutil.py`：哈希/遍历/原子写工具。
- `fim/__main__.py`：CLI（baseline/admit/scan）。
- `fim/tests/test_fim.py`：15 个回归测试。
- `scripts/reproduce.py`：噪声量化与修复前后对比的复现脚本。
