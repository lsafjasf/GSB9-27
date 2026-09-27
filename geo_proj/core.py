"""核心实现：圆柱投影、大圆距离、球面多边形面积。

设计约定
--------
* 对外角度单位为十进制度；坐标顺序统一为 (lat, lon)。
* 所有经度差都先归一化到 [-180, 180]（或弧度 [-pi, pi]），
  因此跨日期变更线（±180°）不会出现绕远路。
* 多边形首尾不需要重复；边被解释为两点间的“最短大圆弧”。
"""

import math

# IUGG 地球平均半径 R1=(2a+b)/3，单位 m；面积/距离误差对比用它。
WGS84_A = 6378137.0
WGS84_F = 1.0 / 298.257223563
EARTH_MEAN_RADIUS = 6371008.8

_TWO_PI = 2.0 * math.pi
_DEG = math.pi / 180.0


def clamp_lon(lon):
    """把任意经度归一化到 (-180, 180]。"""
    lon = ((lon + 180.0) % 360.0) - 180.0
    return 180.0 if lon == -180.0 else lon


def wrap_delta_deg(d):
    """经度差归一化到 [-180, 180)：+190 -> -170。"""
    return ((d + 180.0) % 360.0) - 180.0


def wrap_delta_rad(d):
    """弧度版经度差归一化到 [-pi, pi)。"""
    return ((d + math.pi) % _TWO_PI) - math.pi


def _check_lat(lat):
    if not -90.0 <= lat <= 90.0:
        raise ValueError("lat 超出 [-90, 90]: %r" % (lat,))
    return lat


class Equirectangular:
    r"""等距圆柱投影（Equirectangular / Plate Carrée）。

    公式（phi0 基准纬度，phi1 标准纬线，lambda0 中央经线，R 球体半径）::

        x = R * (lambda - lambda0) * cos(phi1)
        y = R * (phi - phi0)

    参数
    ----
    lat0 : 基准纬度 phi0（度），默认 0
    lon0 : 中央经线 lambda0（度），默认 0
    lat_std : 标准纬线 phi1（度），默认 0（此时即 Plate Carrée）
    radius : 地球半径（m）

    性质与适用范围
    --------------
    * 沿所有经线长度不变形（沿经线 1° 恒为 R*pi/180）；
    * 仅在标准纬线 phi1 上无长度/角度/面积变形，越远离变形越大，
      高纬地区东西方向被拉伸，面积被放大 1/cos(phi) 倍；
    * 适合低纬、小范围栅格索引、简单可视化；不适合高纬大范围量算。
    """

    name = "equirectangular"

    def __init__(self, lat0=0.0, lon0=0.0, lat_std=0.0, radius=EARTH_MEAN_RADIUS):
        _check_lat(lat0)
        _check_lat(lat_std)
        self.lat0 = float(lat0)
        self.lon0 = float(lon0)
        self.lat_std = float(lat_std)
        self.radius = float(radius)
        self._cos = math.cos(self.lat_std * _DEG)

    def forward(self, lat, lon):
        """经纬度 -> 平面米坐标 (x, y)。"""
        _check_lat(lat)
        x = self.radius * wrap_delta_rad((lon - self.lon0) * _DEG) * self._cos
        y = self.radius * (lat - self.lat0) * _DEG
        return x, y

    def inverse(self, x, y):
        """平面米坐标 -> (lat, lon)，经度归一化到 (-180,180]。"""
        lat = y / self.radius / _DEG + self.lat0
        # 往返浮点误差可能使极点略微越界，钳回 [-90, 90]
        lat = max(-90.0, min(90.0, lat))
        lon = x / (self.radius * self._cos) / _DEG + self.lon0
        return lat, clamp_lon(lon)


class LambertCylindricalEqualArea:
    r"""等积圆柱投影（Lambert Cylindrical Equal-Area）。

    公式（phi_std 标准纬线）::

        x = R * (lambda - lambda0) * cos(phi_std)
        y = R * sin(phi) / cos(phi_std)

    参数
    ----
    lat_std : 标准纬线（度），默认 0（此时即 Lambert 等积圆柱原型）；
              常见取值还有 30°（Gall-Peters 为 45°，此处通用公式均可表达）。
    lon0 : 中央经线（度）
    radius : 地球半径（m）

    性质与适用范围
    --------------
    * 严格等积：任意区域投影后面积不变（球体面积意义下）；
    * 形状/角度在标准纬线以外畸变明显，高纬被竖向压扁；
    * 适合以面积统计为目的的世界图、密度/分布制图；
      不适合量距离、量角度或导航。
    """

    name = "lambert_cylindrical_equal_area"

    def __init__(self, lat_std=0.0, lon0=0.0, radius=EARTH_MEAN_RADIUS):
        _check_lat(lat_std)
        cos = math.cos(lat_std * _DEG)
        if abs(cos) < 1e-12:
            raise ValueError("标准纬线过近极点，投影退化")
        self.lat_std = float(lat_std)
        self.lon0 = float(lon0)
        self.radius = float(radius)
        self._cos = cos

    def forward(self, lat, lon):
        _check_lat(lat)
        x = self.radius * wrap_delta_rad((lon - self.lon0) * _DEG) * self._cos
        y = self.radius * math.sin(lat * _DEG) / self._cos
        return x, y

    def inverse(self, x, y):
        sin_phi = y * self._cos / self.radius
        # 往返浮点误差可能略微越界
        if sin_phi > 1.0:
            sin_phi = 1.0
        elif sin_phi < -1.0:
            sin_phi = -1.0
        lat = math.asin(sin_phi) / _DEG
        lon = x / (self.radius * self._cos) / _DEG + self.lon0
        return lat, clamp_lon(lon)


def great_circle_distance(lat1, lon1, lat2, lon2, radius=EARTH_MEAN_RADIUS):
    """两点大圆距离（m）。经度差自动跨 ±180° 取最短弧。

    采用 Vincenty 球面公式（atan2 形式），从零长度到对跖点
    全范围数值稳定（haversine 在对跖点附近有分米级舍入误差）。
    """
    _check_lat(lat1)
    _check_lat(lat2)
    p1 = lat1 * _DEG
    p2 = lat2 * _DEG
    dl = wrap_delta_rad((lon2 - lon1) * _DEG)
    sp1, cp1 = math.sin(p1), math.cos(p1)
    sp2, cp2 = math.sin(p2), math.cos(p2)
    sdl, cdl = math.sin(dl), math.cos(dl)
    num = math.hypot(cp2 * sdl, cp1 * sp2 - sp1 * cp2 * cdl)
    den = sp1 * sp2 + cp1 * cp2 * cdl
    return radius * math.atan2(num, den)


# 兼容别名：接口上仍提供 haversine 这一惯用名
haversine = great_circle_distance


def path_length(points, radius=EARTH_MEAN_RADIUS):
    """大圆弧折线总长（m）。points 为 (lat, lon) 序列。"""
    total = 0.0
    for (lat1, lon1), (lat2, lon2) in zip(points, points[1:]):
        total += haversine(lat1, lon1, lat2, lon2, radius)
    return total


def _to_vec(lat, lon):
    p = lat * _DEG
    l = lon * _DEG
    cp = math.cos(p)
    return (cp * math.cos(l), cp * math.sin(l), math.sin(p))


def spherical_polygon_area(points, radius=EARTH_MEAN_RADIUS):
    """球面多边形面积（m²），边按最短大圆弧解释。

    算法：把顶点转到地心单位矢量，用扇形三角剖分累加每个三角形的
    有向球面盈角（van Oosterom–Strackee 公式，atan2 形式，数值稳健）::

        E = Σ 2 * atan2(a·(b×c), 1 + a·b + b·c + c·a)
        A = |E| * R²

    性质：

    * 全程在三维直角坐标下计算，与经度表示无关，跨日期变更线天然正确；
    * 结果恒为非负；
    * 含极点、跨半球的多边形有确定结果；
    * 面积统一取较小一侧（≤ 2πR²，即一个半球），对覆盖超过半球的
      输入给出确定且无歧义的答案；
    * 少于 3 个顶点（点、线）面积为 0。
    """
    n = len(points)
    if n < 3:
        return 0.0
    v = [_to_vec(lat, lon) for lat, lon in points]
    a = v[0]
    excess = 0.0
    for i in range(1, n - 1):
        b = v[i]
        c = v[i + 1]
        cross = (
            b[1] * c[2] - b[2] * c[1],
            b[2] * c[0] - b[0] * c[2],
            b[0] * c[1] - b[1] * c[0],
        )
        triple = a[0] * cross[0] + a[1] * cross[1] + a[2] * cross[2]
        ab = a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
        bc = b[0] * c[0] + b[1] * c[1] + b[2] * c[2]
        ca = c[0] * a[0] + c[1] * a[1] + c[2] * a[2]
        denom = 1.0 + ab + bc + ca
        if denom == 0.0 and triple == 0.0:
            # 三角形恰好张成半球，盈角 2π，对求和无净贡献的方向歧义；
            # 跳过以保持确定性（仅出现在退化输入）。
            continue
        excess += 2.0 * math.atan2(triple, denom)
    area = abs(excess) * radius * radius
    hemisphere = _TWO_PI * radius * radius
    if area > hemisphere:
        area = 2.0 * hemisphere - area
    return area


def planar_polygon_area(points, lat_ref=None, radius=EARTH_MEAN_RADIUS):
    """局部等距圆柱平面近似面积（m²），用于与球面面积对比误差。

    x = R λ cos(φ_ref)，y = R φ；经度按边逐步展开（unwrap），
    因此跨日期变更线的环也能得到确定的平面结果。
    lat_ref 默认取多边形纬度均值。
    """
    if len(points) < 3:
        return 0.0
    if lat_ref is None:
        lat_ref = sum(p[0] for p in points) / len(points)
    k = radius * math.cos(lat_ref * _DEG)

    # 逐步展开经度，消除 ±180° 跳变
    lons = [points[0][1] * _DEG]
    for (_, lon1), (_, lon2) in zip(points, points[1:]):
        lons.append(lons[-1] + wrap_delta_rad((lon2 - lon1) * _DEG))
    ys = [lat * _DEG * radius for lat, _ in points]
    xs = [lon * k for lon in lons]

    s = 0.0
    n = len(points)
    for i in range(n):
        j = (i + 1) % n
        s += xs[i] * ys[j] - xs[j] * ys[i]
    return abs(s) * 0.5


def roundtrip_errors(projection, lats=None, lons=None):
    """量化投影往返误差：对经纬网格 forward -> inverse，

    返回 (max_err_m, mean_err_m, n)，误差用原始点与往返点间大圆距离度量。
    """
    if lats is None:
        lats = range(-89, 90, 2)
    if lons is None:
        lons = range(-180, 180, 5)
    max_err = 0.0
    sum_err = 0.0
    n = 0
    for lat in lats:
        for lon in lons:
            lat2, lon2 = projection.inverse(*projection.forward(lat, lon))
            err = haversine(lat, lon, lat2, lon2, projection.radius)
            max_err = max(max_err, err)
            sum_err += err
            n += 1
    return max_err, sum_err / n, n
