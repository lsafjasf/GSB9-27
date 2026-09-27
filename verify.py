"""偏差验证脚本：用解析与采样两种方式验证简化结果的偏差上界。

解析验证：对每个原始顶点，精确计算其到简化后折线的最小距离（点到线段），
           取最大值，必须 <= epsilon。
采样验证：沿原始折线的每条边均匀插值采样，计算采样点到简化折线的最小距离，
           取最大值作为数值上界证据（对采样密度负责）。

用法：
    python3 verify.py [epsilon] [n_points] [closed]
"""

import math
import sys

from simplify import point_segment_distance, simplify


def _polyline_segments(poly, closed):
    m = len(poly)
    segs = [(poly[i], poly[i + 1]) for i in range(m - 1)]
    if closed and m > 2:
        segs.append((poly[-1], poly[0]))
    return segs


def _dist_to_polyline(p, segs):
    return min(point_segment_distance(p, a, b) for a, b in segs)


def verify_analytic(original, result):
    """解析验证：返回 (最大偏差, 最差点的原始索引, 是否满足上界)。"""
    simplified = [original[i] for i in result.kept_indices]
    segs = _polyline_segments(simplified, result.closed)
    kept = set(result.kept_indices)
    max_dev = 0.0
    worst = -1
    for i, p in enumerate(original):
        if i in kept:
            continue
        d = _dist_to_polyline(p, segs)
        if d > max_dev:
            max_dev = d
            worst = i
    tol = max(1e-12, result.epsilon * 1e-9)
    return max_dev, worst, max_dev <= result.epsilon + tol


def verify_sampling(original, result, samples_per_edge=16):
    """采样验证：沿原始折线均匀采样，返回 (最大采样偏差, 是否满足上界)。"""
    simplified = [original[i] for i in result.kept_indices]
    segs = _polyline_segments(simplified, result.closed)
    edges = _polyline_segments(original, result.closed)
    max_dev = 0.0
    for a, b in edges:
        for s in range(samples_per_edge + 1):
            t = s / samples_per_edge
            p = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
            d = _dist_to_polyline(p, segs)
            if d > max_dev:
                max_dev = d
    tol = max(1e-12, result.epsilon * 1e-9)
    return max_dev, max_dev <= result.epsilon + tol


def _demo_curve(n, closed):
    """生成演示轨迹：螺旋 + 噪声扰动（确定性，无随机依赖）。"""
    pts = []
    for i in range(n):
        t = i / max(1, n - 1)
        ang = 4.0 * math.pi * t
        r = 1.0 + 2.0 * t
        x = r * math.cos(ang) + 0.05 * math.sin(50.0 * t)
        y = r * math.sin(ang) + 0.05 * math.cos(37.0 * t)
        pts.append((x, y))
    if closed:
        # 闭合环：单位圆 + 扰动
        pts = []
        for i in range(n):
            ang = 2.0 * math.pi * i / n
            r = 1.0 + 0.1 * math.sin(8.0 * ang)
            pts.append((r * math.cos(ang), r * math.sin(ang)))
    return pts


def main():
    epsilon = float(sys.argv[1]) if len(sys.argv) > 1 else 0.05
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 2000
    closed = len(sys.argv) > 3 and sys.argv[3] in ("1", "true", "closed")

    original = _demo_curve(n, closed)
    result = simplify(original, epsilon, closed=closed)

    print(f"原始点数        : {len(original)}")
    print(f"保留点数        : {len(result.kept_indices)} "
          f"(压缩率 {len(result.kept_indices) / len(original):.2%})")
    print(f"阈值 epsilon    : {result.epsilon}")
    print(f"决策次数        : {len(result.decisions)}")
    if closed:
        print(f"起点(索引0)保留 : {0 in result.kept_indices}")
        print(f"自交段对        : {result.self_intersections or '无'}")

    max_dev_a, worst, ok_a = verify_analytic(original, result)
    print(f"[解析] 最大偏差 = {max_dev_a:.6g} (最差点索引 {worst}) "
          f"<= {result.epsilon} ? {'通过' if ok_a else '失败'}")

    max_dev_s, ok_s = verify_sampling(original, result)
    print(f"[采样] 最大偏差 = {max_dev_s:.6g} (每边 16 采样) "
          f"<= {result.epsilon} ? {'通过' if ok_s else '失败'}")

    if not (ok_a and ok_s):
        sys.exit(1)


if __name__ == "__main__":
    main()
