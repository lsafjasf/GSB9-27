"""Self-tests + verification report for slerp.great_circle_path.

Run:
    python3 selftest            # checks, prints report, writes data/*.csv
    python3 -m unittest selftest -v  # same checks via unittest

Outputs (directory ./data):
    path_<case>.csv  per-case interpolated points + segment distances
    verification.txt human-readable verification report

Only the Python 3 standard library is used.
"""

import csv
import math
import os
import unittest

from slerp import (
    EARTH_RADIUS_KM,
    great_circle_path,
    haversine_km,
    latlon_to_unit,
    unit_to_latlon,
)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
N = 11  # samples including endpoints
TOL_KM = 1e-6


def naive_angle_path(lat1, lon1, lat2, lon2, num_points=N):
    """Naive linear interpolation of lat/lon -- the deliberately wrong way.

    Longitude is taken as-is (no unwrap), which goes the long way round
    across +/-180 and cuts straight through the polar region.
    """
    pts, d = [], num_points - 1
    for i in range(num_points):
        t = i / d
        pts.append((lat1 + (lat2 - lat1) * t, lon1 + (lon2 - lon1) * t))
    return pts


def path_length_km(points):
    return sum(
        haversine_km(points[i][0], points[i][1],
                     points[i + 1][0], points[i + 1][1])
        for i in range(len(points) - 1)
    )


CASES = [
    # name, start, end, expected kind
    ("normal", (40.64, -73.78), (51.47, -0.45), "normal"),      # JFK -> LHR
    ("antimeridian", (-13.9, 171.8), (-18.1, -178.4), "normal"),  # near Samoa
    ("near_pole", (89.0, 20.0), (89.0, -160.0), "normal"),      # 180 deg lon apart
    ("antipodal", (0.0, 0.0), (0.0, 180.0), "antipodal"),
    ("coincident", (35.7, 139.7), (35.7, 139.7), "coincident"),
]


def write_path_csv(name, result):
    path = os.path.join(DATA_DIR, f"path_{name}.csv")
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["index", "t", "lat_deg", "lon_deg",
                    "segment_distance_km"])
        for i, (lat, lon, t) in enumerate(result["points"]):
            seg = result["distances"][i] if i < len(result["distances"]) else ""
            w.writerow([i, f"{t:.6f}", f"{lat:.8f}", f"{lon:.8f}",
                        f"{seg:.6f}" if seg != "" else ""])
    return path


def verify_case(name, start, end, expected_kind):
    """Return (result, list of (check_name, ok, detail) verification rows)."""
    lat1, lon1 = start
    lat2, lon2 = end
    r = great_circle_path(lat1, lon1, lat2, lon2, N)
    omega = r["central_angle_rad"]
    checks = []

    checks.append(("kind classification", r["kind"] == expected_kind,
                   r["kind"]))

    # 1. Every sample lies on the unit sphere.
    norms = [math.sqrt(sum(c * c for c in v)) for v in r["vectors"]]
    ok = all(abs(n - 1.0) < 1e-12 for n in norms)
    checks.append(("all points on unit sphere", ok,
                   f"max |norm-1| = {max(abs(n - 1) for n in norms):.2e}"))

    # 2. Endpoints reproduced (as lat/lon).
    p0, p_last = r["points"][0], r["points"][-1]
    end0 = unit_to_latlon(latlon_to_unit(lat1, lon1))
    end1 = unit_to_latlon(latlon_to_unit(lat2, lon2))
    ok = (math.dist(p0[:2], end0) < 1e-9
          and math.dist(p_last[:2], end1) < 1e-9)
    checks.append(("endpoints match inputs", ok,
                   f"{p0[0]:.6f},{p0[1]:.6f} -> {p_last[0]:.6f},"
                   f"{p_last[1]:.6f}"))

    # 3. Segment distances sum to the endpoint great-circle distance.
    seg_sum = sum(r["distances"])
    ok = abs(seg_sum - r["total_distance"]) < TOL_KM
    checks.append(("sum(segments) == endpoint distance", ok,
                   f"{seg_sum:.6f} vs {r['total_distance']:.6f} km"))

    # 4. Cross-check each segment against the independent haversine formula.
    hv = [
        haversine_km(r["points"][i][0], r["points"][i][1],
                     r["points"][i + 1][0], r["points"][i + 1][1])
        for i in range(N - 1)
    ]
    mism = max(abs(hv[i] - r["distances"][i]) for i in range(N - 1))
    checks.append(("segments match haversine formula", mism < TOL_KM,
                   f"max diff = {mism:.2e} km"))

    # 5. Uniform t -> equal segment lengths.
    dmax = max(r["distances"])
    dmin = min(r["distances"])
    span = dmax - dmin
    checks.append(("uniform segment spacing", span < TOL_KM,
                   f"min={dmin:.6f} max={dmax:.6f} km"))

    if name == "normal":
        # Known great-circle distance JFK -> LHR is ~5540-5560 km
        # (depends on the radius model); require the sane band.
        checks.append(("reference distance in 5500-5600 km band",
                       5500.0 < r["total_distance"] < 5600.0,
                       f"{r['total_distance']:.3f} km"))

    if name == "antimeridian":
        # Wrong route would travel ~350 deg of longitude; correct path only ~4.
        naives = naive_angle_path(lat1, lon1, lat2, lon2)
        wrong = path_length_km(naives)
        ok = wrong - r["total_distance"] > 30000.0
        checks.append(("slerp is short way across +-180", ok,
                       f"slerp={r['total_distance']:.1f} km, "
                       f"naive lon interpolation={wrong:.1f} km"))
        # The track must cross the +-180 meridian instead of passing lon 0.
        lons = [p[1] for p in r["points"]]
        max_sep = max(abs(lons[i + 1] - lons[i]) for i in range(len(lons) - 1))
        unwrapped = [l if l >= 0 else l + 360 for l in lons]
        crosses = any(unwrapped[i] <= 180 <= unwrapped[i + 1]
                      for i in range(N - 1))
        checks.append(("track crosses the 180 meridian", crosses,
                       f"max raw lon jump = {max_sep:.2f} deg (wrap expected)"))

    if name == "near_pole":
        # Two points 180 deg apart in longitude at lat 89: the shortest route
        # goes over the pole (~222 km), the naive lat-constant route is ~391 km.
        peak_lat = max(p[0] for p in r["points"])
        checks.append(("route goes over the pole", peak_lat >= 89.5,
                       f"peak lat = {peak_lat:.4f} deg (pole=90)"))
        naives = naive_angle_path(lat1, lon1, lat2, lon2)
        wrong = path_length_km(naives)
        checks.append(("slerp shorter than naive route",
                       wrong > r["total_distance"] + 100.0,
                       f"slerp={r['total_distance']:.1f} km, "
                       f"naive={wrong:.1f} km"))

    if name == "antipodal":
        checks.append(("central angle == 180 deg",
                       abs(r["central_angle_deg"] - 180.0) < 1e-9,
                       f"{r['central_angle_deg']:.9f} deg"))
        checks.append(("distance == pi * R",
                       abs(r["total_distance"] - math.pi * EARTH_RADIUS_KM)
                       < 1e-6,
                       f"{r['total_distance']:.6f} km"))
        # Endpoint must be exactly -start (the antipode), and the midpoint
        # (t=0.5) must be 90 deg away from both endpoints on the route.
        start_v = latlon_to_unit(lat1, lon1)
        end_v = r["vectors"][-1]
        ok = all(abs(end_v[i] + start_v[i]) < 1e-12 for i in range(3))
        checks.append(("endpoint is the antipode (-start)", ok,
                       ", ".join(f"{c:+.8f}" for c in end_v)))
        mid = r["vectors"][N // 2]
        dot = sum(mid[i] * start_v[i] for i in range(3))
        checks.append(("midpoint 90 deg from both endpoints",
                       abs(dot) < 1e-12,
                       f"dot(mid, start) = {dot:.2e}"))
        # Chosen route is a great circle: all points coplanar with origin.
        a = r["vectors"][0]
        n = r["vectors"][1]
        normal = (
            a[1] * n[2] - a[2] * n[1],
            a[2] * n[0] - a[0] * n[2],
            a[0] * n[1] - a[1] * n[0],
        )
        flat = max(abs(sum(normal[j] * v[j] for j in range(3)))
                   for v in r["vectors"])
        checks.append(("all points on one great-circle plane", flat < 1e-11,
                       f"max plane offset = {flat:.2e}"))

    if name == "coincident":
        ok = (r["central_angle_deg"] == 0.0
              and all(d == 0.0 for d in r["distances"])
              and len({(round(p[0], 12), round(p[1], 12))
                       for p in r["points"]}) == 1)
        checks.append(("zero angle, zero segments, constant point", ok,
                       f"angle={r['central_angle_deg']} deg, "
                       f"total={r['total_distance']} km"))

    return r, checks


class SlerpTests(unittest.TestCase):
    def test_all_cases(self):
        for name, start, end, kind in CASES:
            with self.subTest(case=name):
                _, checks = verify_case(name, start, end, kind)
                for label, ok, _detail in checks:
                    self.assertTrue(ok, f"{name}: {label}")

    def test_longitude_inputs_normalise(self):
        r1 = great_circle_path(0.0, 179.0, 0.0, -179.0, 3)
        r2 = great_circle_path(0.0, -181.0, 0.0, 181.0, 3)
        self.assertAlmostEqual(r1["total_distance"], r2["total_distance"],
                               places=9)
        self.assertLess(r1["total_distance"], 500.0)  # ~2 deg, not 358


def build_report():
    os.makedirs(DATA_DIR, exist_ok=True)
    lines = [
        "Spherical interpolation verification report",
        f"radius = {EARTH_RADIUS_KM} km, samples per path = {N}",
        "=" * 78,
    ]
    all_ok = True
    for name, start, end, kind in CASES:
        r, checks = verify_case(name, start, end, kind)
        csv_path = write_path_csv(name, r)
        lines.append("")
        lines.append(f"[{name}] {start} -> {end}")
        lines.append(f"  kind={r['kind']}  central angle="
                     f"{r['central_angle_deg']:.6f} deg  "
                     f"distance={r['total_distance']:.3f} km")
        lines.append(f"  data: {os.path.relpath(csv_path)}")
        for label, ok, detail in checks:
            all_ok &= ok
            lines.append(f"  [{'PASS' if ok else 'FAIL'}] {label} ({detail})")
        lines.append("  path (lat, lon):")
        for lat, lon, t in r["points"]:
            lines.append(f"    t={t:4.2f}  {lat:9.5f}  {lon:10.5f}")
    lines.append("")
    lines.append("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED")
    report = "\n".join(lines)
    with open(os.path.join(DATA_DIR, "verification.txt"), "w") as fh:
        fh.write(report + "\n")
    return report, all_ok


if __name__ == "__main__":
    report, ok = build_report()
    print(report)
    raise SystemExit(0 if ok else 1)
