# 字段级日志脱敏（Python 3 标准库，无第三方依赖）

## 文件
- `redact.py` — 脱敏库：字段名规则 + 内容特征规则、嵌套/数组递归、留痕、流式处理
- `selftest.py` — 自测：漏网检查（必须为零）、留痕完整性、摘要可校验、误伤检查
- `bench.py` — 性能对比：过滤前后体积与耗时
- `artifacts/` — 运行产物（漏网报告、留痕样例、性能数据）

## 运行命令
```bash
python3 selftest.py          # 自测 + 漏网检查，失败时退出码非零
python3 bench.py 20000       # 性能对比（参数为日志行数）
```

流式过滤任意日志文件（内存有界，支持超长行）：
```bash
python3 - <<'PY'
from redact import Redactor, redact_stream
import sys
r = Redactor()
with open('in.log') as i, open('out.log','w') as o, open('audit.jsonl','w') as a:
    print(redact_stream(i, o, r, audit_fh=a))
PY
```

## 识别规则
- 字段名（分词后词级匹配，兼容驼峰/下划线/数字后缀改写，如 `userPassword2`、`x_pwd_bak`）：
  password/passwd/pwd/passphrase/secret/token/apikey/credential(s)/privatekey/accesskey/secretkey/sessionid/authorization/idcard/ssn
- 内容特征（自由文本、内嵌 JSON 片段均生效）：
  kv 型口令（`password=...`、`"token":"..."`）、Bearer、JWT、AWS AKID、邮箱、手机号、
  身份证（含校验位验证）、银行卡（Luhn 验证）

## 脱敏格式与留痕
- 脱敏值形如 `[REDACTED:field:password#95c102341a3c]`，其中 `#` 后为原值 sha256 前 12 位，
  可用来校验“某明文是否就是被脱敏的值”，但不泄露明文本身。
- 每条脱敏生成留痕：`{path, rule, digest, value_length, action}`，见 `artifacts/audit_sample.jsonl`。

## 覆盖率证据
- `artifacts/leak_check.json`：17 个明文秘密（含嵌套、数组元素、改名字段、自由文本、
  内嵌 JSON、超长行）脱敏后残留为 0，且每个秘密都有对应留痕、摘要可校验。
- `artifacts/perf.json`：2 万行混合日志（含 1MB 超长行）过滤前后体积/耗时对比。
