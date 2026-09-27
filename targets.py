"""
Three known payment-like implementations used to self-test the framework.

  IdempotentPaymentTarget    deduplicates by idempotency key: side effect once
  NonIdempotentPaymentTarget charges on every call, new txn id every time
  FakeIdempotentPaymentTarget returns a cached response per key but still
                             applies the side effect on every call

All three support arm_timeout(n): the next n executions apply their side
effect and then raise SimulatedTimeout, mimicking "server processed it but
the client saw a timeout".
"""

from __future__ import annotations

import copy
import threading

from idempotency_framework import SimulatedTimeout


class _BasePaymentTarget:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._timeouts_remaining = 0
        self.reset()

    # -- target contract ----------------------------------------------------

    def reset(self) -> None:
        with self._lock:
            self.charge_count = 0
            self.ledger = []          # written records
            self.notifications = []   # emitted notifications
            self._processed = {}      # idempotency-key -> response cache

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "side_effect_count": self.charge_count,
                "ledger": copy.deepcopy(self.ledger),
                "notifications": list(self.notifications),
            }

    # -- timeout simulation ---------------------------------------------------

    def arm_timeout(self, n: int) -> None:
        with self._lock:
            self._timeouts_remaining = n

    def _maybe_timeout(self, key: str) -> None:
        with self._lock:
            if self._timeouts_remaining > 0:
                self._timeouts_remaining -= 1
                raise SimulatedTimeout(
                    f"request {key}: processed but response lost (timeout)")

    # -- shared side effect -----------------------------------------------------

    def _apply_side_effect_locked(self, request: dict) -> dict:
        """Must be called with self._lock held. Returns the response."""
        self.charge_count += 1
        txn_id = f"txn-{self.charge_count:04d}"
        self.ledger.append({
            "transaction_id": txn_id,
            "idempotency_key": request["idempotency_key"],
            "amount": request["amount"],
            "currency": request["currency"],
        })
        self.notifications.append(
            f"sms: charged {request['amount']} {request['currency']} ({txn_id})")
        return {
            "status": "ok",
            "transaction_id": txn_id,
            "idempotency_key": request["idempotency_key"],
        }


class IdempotentPaymentTarget(_BasePaymentTarget):
    """Truly idempotent: deduplicates by idempotency key."""

    def execute(self, request: dict) -> dict:
        key = request["idempotency_key"]
        with self._lock:
            if key in self._processed:
                response = self._processed[key]
            else:
                response = self._apply_side_effect_locked(request)
                self._processed[key] = response
        self._maybe_timeout(key)
        return response


class NonIdempotentPaymentTarget(_BasePaymentTarget):
    """Not idempotent: every call charges again and returns a new txn id."""

    def execute(self, request: dict) -> dict:
        with self._lock:
            response = self._apply_side_effect_locked(request)
        self._maybe_timeout(request["idempotency_key"])
        return response


class FakeIdempotentPaymentTarget(_BasePaymentTarget):
    """Pretends to be idempotent: caches the *response* per key but still
    applies the side effect on every single call."""

    def execute(self, request: dict) -> dict:
        key = request["idempotency_key"]
        with self._lock:
            self._apply_side_effect_locked(request)  # always charges!
            if key not in self._processed:
                self._processed[key] = {
                    "status": "ok",
                    "transaction_id": f"txn-{self.charge_count:04d}",
                    "idempotency_key": key,
                }
            response = self._processed[key]
        self._maybe_timeout(key)
        return response
