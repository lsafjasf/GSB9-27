"""Linear-time exact pattern matching: prefix-function (KMP) and Z-function.

Public API
----------
find_all_kmp(pattern, text) -> list[int]
find_all_z(pattern, text)   -> list[int]
find_all_naive(pattern, text) -> list[int]
prefix_function(pattern) -> list[int]
z_function(seq) -> list[int]

Both text and pattern may be ``str`` (Unicode code points) or ``bytes`` /
``bytearray`` (binary bytes).  The two operands must belong to the same family.
The returned positions are zero-based offsets into ``text``, listed in
ascending order; overlapping matches are included.
"""

__all__ = [
    "find_all_kmp",
    "find_all_z",
    "find_all_naive",
    "prefix_function",
    "z_function",
]


def _check_types(pattern, text):
    is_str = isinstance(pattern, str) and isinstance(text, str)
    is_bytes = (
        isinstance(pattern, (bytes, bytearray))
        and isinstance(text, (bytes, bytearray))
    )
    if not (is_str or is_bytes):
        raise TypeError(
            "pattern and text must both be str, or both be bytes/bytearray"
        )


def prefix_function(pattern):
    """pi[i] = length of the longest proper prefix of pattern[:i+1] that is
    also its suffix.  Runs in O(len(pattern)) time and O(len(pattern)) space.
    """
    pi = [0] * len(pattern)
    j = 0
    for i in range(1, len(pattern)):
        while j > 0 and pattern[i] != pattern[j]:
            j = pi[j - 1]
        if pattern[i] == pattern[j]:
            j += 1
        pi[i] = j
    return pi


def z_function(seq):
    """z[i] = longest common prefix of seq and seq[i:].

    Runs in O(len(seq)) time and O(len(seq)) space.  seq must support
    indexing (str / bytes / list).
    """
    n = len(seq)
    z = [0] * n
    if n == 0:
        return z
    left = right = 0
    for i in range(1, n):
        if i < right:
            z[i] = min(right - i, z[i - left])
        while i + z[i] < n and seq[z[i]] == seq[i + z[i]]:
            z[i] += 1
        if i + z[i] > right:
            left, right = i, i + z[i]
    return z


def find_all_kmp(pattern, text):
    """All zero-based match starts using the prefix function (KMP).

    O(len(pattern) + len(text)) time, O(len(pattern)) extra space.  The text
    is scanned once left to right, so it also works over an incoming stream.
    """
    _check_types(pattern, text)
    if len(pattern) == 0 or len(pattern) > len(text):
        return []
    pi = prefix_function(pattern)
    matches = []
    matched = 0
    for i, ch in enumerate(text):
        while matched > 0 and ch != pattern[matched]:
            matched = pi[matched - 1]
        if ch == pattern[matched]:
            matched += 1
        if matched == len(pattern):
            matches.append(i - matched + 1)
            matched = pi[matched - 1]  # keep going -> overlapping matches
    return matches


def find_all_z(pattern, text):
    """All zero-based match starts via the Z-function on pattern + sep + text.

    O(len(pattern) + len(text)) time and space.  A sentinel object that is
    unequal to every character/byte guarantees a Z value over the text region
    never crosses back into the pattern, with no scan to pick a sentinel.
    """
    _check_types(pattern, text)
    if len(pattern) == 0 or len(pattern) > len(text):
        return []
    m = len(pattern)
    sentinel = object()
    combined = list(pattern)
    combined.append(sentinel)
    combined.extend(text)
    z = z_function(combined)
    return [idx - m - 1 for idx in range(m + 1, len(combined)) if z[idx] >= m]


def find_all_naive(pattern, text):
    """Textbook naive matching (quadratic in the worst case); reference only.

    Each alignment is checked one element at a time, so periodic inputs make
    the cost Theta(len(text) * len(pattern)).  A slice-compare variant would
    be fast here because the compare runs in C, so this explicit loop is the
    fair baseline used to demonstrate quadratic degeneration.
    """
    _check_types(pattern, text)
    if len(pattern) == 0 or len(pattern) > len(text):
        return []
    matches = []
    m, n = len(pattern), len(text)
    for start in range(n - m + 1):
        offset = 0
        while offset < m and text[start + offset] == pattern[offset]:
            offset += 1
        if offset == m:
            matches.append(start)
    return matches


if __name__ == "__main__":
    # Tiny built-in smoke test; the full checks live in test_linmatch.py.
    p, t = "abab", "abababab"
    assert find_all_kmp(p, t) == [0, 2, 4]
    assert find_all_z(p, t) == [0, 2, 4]
    assert find_all_naive(p, t) == [0, 2, 4]
    print("smoke test ok")
