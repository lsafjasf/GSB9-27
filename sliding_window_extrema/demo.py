"""Tiny manual demo: python3 -m sliding_window_extrema.demo"""

from .sliding_minmax import SlidingWindowMinMax


def main() -> None:
    sw = SlidingWindowMinMax(3)
    stream = [2, 5, 3, 9, 1]
    print("window=3")
    for value in stream:
        sw.push(value)
        print(f"  push {value}: active={sw.active_count}, "
              f"min={sw.get_min()}, max={sw.get_max()}")

    sw.resize(6)
    print(f"resize 6 -> (min, max) = {sw.get_min_max()}")

    sw.resize(0)
    print(f"resize 0 -> (min, max) = {sw.get_min_max()} (empty window)")

    sw.resize(2)
    print(f"resize 2 -> (min, max) = {sw.get_min_max()} (history reused)")


if __name__ == "__main__":
    main()
