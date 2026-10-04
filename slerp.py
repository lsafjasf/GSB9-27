"""Spherical (great-circle / slerp) interpolation between two points on a sphere.

Only the Python 3 standard library is used (``math``).

Input coordinates are geographic (lat, lon) in degrees:
    lat in [-90, 90], lon in [-180, 180) (other ranges accepted, normalised).

The interpolation follows the great circle (geodesic), i.e. the shortest
route.  It is implemented by converting the points to 3-D unit vectors and
applying spherical linear interpolation (slerp), so wrapping longitude at
+/-180 deg and the pole singularity are handled automatically -- angles are
never interpolated directly.

Degenerate cases
----------------
* Coincident points (central angle == 0):
  the unique "path" is the point itself; all samples equal it and every
  segment has length 0.

* Antipodal points (central angle == pi):
  infinitely many great-circle routes of equal (maximal) length pi*R exist,
  so no unique shortest path is defined.  We still emit a deterministic one:
  take the vector n = cross(a, reference) normalised (reference = z-axis,
  falling back to x-axis if a is parallel to z), which is perpendicular to a,
  and walk p(t) = a*cos(pi*t) + n*sin(pi*t).  This is one valid great-circle
  route through the antipode.  Callers needing a different tie-break should
  split the route at an explicit intermediate waypoint.
"""

import math

EARTH_RADIUS_KM = 6371.0088  # mean Earth radius (IUGG R1)


def _normalize_lon(lon_deg):
    """Wrap longitude to [-180, 180)."""
    lon = (lon_deg + 180.0) % 360.0 - 180.0
    return 180.0 if lon == -180.0 else lon


def latlon_to_unit(lat_deg, lon_deg):
    """Geographic (lat, lon) degrees -> 3-D unit vector (x, y, z)."""
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    cos_lat = math.cos(lat)
    return (
        cos_lat * math.cos(lon),
        cos_lat * math.sin(lon),
        math.sin(lat),
    )


def unit_to_latlon(v):
    """3-D vector (need not be exactly unit) -> (lat, lon) degrees."""
    x, y, z = v
    norm = math.sqrt(x * x + y * y + z * z)
    if norm == 0.0:
        raise ValueError("zero vector cannot be converted to lat/lon")
    nx, ny, nz = x / norm, y / norm, z / norm
    nz = max(-1.0, min(1.0, nz))
    lat = math.degrees(math.asin(nz))
    # At the poles longitude is undefined; fix it to 0 for determinism.
    lon = 0.0 if abs(nz) >= 1.0 - 1e-14 else _normalize_lon(
        math.degrees(math.atan2(ny, nx))
    )
    return lat, lon


def central_angle(a, b):
    """Central angle (radians, [0, pi]) between two unit vectors."""
    cross_norm = math.sqrt(
        (a[1] * b[2] - a[2] * b[1]) ** 2
        + (a[2] * b[0] - a[0] * b[2]) ** 2
        + (a[0] * b[1] - a[1] * b[0]) ** 2
    )
    dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
    return math.atan2(cross_norm, dot)


def _slerp(a, b, t, omega):
    """Unit-vector slerp for 0 < omega < pi."""
    s = math.sin(omega)
    c0 = math.sin((1.0 - t) * omega) / s
    c1 = math.sin(t * omega) / s
    return (
        c0 * a[0] + c1 * b[0],
        c0 * a[1] + c1 * b[1],
        c0 * a[2] + c1 * b[2],
    )


def _antipodal_direction(a):
    """Deterministic unit vector perpendicular to a (used for antipodes)."""
    z_axis = (0.0, 0.0, 1.0)
    x_axis = (1.0, 0.0, 0.0)
    ref = x_axis if abs(a[2]) > 0.9 else z_axis
    n = (
        a[1] * ref[2] - a[2] * ref[1],
        a[2] * ref[0] - a[0] * ref[2],
        a[0] * ref[1] - a[1] * ref[0],
    )
    nlen = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2)
    return (n[0] / nlen, n[1] / nlen, n[2] / nlen)


def great_circle_path(lat1, lon1, lat2, lon2, num_points=11,
                      radius=EARTH_RADIUS_KM):
    """Interpolate along the great circle between two geographic points.

    Returns a dict with:
        points    : list of (lat, lon, t) tuples, t in [0, 1], uniform
        vectors   : corresponding 3-D unit vectors
        distances : list of great-circle segment lengths (same units as
                    ``radius``), len == num_points - 1
        total_distance, central_angle_rad/deg
        kind      : "coincident" | "antipodal" | "normal"

    ``num_points`` includes both endpoints, so it must be >= 2.
    """
    if num_points < 2:
        raise ValueError("num_points must be >= 2")

    a = latlon_to_unit(lat1, lon1)
    b = latlon_to_unit(lat2, lon2)
    omega = central_angle(a, b)
    n_segments = num_points - 1

    if omega < 1e-12:
        kind = "coincident"
        plat, plon = unit_to_latlon(a)
        vectors = [a] * num_points
        points = [(plat, plon, i / n_segments) for i in range(num_points)]
        distances = [0.0] * n_segments
    elif math.pi - omega < 1e-12:
        kind = "antipodal"
        n = _antipodal_direction(a)
        vectors, points = [], []
        for i in range(num_points):
            t = i / n_segments
            v = (
                a[0] * math.cos(math.pi * t) + n[0] * math.sin(math.pi * t),
                a[1] * math.cos(math.pi * t) + n[1] * math.sin(math.pi * t),
                a[2] * math.cos(math.pi * t) + n[2] * math.sin(math.pi * t),
            )
            vectors.append(v)
            plat, plon = unit_to_latlon(v)
            points.append((plat, plon, t))
        distances = [
            radius * central_angle(vectors[i], vectors[i + 1])
            for i in range(n_segments)
        ]
    else:
        kind = "normal"
        vectors, points = [], []
        for i in range(num_points):
            t = i / n_segments
            v = _slerp(a, b, t, omega)
            vectors.append(v)
            plat, plon = unit_to_latlon(v)
            points.append((plat, plon, t))
        # Uniform slerp spacing makes every segment exactly omega/n.
        distances = [radius * omega / n_segments] * n_segments

    return {
        "kind": kind,
        "points": points,
        "vectors": vectors,
        "distances": distances,
        "total_distance": radius * omega,
        "central_angle_rad": omega,
        "central_angle_deg": math.degrees(omega),
        "radius": radius,
    }


def haversine_km(lat1, lon1, lat2, lon2, radius=EARTH_RADIUS_KM):
    """Independent great-circle distance check via the haversine formula."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    h = (math.sin(dphi / 2.0) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2.0) ** 2)
    return 2.0 * radius * math.asin(min(1.0, math.sqrt(h)))
