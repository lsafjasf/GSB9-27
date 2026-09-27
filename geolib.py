"""geolib: 地图投影与球面度量库（仅标准库）。

包含：
- 等距圆柱投影（Equirectangular）与等积圆柱投影（Lambert Cylindrical
  Equal-Area），均支持正/逆投影，往返误差可量化（见 test_geolib.py）。
- 大圆距离（对零距离与近对跖点均数值稳定的 atan2 形式）。
- 球面多边形面积：边按大圆段解释，用球面角盈（Girard）向量法计算，
  天然免疫日期变更线 / 极点 / 跨半球问题，结果恒非负。
- 球面三角形有向立体角（Van Oosterom-Strackee 公式），供独立交叉校验。

角度单位：对外接口一律使用「度」（经度 [-180, 180]，纬度 [-90, 90]）。
投影坐标单位：米（x 向东，y 向北，原点为 (lon0, 0)）。
"""

import math

__all__ = [
    "MEAN_RADIUS",
    "Equirectangular",
    "CylindricalEqualArea",
    "great_circle_distance",
    "spherical_polygon_area",
    "spherical_triangle_area",
    "wrap_longitude",
]

# IUGG 平均半径（米），球面度量统一使用该半径。
MEAN_RADIUS = 6371008.8

_TWO_PI = 2.0 * math.pi


def wrap_longitude(lon):
    """把经度（度）规整到 [-180, 180)。"""
    return (lon + 180.0) % 360.0 - 180.0


def _wrap_delta_lon_rad(dlon):
    """把经度差（弧度）规整到 [-pi, pi)，保证走「短边」而非绕远路。"""
    return (dlon + math.pi) % _TWO_PI - math.pi


def _to_vec(lon, lat):
    """(lon, lat) 度 -> 地心单位向量 (x, y, z)。"""
    lam = math.radians(lon)
    phi = math.radians(lat)
    cp = math.cos(phi)
    return (cp * math.cos(lam), cp * math.sin(lam), math.sin(phi))


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


class Equirectangular:
    """等距圆柱投影（Equirectangular / Plate Carrée 的一般形式）。

    参数：
        lon0   中央经线（度），默认 0。
        lat1   标准纬线（度），默认 0。该纬线上无长度变形；
               东西向比例按 cos(lat1) 整体缩放。
        radius 球半径（米），默认 IUGG 平均半径。

    正投影：x = R*(lon-lon0)*cos(lat1)，y = R*lat（经纬度取弧度）。
    性质：子午线方向等距；构造简单、正反算闭合。
    适用：以 lat1 为中心的中小范围区域图；全球图仅适合示意，
          高纬度东西向变形大（既不等积也不保角）。
    """

    def __init__(self, lon0=0.0, lat1=0.0, radius=MEAN_RADIUS):
        if not -90.0 <= lat1 <= 90.0:
            raise ValueError("lat1 必须在 [-90, 90] 内")
        self.lon0 = float(lon0)
        self.lat1 = float(lat1)
        self.radius = float(radius)
        self._cos_lat1 = math.cos(math.radians(lat1))

    def forward(self, lon, lat):
        """(lon, lat) 度 -> (x, y) 米。"""
        dlon = _wrap_delta_lon_rad(math.radians(lon) - math.radians(self.lon0))
        x = self.radius * dlon * self._cos_lat1
        y = self.radius * math.radians(lat)
        return x, y

    def inverse(self, x, y):
        """(x, y) 米 -> (lon, lat) 度。"""
        lat = math.degrees(y / self.radius)
        if lat > 90.0 + 1e-9 or lat < -90.0 - 1e-9:
            raise ValueError("y 超出可逆范围（|lat| > 90）")
        # 正投影产生的浮点抖动可能使 |lat| 略超 90，钳制即可。
        lat = max(-90.0, min(90.0, lat))
        lon = self.lon0 + math.degrees(x / (self.radius * self._cos_lat1))
        return wrap_longitude(lon), lat


class CylindricalEqualArea:
    """等积圆柱投影（Lambert Cylindrical Equal-Area）。

    参数：
        lon0   中央经线（度），默认 0。
        lat1   标准纬线（度），默认 0。lat1=0 即经典 Lambert 等积圆柱；
               非零 lat1 为割线形式（如 Gall-Peters 取 lat1=45）。
        radius 球半径（米）。

    正投影：x = R*(lon-lon0)*cos(lat1)，y = R*sin(lat)/cos(lat1)。
    性质：严格等积，全球范围面积无系统误差；代价是形状/角度变形，
          lat1=0 时高纬度被显著压扁。
    适用：需要面积对比的全球/大区域专题图（分布密度、土地利用等）。
    注意：极点处 y = ±R/cos(lat1)，有限且可逆，极点可精确往返。
    """

    def __init__(self, lon0=0.0, lat1=0.0, radius=MEAN_RADIUS):
        if not -90.0 <= lat1 <= 90.0:
            raise ValueError("lat1 必须在 [-90, 90] 内")
        if abs(abs(lat1) - 90.0) < 1e-12:
            raise ValueError("lat1 不能为 ±90（cos(lat1)=0，投影退化）")
        self.lon0 = float(lon0)
        self.lat1 = float(lat1)
        self.radius = float(radius)
        self._cos_lat1 = math.cos(math.radians(lat1))

    def forward(self, lon, lat):
        """(lon, lat) 度 -> (x, y) 米。"""
        dlon = _wrap_delta_lon_rad(math.radians(lon) - math.radians(self.lon0))
        x = self.radius * dlon * self._cos_lat1
        y = self.radius * math.sin(math.radians(lat)) / self._cos_lat1
        return x, y

    def inverse(self, x, y):
        """(x, y) 米 -> (lon, lat) 度。"""
        s = y * self._cos_lat1 / self.radius
        # 浮点抖动可能使 |s| 略大于 1，钳制后再 asin。
        s = max(-1.0, min(1.0, s))
        lat = math.degrees(math.asin(s))
        lon = self.lon0 + math.degrees(x / (self.radius * self._cos_lat1))
        return wrap_longitude(lon), lat


def great_circle_distance(lon1, lat1, lon2, lat2, radius=MEAN_RADIUS):
    """大圆距离（米）。

    使用 atan2 形式的球面张角公式（球面 Vincenty 公式）：
    对零距离、近对跖点均数值稳定，恒取 <= pi 的张角，不会绕远路。
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlon = _wrap_delta_lon_rad(math.radians(lon2) - math.radians(lon1))
    cos_phi1 = math.cos(phi1)
    cos_phi2 = math.cos(phi2)
    sin_dlon = math.sin(dlon)
    cos_dlon = math.cos(dlon)
    num = math.hypot(cos_phi2 * sin_dlon,
                     cos_phi1 * math.sin(phi2) - math.sin(phi1) * cos_phi2 * cos_dlon)
    den = math.sin(phi1) * math.sin(phi2) + cos_phi1 * cos_phi2 * cos_dlon
    return radius * math.atan2(num, den)


def spherical_triangle_area(a, b, c, radius=MEAN_RADIUS):
    """球面三角形面积（平方米），顶点为 (lon, lat) 度，边为大圆段。

    用 Van Oosterom-Strackee 有向立体角公式取绝对值，数值稳定。
    """
    va = _to_vec(*a)
    vb = _to_vec(*b)
    vc = _to_vec(*c)
    num = _dot(_cross(va, vb), vc)
    den = 1.0 + _dot(va, vb) + _dot(vb, vc) + _dot(va, vc)
    return abs(2.0 * math.atan2(num, den)) * radius * radius


def _signed_solid_angle(va, vb, vc):
    """三角形 (va, vb, vc) 的有向立体角（球面度），供交叉校验使用。"""
    num = _dot(_cross(va, vb), vc)
    den = 1.0 + _dot(va, vb) + _dot(vb, vc) + _dot(va, vc)
    return 2.0 * math.atan2(num, den)


def spherical_polygon_area(coords, radius=MEAN_RADIUS):
    """球面多边形面积（平方米），恒 >= 0。

    coords: [(lon, lat), ...] 度，按边界顺序（顺/逆时针均可，结果取绝对值）。
    边按大圆段解释。算法：选取球内参考点（顶点向量和的方向，退化时回退
    到极点/轴向），对每条边累加有向立体角 Ω(ref, v_i, v_i+1)
    （Van Oosterom-Strackee 公式）。有向面积在闭合边界上精确抵消，
    对任意简单多边形（含非凸）严格成立。全程使用 3D 向量，因此：
    - 跨 ±180° 日期变更线无特殊分支，自动走短边；
    - 顶点落在极点、多边形包含极点、跨南北半球均有确定结果；
    - 结果若超过半球面积则取补（返回两侧区域中较小者）。

    退化情形：少于 3 个有效顶点（单点、零长度线、重复点）面积为 0。
    """
    n = len(coords)
    if n < 3:
        return 0.0
    pts = [_to_vec(lon, lat) for lon, lat in coords]
    # 去除相邻重复点（含首尾），零长度边不产生贡献。
    verts = []
    for p in pts:
        if not verts or _dot(p, verts[-1]) < 1.0 - 1e-18:
            verts.append(p)
    if len(verts) > 1 and _dot(verts[0], verts[-1]) >= 1.0 - 1e-18:
        verts.pop()
    m = len(verts)
    if m < 3:
        return 0.0

    # 参考点候选：顶点向量和的方向（通常落在多边形内部），
    # 退化（如赤道环顶点对称抵消）时回退到固定轴向。
    sx = sum(p[0] for p in verts)
    sy = sum(p[1] for p in verts)
    sz = sum(p[2] for p in verts)
    norm = math.sqrt(sx * sx + sy * sy + sz * sz)
    candidates = []
    if norm > 1e-12:
        candidates.append((sx / norm, sy / norm, sz / norm))
    candidates += [(0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]

    total = None
    for ref in candidates:
        acc = 0.0
        degenerate = False
        for i in range(m):
            a = verts[i]
            b = verts[(i + 1) % m]
            den = 1.0 + _dot(ref, a) + _dot(a, b) + _dot(b, ref)
            if abs(den) < 1e-14:
                degenerate = True  # 参考点与边对跖配置，换下一个候选
                break
            acc += 2.0 * math.atan2(_dot(_cross(ref, a), b), den)
        if not degenerate:
            total = acc
            break
    if total is None:  # 理论上不可达：三个正交轴向不可能同时退化
        raise ValueError("多边形退化：无法确定参考点")

    area = abs(total) * radius * radius
    sphere = 4.0 * math.pi * radius * radius
    if area > 0.5 * sphere:
        area = sphere - area
    return area
