"""地形高度图库：任意点高度插值、坡度与法线计算、空洞（无数据）处理。

仅使用 Python 标准库。

插值方式与取舍
--------------
提供两种插值器，法线均由插值曲面的解析偏导导出（不是相邻点差分）：

1. ``bilinear`` 双线性插值（分片双曲抛物面）
   - 每个网格单元内是一张双线性曲面，C0 连续（高度连续），
     但跨单元边的一阶导数不连续，法线在单元边上会有跳变。
   - 优点：只需 4 个角点，对空洞的"污染范围"最小（仅缺失角点所在单元）。
2. ``bicubic`` 双三次 Catmull-Rom 插值（分片三次曲面，默认）
   - C1 连续：跨单元边高度与一阶导数均连续，法线全场连续无跳变。
   - 能精确复现线性曲面；对光滑曲面的逼近阶更高，误差显著小于双线性。
   - 代价：需要 4x4 邻域，靠近空洞时更容易返回无数据；
     边界处缺失的 stencil 点用线性外推（幻影点）补齐，
     保证边界单元同样精确复现平面且导数连续。

空洞处理
--------
数据中 ``None`` 或 ``NaN`` 视为无数据。查询时若所需邻域内含有空洞，
返回 ``Status.NODATA`` 且高度为 ``None``，绝不以 0 代替。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Sequence, Tuple

__all__ = ["Heightmap", "QueryResult", "Status"]


class Status(Enum):
    OK = "ok"                    # 查询成功
    NODATA = "nodata"            # 位置在网格内，但插值邻域含空洞
    OUT_OF_BOUNDS = "out_of_bounds"  # 位置在网格范围外


@dataclass
class QueryResult:
    """单点查询结果。status != OK 时各数值字段为 None。"""
    status: Status
    height: Optional[float] = None                    # 高度
    normal: Optional[Tuple[float, float, float]] = None  # 单位法线 (nx, ny, nz)
    slope_deg: Optional[float] = None                 # 坡度（度）

    @property
    def ok(self) -> bool:
        return self.status is Status.OK


def _is_void(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _cr_weights(t: float) -> Tuple[float, float, float, float]:
    """Catmull-Rom 基函数（值）。"""
    t2 = t * t
    t3 = t2 * t
    return (
        -0.5 * t3 + t2 - 0.5 * t,
        1.5 * t3 - 2.5 * t2 + 1.0,
        -1.5 * t3 + 2.0 * t2 + 0.5 * t,
        0.5 * t3 - 0.5 * t2,
    )


def _cr_dweights(t: float) -> Tuple[float, float, float, float]:
    """Catmull-Rom 基函数对 t 的导数。"""
    t2 = t * t
    return (
        -1.5 * t2 + 2.0 * t - 0.5,
        4.5 * t2 - 5.0 * t,
        -4.5 * t2 + 4.0 * t + 0.5,
        1.5 * t2 - t,
    )


class Heightmap:
    """规则网格高度图。

    data[j][i] 为节点高度，j 为 y 方向行号、i 为 x 方向列号；
    节点世界坐标 x = origin[0] + i * cell_size，y = origin[1] + j * cell_size。
    空洞用 None 或 float('nan') 表示。
    """

    BILINEAR = "bilinear"
    BICUBIC = "bicubic"

    def __init__(
        self,
        data: Sequence[Sequence[Optional[float]]],
        cell_size: float = 1.0,
        origin: Tuple[float, float] = (0.0, 0.0),
    ) -> None:
        if cell_size <= 0:
            raise ValueError("cell_size 必须为正数")
        rows = [list(r) for r in data]
        if len(rows) < 2 or any(len(r) != len(rows[0]) for r in rows) or len(rows[0]) < 2:
            raise ValueError("data 必须是至少 2x2 的矩形网格")
        self._z: List[List[Optional[float]]] = [
            [None if _is_void(v) else float(v) for v in row] for row in rows
        ]
        self.ny = len(rows)
        self.nx = len(rows[0])
        self.cell = float(cell_size)
        self.ox, self.oy = float(origin[0]), float(origin[1])

    # ---- 基本属性 ----

    @property
    def bounds(self) -> Tuple[float, float, float, float]:
        """(xmin, ymin, xmax, ymax) 可查询范围。"""
        return (
            self.ox,
            self.oy,
            self.ox + (self.nx - 1) * self.cell,
            self.oy + (self.ny - 1) * self.cell,
        )

    def node(self, i: int, j: int) -> Optional[float]:
        """节点 (i, j) 的原始采样值，空洞返回 None。"""
        return self._z[j][i]

    # ---- 内部：定位单元 ----

    def _cell(self, x: float, y: float) -> Optional[Tuple[int, int, float, float]]:
        fx = (x - self.ox) / self.cell
        fy = (y - self.oy) / self.cell
        if fx < 0.0 or fy < 0.0 or fx > self.nx - 1 or fy > self.ny - 1:
            return None
        i = min(int(math.floor(fx)), self.nx - 2)
        j = min(int(math.floor(fy)), self.ny - 2)
        return i, j, fx - i, fy - j

    # ---- 内部：分片曲面求值（返回 z, dz/du, dz/dv；邻域含空洞返回 None）----

    def _bilinear_patch(self, i: int, j: int, u: float, v: float):
        z00 = self._z[j][i]
        z10 = self._z[j][i + 1]
        z01 = self._z[j + 1][i]
        z11 = self._z[j + 1][i + 1]
        if z00 is None or z10 is None or z01 is None or z11 is None:
            return None
        z = (
            z00 * (1 - u) * (1 - v)
            + z10 * u * (1 - v)
            + z01 * (1 - u) * v
            + z11 * u * v
        )
        du = (z10 - z00) * (1 - v) + (z11 - z01) * v
        dv = (z01 - z00) * (1 - u) + (z11 - z10) * u
        return z, du, dv

    def _x_value(self, jy: int, ix: int) -> Optional[float]:
        """取 x 方向 stencil 点的值；越界位置用线性外推（2*a-b）构造幻影点。

        线性外推能精确延续线性曲面，保证边界单元也精确复现平面，
        并维持跨边界单元边的一阶导数连续。依赖空洞外推则返回 None。
        """
        if ix < 0:
            a, b = self._z[jy][0], self._z[jy][1]
            return None if a is None or b is None else 2.0 * a - b
        if ix >= self.nx:
            a, b = self._z[jy][self.nx - 1], self._z[jy][self.nx - 2]
            return None if a is None or b is None else 2.0 * a - b
        return self._z[jy][ix]

    def _bicubic_patch(self, i: int, j: int, u: float, v: float):
        # 先沿 x 对 y 方向 4 个 stencil 行求值；越界行之后用线性外推补。
        cols: List[Optional[float]] = []
        cols_dx: List[Optional[float]] = []
        for k in range(4):
            jy = j - 1 + k
            if 0 <= jy < self.ny:
                vals = [self._x_value(jy, i - 1 + l) for l in range(4)]
                if any(val is None for val in vals):
                    return None
                wu = _cr_weights(u)
                du_w = _cr_dweights(u)
                cols.append(sum(w * val for w, val in zip(wu, vals)))
                cols_dx.append(sum(w * val for w, val in zip(du_w, vals)))
            else:
                cols.append(None)
                cols_dx.append(None)
        # y 方向幻影行：线性外推（2 行的极小网格下两个幻影可同时出现）
        if cols[0] is None:
            cols[0] = 2.0 * cols[1] - cols[2]
            cols_dx[0] = 2.0 * cols_dx[1] - cols_dx[2]
        if cols[3] is None:
            cols[3] = 2.0 * cols[2] - cols[1]
            cols_dx[3] = 2.0 * cols_dx[2] - cols_dx[1]
        wv = _cr_weights(v)
        dv_w = _cr_dweights(v)
        z = sum(w * val for w, val in zip(wv, cols))
        dzdv = sum(w * val for w, val in zip(dv_w, cols))
        dzdu = sum(w * val for w, val in zip(wv, cols_dx))
        return z, dzdu, dzdv

    # ---- 公开查询接口 ----

    def query(self, x: float, y: float, method: str = BICUBIC) -> QueryResult:
        """查询任意点的高度、单位法线与坡度。

        法线由插值曲面的解析偏导导出：n = normalize(-dz/dx, -dz/dy, 1)。
        """
        loc = self._cell(x, y)
        if loc is None:
            return QueryResult(Status.OUT_OF_BOUNDS)
        i, j, u, v = loc
        if method == self.BILINEAR:
            patch = self._bilinear_patch(i, j, u, v)
        elif method == self.BICUBIC:
            patch = self._bicubic_patch(i, j, u, v)
        else:
            raise ValueError(f"未知插值方式: {method!r}")
        if patch is None:
            return QueryResult(Status.NODATA)
        z, dzdu, dzdv = patch
        gx = dzdu / self.cell
        gy = dzdv / self.cell
        inv_len = 1.0 / math.sqrt(gx * gx + gy * gy + 1.0)
        normal = (-gx * inv_len, -gy * inv_len, inv_len)
        slope = math.degrees(math.atan(math.hypot(gx, gy)))
        return QueryResult(Status.OK, z, normal, slope)

    def height(self, x: float, y: float, method: str = BICUBIC) -> Optional[float]:
        """仅取高度；无数据或越界返回 None。"""
        return self.query(x, y, method).height

    def normal(self, x: float, y: float, method: str = BICUBIC):
        """仅取单位法线；无数据或越界返回 None。"""
        return self.query(x, y, method).normal

    def slope(self, x: float, y: float, method: str = BICUBIC) -> Optional[float]:
        """仅取坡度（度）；无数据或越界返回 None。"""
        return self.query(x, y, method).slope_deg
