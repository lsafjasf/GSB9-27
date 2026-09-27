"""后缀数组（Suffix Array）与最长公共前缀数组（LCP Array）库。

仅使用 Python 标准库，输入按 bytes 处理（允许任意字节，含 \\x00 / \\xff）。

算法与复杂度（n = len(data)）：
  - 后缀数组构造：前缀倍增法（Manber–Myers）+ 计数排序（基数排序）。
    每轮排序 O(n)，共 O(log n) 轮，总复杂度 O(n log n)，
    显著优于朴素做法（直接排序全部后缀，O(n^2 log n)）。
  - LCP 数组：Kasai 算法，O(n)。
  - 任意两后缀 LCP 查询：对 LCP 数组建稀疏表（Sparse Table）做 RMQ，
    构建 O(n log n)，单次查询 O(1)。稀疏表在首次查询时惰性构建。
  - 最长重复子串：LCP 数组最大值，O(n)。

约定：
  - sa[i]    ：排名第 i 小的后缀的起始下标。
  - lcp[0] = 0；lcp[i] (i > 0) 为 sa[i-1] 与 sa[i] 两个后缀的最长公共前缀长度。
"""


def build_suffix_array(s: bytes) -> list[int]:
    """前缀倍增 + 计数排序构造后缀数组，O(n log n)。"""
    n = len(s)
    if n == 0:
        return []

    # 初始：按单字节计数排序（字节值域恰为 0..255）
    cnt = [0] * 256
    for b in s:
        cnt[b] += 1
    pos = [0] * 256
    total = 0
    for c in range(256):
        pos[c] = total
        total += cnt[c]
    sa = [0] * n
    for i, b in enumerate(s):
        sa[pos[b]] = i
        pos[b] += 1

    # rank[i]：后缀 i 当前（按前 k 个字符）的等价类编号
    rank = [0] * n
    classes = 1
    rank[sa[0]] = 0
    for idx in range(1, n):
        if s[sa[idx]] != s[sa[idx - 1]]:
            classes += 1
        rank[sa[idx]] = classes - 1

    k = 1
    while classes < n:
        # 按第二关键字 rank[i+k] 排序：i+k >= n 者（第二关键字视为 -1）排最前，
        # 其余利用上一轮 sa 的顺序平移得到（sa 已按第一关键字有序）。
        sa2 = list(range(n - k, n)) if k < n else []
        sa2.extend(x - k for x in sa if x >= k)
        # 计数排序按第一关键字 rank[i] 稳定排序
        cnt = [0] * classes
        for i in sa2:
            cnt[rank[i]] += 1
        pos = [0] * classes
        total = 0
        for c in range(classes):
            pos[c] = total
            total += cnt[c]
        new_sa = [0] * n
        for i in sa2:
            r = rank[i]
            new_sa[pos[r]] = i
            pos[r] += 1
        sa = new_sa
        # 重新计算等价类
        new_rank = [0] * n
        new_rank[sa[0]] = 0
        new_classes = 1
        for idx in range(1, n):
            p, c = sa[idx - 1], sa[idx]
            key_p = (rank[p], rank[p + k] if p + k < n else -1)
            key_c = (rank[c], rank[c + k] if c + k < n else -1)
            if key_p != key_c:
                new_classes += 1
            new_rank[c] = new_classes - 1
        rank = new_rank
        classes = new_classes
        k <<= 1
    return sa


def build_lcp_array(s: bytes, sa: list[int]) -> list[int]:
    """Kasai 算法由后缀数组构造 LCP 数组，O(n)。

    返回长度 n 的数组：lcp[0] = 0，lcp[i] = LCP(后缀 sa[i-1], 后缀 sa[i])。
    """
    n = len(s)
    if n == 0:
        return []
    rank = [0] * n
    for i, su in enumerate(sa):
        rank[su] = i
    lcp = [0] * n
    h = 0
    for i in range(n):
        r = rank[i]
        if r > 0:
            j = sa[r - 1]
            while i + h < n and j + h < n and s[i + h] == s[j + h]:
                h += 1
            lcp[r] = h
            if h:
                h -= 1
    return lcp


class SuffixArray:
    """对一段 bytes 预处理后，提供重复子串相关查询。"""

    def __init__(self, data):
        self.data = bytes(data)  # 接受 bytes / bytearray / memoryview
        self.n = len(self.data)
        self.sa = build_suffix_array(self.data)
        self.lcp = build_lcp_array(self.data, self.sa)
        self.rank = [0] * self.n
        for i, su in enumerate(self.sa):
            self.rank[su] = i
        self._st = None   # 稀疏表，惰性构建
        self._log = None

    def _build_rmq(self):
        """对 lcp 数组建稀疏表（区间最小值），O(n log n)。"""
        n = self.n
        log = [0] * (n + 1)
        for i in range(2, n + 1):
            log[i] = log[i >> 1] + 1
        st = [self.lcp]
        j = 1
        while (1 << j) <= n:
            prev = st[-1]
            span = 1 << (j - 1)
            st.append([min(prev[i], prev[i + span])
                       for i in range(n - (1 << j) + 1)])
            j += 1
        self._log = log
        self._st = st

    def lcp_of_suffixes(self, i: int, j: int) -> int:
        """任意两后缀（起始下标 i、j）的最长公共前缀长度，O(1)。"""
        if not (0 <= i < self.n) or not (0 <= j < self.n):
            raise IndexError("suffix index out of range")
        if i == j:
            return self.n - i
        if self._st is None:
            self._build_rmq()
        ri, rj = self.rank[i], self.rank[j]
        if ri > rj:
            ri, rj = rj, ri
        # 答案为 lcp[ri+1 .. rj] 的最小值
        lo, hi = ri + 1, rj
        k = self._log[hi - lo + 1]
        row = self._st[k]
        return min(row[lo], row[hi - (1 << k) + 1])

    def longest_repeated_substring(self):
        """最长重复子串（允许重叠）。返回 (长度, 其中一个出现位置)。

        长度 0 表示不存在重复子串（空串、单字节串或全互异）。
        """
        if self.n < 2:
            return (0, 0)
        best_len = 0
        best_idx = 0
        for i in range(1, self.n):
            if self.lcp[i] > best_len:
                best_len = self.lcp[i]
                best_idx = i
        if best_len == 0:
            return (0, 0)
        return (best_len, self.sa[best_idx])
