"""Delivery cancellation keeps a timestamp and a readable basis (dev-0028 SPEC-2).

When a source is disabled or revoked, ``_invalidate`` cancels the not-yet-sent
deliveries that reference it. Those rows now record ``updated_at`` and append
``cancelled_basis`` to the receipt, matching the other cancel paths
(``commitment_cancel`` / ``initiative_dismiss``). This file is separate from
``test_framework_product.py`` so the two can be edited independently.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mirally.framework import FrameworkService

SOURCE = "memory:item"


class DeliveryInvalidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name) / "中文 root with spaces"
        self.base.mkdir()
        self.home = self.base / "个人 数据"
        self.now = 1_000_000
        self.service = FrameworkService(self.base, self.home, clock=lambda: self.now)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def call(self, method, **params):
        return self.service.call(method, params)

    def pending_delivery(self, *, channel="weixin", dedupe="offer-1", sources=None):
        """Queue one initiative delivery that references a source and stays pending."""
        offer = self.call("initiative.offer",
                          title="今晚的提醒", body="想起你提过的事", reason="follow_up",
                          expires_at_utc_ms=self.now + 3_600_000, dedupe_key=dedupe,
                          channel=channel,
                          sources=[{"source_id": SOURCE}] if sources is None else sources)
        self.call("settings.update", patch={"general_initiative_enabled": True,
                                            "background_enabled": True, "do_not_disturb": False})
        self.call("delivery.tick")
        rows = [row for row in self.call("delivery.list")["deliveries"]
                if row["reference_id"] == offer["id"]]
        self.assertEqual(len(rows), 1, rows)
        return rows[0]

    def delivery(self, delivery_id):
        return next(row for row in self.call("delivery.list")["deliveries"] if row["id"] == delivery_id)

    def set_receipt(self, delivery_id, value):
        self.service.db.execute("UPDATE deliveries SET receipt_json=? WHERE id=?",
                                (value, delivery_id))

    def disable_source(self):
        return self.call("source.set", source_id=SOURCE, enabled=False)

    # V1 --------------------------------------------------------------------
    def test_disabling_a_source_cancels_pending_delivery_with_time_and_basis(self):
        delivery = self.pending_delivery()
        self.assertEqual(delivery["status"], "pending")
        created_at = delivery["created_at"]
        self.now += 5_000

        self.disable_source()

        after = self.delivery(delivery["id"])
        self.assertEqual(after["status"], "cancelled")
        self.assertEqual(after["updated_at"], self.now)
        self.assertNotEqual(after["updated_at"], created_at)
        self.assertEqual(after["receipt"]["cancelled_basis"], "source_disabled")

    # V2 --------------------------------------------------------------------
    def test_enabled_source_leaves_the_delivery_pending(self):
        delivery = self.pending_delivery()
        self.now += 5_000
        self.call("source.set", source_id=SOURCE, enabled=True)  # already enabled: no-op guard

        after = self.delivery(delivery["id"])
        self.assertEqual(after["status"], "pending")
        self.assertEqual(after["updated_at"], delivery["updated_at"])
        self.assertIsNone(after["receipt"])

    # V3 --------------------------------------------------------------------
    def test_already_delivered_delivery_is_untouched(self):
        delivery = self.pending_delivery()
        claim = self.call("delivery.claim", delivery_id=delivery["id"])
        self.call("delivery.ack", delivery_id=delivery["id"],
                  claim_token=claim["claim_token"], state="delivered")
        delivered = self.delivery(delivery["id"])
        self.assertEqual(delivered["status"], "delivered")
        delivered_receipt = dict(delivered["receipt"])
        self.now += 5_000

        self.disable_source()

        after = self.delivery(delivery["id"])
        self.assertEqual(after["status"], "delivered")
        self.assertEqual(after["updated_at"], delivered["updated_at"])
        self.assertEqual(after["receipt"], delivered_receipt)
        self.assertNotIn("cancelled_basis", after["receipt"] or {})

    # V4 --------------------------------------------------------------------
    def test_repeating_the_disable_does_not_refresh_the_cancel_time(self):
        delivery = self.pending_delivery()
        self.now += 1_000
        self.disable_source()
        first = self.delivery(delivery["id"])
        self.now += 60_000
        self.disable_source()  # same state again

        second = self.delivery(delivery["id"])
        self.assertEqual(second["status"], "cancelled")
        self.assertEqual(second["updated_at"], first["updated_at"])
        self.assertEqual(second["receipt"], first["receipt"])

    # V5 --------------------------------------------------------------------
    def test_cancel_basis_merges_into_an_existing_receipt(self):
        delivery = self.pending_delivery()
        existing = {"attempt": 2, "channel_note": "重试过一次", "nested": {"keep": True}}
        self.set_receipt(delivery["id"], json.dumps(existing, ensure_ascii=False))

        self.disable_source()

        receipt = self.delivery(delivery["id"])["receipt"]
        for key, value in existing.items():
            self.assertEqual(receipt[key], value, key)
        self.assertEqual(receipt["cancelled_basis"], "source_disabled")

    def test_cancel_basis_does_not_clobber_a_non_object_receipt(self):
        """A legacy/foreign receipt value must be preserved, not dropped."""
        delivery = self.pending_delivery()
        self.set_receipt(delivery["id"], json.dumps("legacy-string"))

        self.disable_source()

        receipt = self.delivery(delivery["id"])["receipt"]
        self.assertEqual(receipt["previous_receipt"], "legacy-string")
        self.assertEqual(receipt["cancelled_basis"], "source_disabled")

    # Source changed (version bump) ----------------------------------------
    def test_source_change_cancels_with_source_changed_basis(self):
        delivery = self.pending_delivery()
        # Disable and re-enable: the version moves on, so the queued reference is stale.
        self.call("source.set", source_id=SOURCE, enabled=False)
        self.call("source.set", source_id=SOURCE, enabled=True)
        # Re-queue with the *current* version, then bump again to simulate a change.
        self.service.db.execute("UPDATE deliveries SET status='pending',receipt_json=NULL,updated_at=? WHERE id=?",
                                (self.now, delivery["id"]))
        current = next(s for s in self.call("source.list")["sources"] if s["source_id"] == SOURCE)
        self.service.db.execute("UPDATE deliveries SET sources_json=? WHERE id=?",
                                (json.dumps([{"source_id": SOURCE, "version": current["version"]}]), delivery["id"]))
        self.now += 2_000

        # force=True replays invalidation and bumps the version -> source_changed.
        self.service._invalidate(SOURCE, True, "source_changed", force=True)

        after = self.delivery(delivery["id"])
        self.assertEqual(after["status"], "cancelled")
        self.assertEqual(after["updated_at"], self.now)
        self.assertEqual(after["receipt"]["cancelled_basis"], "source_changed")

    # Judgement boundaries must not move ------------------------------------
    def test_unrelated_source_does_not_cancel(self):
        delivery = self.pending_delivery()
        self.call("source.set", source_id="memory:other", enabled=False)
        after = self.delivery(delivery["id"])
        self.assertEqual(after["status"], "pending")
        self.assertIsNone(after["receipt"])

    def test_rows_are_never_deleted(self):
        delivery = self.pending_delivery()
        before = {row["id"] for row in self.call("delivery.list")["deliveries"]}
        self.disable_source()
        after_rows = self.call("delivery.list")["deliveries"]
        self.assertEqual({row["id"] for row in after_rows}, before)
        self.assertIn(delivery["id"], before)

    # Late acknowledgement must not erase the cancellation ------------------
    def test_late_delivered_ack_keeps_cancel_basis_and_time(self):
        delivery = self.pending_delivery()
        claimed = self.call("delivery.claim", delivery_id=delivery["id"])
        self.assertEqual(claimed["id"], delivery["id"])
        self.assertEqual(claimed["status"], "sending")
        token = claimed["claim_token"]
        baselined = self.delivery(delivery["id"])
        self.assertEqual(baselined["status"], "sending")
        self.now += 3_000
        self.disable_source()
        cancelled = self.delivery(delivery["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["receipt"]["cancelled_basis"], "source_disabled")
        self.assertEqual(cancelled["receipt"]["cancelled_at"], self.now)
        cancelled_at = cancelled["updated_at"]
        self.now += 60_000

        acked = self.call("delivery.ack", delivery_id=delivery["id"], claim_token=token,
                          state="delivered", receipt={"channel_message_id": "wx-42"})

        self.assertEqual(acked["status"], "cancelled")
        receipt = acked["receipt"]
        # The late ack is recorded ...
        self.assertEqual(receipt["state"], "delivered")
        self.assertIs(receipt["late"], True)
        self.assertEqual(receipt["receipt"], {"channel_message_id": "wx-42"})
        # ... without losing why and when the plan was cancelled.
        self.assertEqual(receipt["cancelled_basis"], "source_disabled")
        self.assertEqual(receipt["cancelled_at"], cancelled_at)
        self.assertEqual(acked["updated_at"], self.now)
        # The late ack never fulfils the cancelled plan: the owner keeps its own status
        # and the delivery row stays cancelled.
        owner = next(row for row in self.call("initiative.list")["initiatives"]
                     if row["id"] == delivery["reference_id"])
        self.assertNotEqual(owner["status"], "delivered")


if __name__ == "__main__":
    unittest.main()
