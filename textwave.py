"""textwave — 保留局部极值的等宽文本波形渲染库（仅标准库）。

核心策略
--------
下采样：min-max 分桶。每个水平格对应原序列的一个连续区间，
取区间最小值与最大值，在该列竖直方向上填满两者之间的所有行。
因此任何局部尖峰/谷值都不会被平均抹平。

纵轴范围：截尾鲁棒范围。先求出每列的 min/max 两个序列并排序，
默认从两端各截掉 round(m * clip_percentile / 100) 列（默认 1%），
以剩余列的最小/最大值作为显示范围。个别离群点只会被裁到边界
并以 '^' / 'v' 标记，不会把曲线压成一条线。若截尾后的范围退化
（宽度为 0，例如“全零 + 单尖峰”），自动回退到完整数据范围，
保证尖峰仍然完整可见。clip_percentile=0 可关闭裁剪。
"""

__all__ = ["render", "bin_minmax", "choose_range"]

_ASCII_FILL = "#"
_CLIP_UP = "^"
_CLIP_DOWN = "v"


def _fmt(value):
    """紧凑数值格式化，用于刻度与范围说明。"""
    if value == 0:
        return "0"
    av = abs(value)
    if av >= 1e6 or av < 1e-4:
        mant, exp = f"{value:.3e}".split("e")
        return mant.rstrip("0").rstrip(".") + "e" + exp
    return f"{value:.4g}"


def bin_minmax(data, width):
    """把序列分桶为 width 列，返回 (cols_min, cols_max)。

    第 i 列覆盖区间 [ceil(i*n/width), ceil((i+1)*n/width))，
    与“点 j 落在第 j*width//n 列”的映射一致，边界不重不漏。
    空桶（n < width 时）对应位置为 None。
    时间 O(n)，额外内存 O(width)（切片拷贝为瞬时分摊 O(n/width)）。
    """
    n = len(data)
    cols_min = []
    cols_max = []
    for i in range(width):
        a = -(-i * n // width)  # ceil(i*n/width)
        b = -(-(i + 1) * n // width)
        if b <= a:
            cols_min.append(None)
            cols_max.append(None)
        else:
            seg = data[a:b]
            cols_min.append(min(seg))
            cols_max.append(max(seg))
    return cols_min, cols_max


def choose_range(cols_min, cols_max, clip_percentile=1.0):
    """根据每列极值选取显示范围。

    返回 (lo, hi, full_lo, full_hi, clipped)。
    - 从列极值序列两端各截掉 round(m*pct/100) 列，抑制离群点；
    - 截尾范围退化（hi <= lo）时回退到完整范围；
    - 完全退化（常数序列）时围绕该值对称扩出非零量程。
    """
    mins = sorted(v for v in cols_min if v is not None)
    maxs = sorted(v for v in cols_max if v is not None)
    full_lo, full_hi = mins[0], maxs[-1]

    m = len(mins)
    k = min(int(0.5 + m * clip_percentile / 100.0), (m - 1) // 2)
    lo = mins[k]
    hi = maxs[m - 1 - k]
    if hi - lo <= 0:  # 退化：如全零序列里的单尖峰，回退完整范围
        lo, hi = full_lo, full_hi
        clipped = False
    else:
        if k > 0:
            # 小边距吸收贴近边界的正常列；真正的离群点远超边距仍被裁剪
            margin = (hi - lo) * 0.02
            lo -= margin
            hi += margin
        clipped = lo > full_lo or hi < full_hi

    if hi <= lo:  # 常数序列：对称扩量程，避免除零
        pad = abs(hi) * 0.5 if hi != 0 else 0.5
        lo, hi = hi - pad, hi + pad
        clipped = False
    return lo, hi, full_lo, full_hi, clipped


def _row_of(value, lo, hi, height):
    """把数值映射到行号（0 在顶部），越界裁剪到边界。"""
    r = int(round((hi - value) / (hi - lo) * (height - 1)))
    if r < 0:
        return 0
    if r > height - 1:
        return height - 1
    return r


def render(data, width=80, height=16, clip_percentile=1.0):
    """把数值序列渲染为等宽文本波形图，返回字符串。

    参数：
        data: 数值序列（list/tuple/array 等，迭代器会被物化）
        width: 图宽（列数），>= 1
        height: 图高（行数），>= 2
        clip_percentile: 鲁棒裁剪百分位，0 表示使用完整数据范围
    """
    if width < 1:
        raise ValueError("width must be >= 1")
    if height < 2:
        raise ValueError("height must be >= 2")
    if not 0 <= clip_percentile < 50:
        raise ValueError("clip_percentile must be in [0, 50)")
    try:
        n = len(data)
    except TypeError:
        data = list(data)
        n = len(data)
    if n == 0:
        raise ValueError("empty sequence")

    cols_min, cols_max = bin_minmax(data, width)
    lo, hi, full_lo, full_hi, clipped = choose_range(
        cols_min, cols_max, clip_percentile
    )

    grid = [[" "] * width for _ in range(height)]
    clip_up = clip_down = 0
    for c in range(width):
        cmin, cmax = cols_min[c], cols_max[c]
        if cmin is None:
            continue
        if cmax > hi:
            clip_up += 1
        if cmin < lo:
            clip_down += 1
        r_top = _row_of(cmax, lo, hi, height)
        r_bot = _row_of(cmin, lo, hi, height)
        for r in range(r_top, r_bot + 1):
            grid[r][c] = _ASCII_FILL
        if cmax > hi:
            grid[0][c] = _CLIP_UP
        if cmin < lo:
            grid[height - 1][c] = _CLIP_DOWN

    header = (
        f"n={n}  y-range=[{_fmt(lo)}, {_fmt(hi)}]"
        f"  data=[{_fmt(full_lo)}, {_fmt(full_hi)}]"
    )
    if clipped:
        header += f"  clipped: {clip_up} col(s) above, {clip_down} below"

    labels = [
        _fmt(hi - (hi - lo) * r / (height - 1)) for r in range(height)
    ]
    label_w = max(len(s) for s in labels)
    lines = [header]
    for r in range(height):
        lines.append(labels[r].rjust(label_w) + " |" + "".join(grid[r]))
    return "\n".join(lines)
