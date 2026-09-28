#!/bin/sh
# 可复跑验证：干净基线零告警 -> 引入一处新违规必定失败 -> 删除后恢复零告警。
# 用法: sh tools/verify_clock_check.sh
set -u
cd "$(dirname "$0")/.." || exit 2
CHECK="python3 tools/check_clock_usage.py src"
PROBE="src/_clock_probe.py"
fail() { echo "== 验证失败: $1"; rm -f "$PROBE"; exit 1; }

echo "== [1/3] 干净基线（期望 PASS, 退出码 0）"
$CHECK; [ $? -eq 0 ] || fail "干净基线应通过"

echo
echo "== [2/3] 引入一处新违规 time.time()（期望发现违规, 退出码 1）"
cat > "$PROBE" <<'PY'
import time
def newly_introduced():
    return time.time()
PY
$CHECK
rc=$?
[ "$rc" -eq 1 ] || fail "引入新违规后应退出 1，实际 $rc"
echo "（退出码 $rc，符合预期）"

echo
echo "== [3/3] 清理违规（期望恢复 PASS, 退出码 0）"
rm -f "$PROBE"
$CHECK; [ $? -eq 0 ] || fail "清理后应恢复零告警"
echo
echo "== 全部验证通过"
