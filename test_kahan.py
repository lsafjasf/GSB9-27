"""KahanSum 自测：正确性、合并一致性、边界情形。仅标准库。"""
import random
import sys
from fractions import Fraction

from kahan import KahanSum

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


def exact_sum(xs):
    return sum((Fraction(x) for x in xs), Fraction(0))


def rel_err(got, exact):
    exact = float(exact)
    if exact == 0.0:
        return abs(got)
    return abs(got - exact) / abs(exact)


# 1. 全零
k = KahanSum([0.0] * 100000)
check("全零序列", k.value == 0.0 and k.count == 100000, f"value={k.value}")

# 2. 正负抵消：1e16 + 1 - 1e16 重复
xs = []
for _ in range(1000):
    xs += [1e16, 1.0, -1e16]
k = KahanSum(xs)
naive = 0.0
for x in xs:
    naive += x
check("正负抵消", k.value == 1000.0, f"kahan={k.value}, naive={naive}")

# 3. 极大与极小值混合
xs = [1e308, 1e-308, -1e308, 1e-308, 5e-300]
k = KahanSum(xs)
exact = exact_sum(xs)
check("极大极小混合", rel_err(k.value, exact) < 1e-15,
      f"kahan={k.value:.6e}, exact={float(exact):.6e}")

# 4. 增量分批喂入 == 一次性喂入
random.seed(42)
xs = [random.uniform(-1e6, 1e6) for _ in range(10000)]
one_shot = KahanSum(xs)
batched = KahanSum()
for i in range(0, len(xs), 97):
    batched.add_many(xs[i:i + 97])
check("分批喂入一致", batched.value == one_shot.value and batched.count == one_shot.count)

# 5. 重置
batched.reset()
check("重置", batched.value == 0.0 and batched.count == 0)
batched.add(3.5)
check("重置后可用", batched.value == 3.5)

# 6. 合并一致性：merge(a,b) 与顺序累加 a+b 的元素一致（精确可表示情形应 bit 级相等）
random.seed(7)
part1 = [float(random.randint(-1000, 1000)) for _ in range(5000)]
part2 = [float(random.randint(-1000, 1000)) for _ in range(3000)]
seq = KahanSum(part1 + part2)
a, b = KahanSum(part1), KahanSum(part2)
m = a.merged(b)
check("合并(整数)与顺序累加 bit 级一致", m.value == seq.value and m.count == seq.count,
      f"merged={m.value}, sequential={seq.value}")

# 7. 合并一致性：一般浮点情形，与精确值对比误差在 1 ulp 量级
part1 = [random.uniform(-1e4, 1e4) for _ in range(20000)]
part2 = [random.uniform(-1e4, 1e4) for _ in range(15000)]
seq = KahanSum(part1 + part2)
m = KahanSum(part1).merged(KahanSum(part2))
exact = exact_sum(part1 + part2)
e_seq, e_m = rel_err(seq.value, exact), rel_err(m.value, exact)
check("合并(浮点)误差与顺序累加同量级", e_m <= max(e_seq, 1e-15) * 4 + 1e-16,
      f"rel_err merged={e_m:.3e}, sequential={e_seq:.3e}")

# 8. 合并结合性 smoke：(a+b)+c 与 a+(b+c) 都接近精确值
parts = [[random.uniform(0, 1) for _ in range(1000)] for _ in range(3)]
accs = [KahanSum(p) for p in parts]
left = accs[0].merged(accs[1]).merge(KahanSum(parts[2]))
right = KahanSum(parts[0]).merge(accs[1].merged(accs[2]))
exact = exact_sum(sum(parts, []))
check("合并结合性", rel_err(left.value, exact) < 1e-14 and rel_err(right.value, exact) < 1e-14)

# 9. 百万长度正确性
n = 1_000_000
xs = [1.0 / 3.0] * n
k = KahanSum(xs)
naive = 0.0
for x in xs:
    naive += x
exact = Fraction(1, 3) * n
check("百万长度", rel_err(k.value, exact) < 1e-15,
      f"kahan rel_err={rel_err(k.value, exact):.3e}, naive rel_err={rel_err(naive, exact):.3e}")

print()
if FAILURES:
    print(f"{len(FAILURES)} 项失败: {FAILURES}")
    sys.exit(1)
print("全部测试通过")
