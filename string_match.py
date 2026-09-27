"""Linear-time substring matching implementations.

All functions operate on :class:`bytes` and :class:`str` without decoding
binary data.  They also work with other finite sequences supporting indexed
access and ``len()``.

Contract:
* Every returned position is a zero-based start index.
* All occurrences are returned, including overlapping ones.
* An empty pattern is defined to have no non-empty occurrence, so ``[]`` is
  returned, even for an empty text.
* A pattern longer than the text returns ``[]``.
"""

from typing import Any, List, Sequence


def naive_find_all(text: Sequence[Any], pattern: Sequence[Any]) -> List[int]:
    """Return all matches using the straightforward O(n*m) algorithm."""
    n = len(text)
    m = len(pattern)

    if m == 0 or m > n:
        return []

    matches: List[int] = []
    for start in range(n - m + 1):
        offset = 0
        while offset < m and text[start + offset] == pattern[offset]:
            offset += 1
        if offset == m:
            matches.append(start)
    return matches


def prefix_function(pattern: Sequence[Any]) -> List[int]:
    """Compute the KMP prefix function of ``pattern`` in O(m) time."""
    m = len(pattern)
    pi = [0] * m

    for i in range(1, m):
        length = pi[i - 1]
        while length > 0 and pattern[i] != pattern[length]:
            length = pi[length - 1]
        if pattern[i] == pattern[length]:
            length += 1
        pi[i] = length

    return pi


def kmp_find_all(text: Sequence[Any], pattern: Sequence[Any]) -> List[int]:
    """Return all matches using the prefix-function (KMP) algorithm.

    Time: O(n + m).  Extra space: O(m) for the prefix table.
    """
    n = len(text)
    m = len(pattern)

    if m == 0 or m > n:
        return []

    pi = prefix_function(pattern)
    matches: List[int] = []
    matched = 0

    for i in range(n):
        while matched > 0 and text[i] != pattern[matched]:
            matched = pi[matched - 1]
        if text[i] == pattern[matched]:
            matched += 1

        if matched == m:
            matches.append(i - m + 1)
            matched = pi[matched - 1]

    return matches


def z_function(sequence: Sequence[Any]) -> List[int]:
    """Compute Z values: z[i] is the LCP of ``sequence`` and ``sequence[i:]``.

    Time: O(len(sequence)).
    """
    length = len(sequence)
    z = [0] * length
    left = 0
    right = 0

    for i in range(1, length):
        if i < right:
            z[i] = min(right - i, z[i - left])

        while i + z[i] < length and sequence[z[i]] == sequence[i + z[i]]:
            z[i] += 1

        if i + z[i] > right:
            left = i
            right = i + z[i]

    return z


def z_find_all(text: Sequence[Any], pattern: Sequence[Any]) -> List[int]:
    """Return all matches using a Z-function scan.

    The Z array of the pattern describes its internal periodicity.  While
    scanning the text, ``[left, right)`` is a Z-box known to equal a prefix of
    the pattern.  Values inside that box are initialized from the pattern's Z
    array before extending.

    Time: O(n + m).  Extra space: O(m) for the pattern Z array.
    """
    n = len(text)
    m = len(pattern)

    if m == 0 or m > n:
        return []

    pattern_z = z_function(pattern)
    matches: List[int] = []
    left = 0
    right = 0

    for i in range(n):
        matched = 0

        if i < right:
            matched = min(right - i, pattern_z[i - left])

        while (
            matched < m
            and i + matched < n
            and text[i + matched] == pattern[matched]
        ):
            matched += 1

        if matched == m:
            matches.append(i)

        if i + matched > right:
            left = i
            right = i + matched

    return matches
