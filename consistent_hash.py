"""Consistent hashing with virtual nodes (hash ring), weighted nodes, stdlib only.

Key properties:
- A key's owner is determined solely by the ring: hash positions are derived
  from (node_id, replica_index), never from registration order.
- Adding/removing a node only moves keys to/from that node; keys belonging
  to untouched nodes never migrate.
- Lookup is O(log V) where V = total number of virtual nodes.
"""

from bisect import bisect_left, insort
import hashlib

__all__ = ["HashRing"]


def _hash64(text):
    """Deterministic 64-bit hash of a string (stdlib only)."""
    digest = hashlib.md5(text.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "little")


class HashRing:
    """Hash ring with virtual nodes and per-node weights.

    Parameters
    ----------
    vnodes_per_weight:
        Number of virtual nodes placed on the ring per unit of weight.
        A node with weight ``w`` owns ``round(vnodes_per_weight * w)``
        points on the ring. Weight 0 places no points (node never selected).
    """

    def __init__(self, vnodes_per_weight=160):
        if vnodes_per_weight < 1:
            raise ValueError("vnodes_per_weight must be >= 1")
        self.vnodes_per_weight = vnodes_per_weight
        # Ring entries are (point, node_id) tuples kept sorted. Ties on
        # point are broken by node_id, so the ring contents are fully
        # deterministic and independent of insertion order.
        self._ring = []
        self._weights = {}  # node_id -> weight

    # ------------------------------------------------------------------
    # node management
    # ------------------------------------------------------------------
    def add_node(self, node_id, weight=1.0):
        """Add a node (idempotent-unsafe: adding twice raises)."""
        if node_id in self._weights:
            raise ValueError("node already present: %r" % (node_id,))
        if weight < 0:
            raise ValueError("weight must be >= 0")
        self._weights[node_id] = weight
        replicas = self._replica_count(weight)
        for i in range(replicas):
            point = _hash64("%s#%d" % (node_id, i))
            insort(self._ring, (point, node_id))

    def remove_node(self, node_id):
        """Remove a node and all its virtual nodes."""
        if node_id not in self._weights:
            raise KeyError("node not present: %r" % (node_id,))
        del self._weights[node_id]
        self._ring = [entry for entry in self._ring if entry[1] != node_id]

    def nodes(self):
        """Return the set of live node ids."""
        return set(self._weights)

    def _replica_count(self, weight):
        return int(round(self.vnodes_per_weight * weight))

    # ------------------------------------------------------------------
    # lookup
    # ------------------------------------------------------------------
    def get_node(self, key):
        """Return the node id owning ``key``, or None if the ring is empty."""
        if not self._ring:
            return None
        point = _hash64(str(key))
        idx = bisect_left(self._ring, (point,))
        if idx == len(self._ring):
            idx = 0  # wrap around the ring
        return self._ring[idx][1]

    def get_nodes(self, key, count):
        """Return up to ``count`` distinct nodes, clockwise from key position.

        Useful for replication/failover. Order is deterministic.
        """
        result = []
        if not self._ring or count <= 0:
            return result
        point = _hash64(str(key))
        idx = bisect_left(self._ring, (point,))
        seen = set()
        for offset in range(len(self._ring)):
            node_id = self._ring[(idx + offset) % len(self._ring)][1]
            if node_id not in seen:
                seen.add(node_id)
                result.append(node_id)
                if len(result) == count:
                    break
        return result

    # ------------------------------------------------------------------
    # introspection
    # ------------------------------------------------------------------
    @property
    def ring_size(self):
        """Total number of virtual nodes (points) on the ring."""
        return len(self._ring)

    def ownership(self):
        """Exact fraction of the hash space owned by each node.

        Computed from arc lengths between consecutive points, so it is
        exact (no key sampling noise).
        """
        shares = {node_id: 0 for node_id in self._weights}
        if not self._ring:
            return shares
        modulus = 1 << 64
        n = len(self._ring)
        for i in range(n):
            point, node_id = self._ring[i]
            nxt = self._ring[(i + 1) % n][0]
            arc = (nxt - point) % modulus
            shares[node_id] += arc
        return {node_id: arc / modulus for node_id, arc in shares.items()}
