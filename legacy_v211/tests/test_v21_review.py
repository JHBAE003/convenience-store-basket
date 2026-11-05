"""Independent regression checks for field provenance and freshness guards."""
from datetime import datetime, timedelta, timezone
import unittest

from core.models import StoreProduct
from core.cart_models import CartItem
from core.services.v2_common import current_evidence
from core.services.cart_pricing import evaluate_line
from core.services.integration_service import integrate_records


def live(**values):
    stamp = datetime.now(timezone.utc).isoformat()
    args = dict(brand="CU", store_name="점포", address="서울", product_name="콜라 500ml",
                product_code="SKU-1", price=1800, stock=5, promotion="", promotion_known=True,
                data_source="LIVE", price_data_status="LIVE", stock_data_status="LIVE",
                promotion_data_status="LIVE", updated_at=stamp, received_at=stamp,
                price_updated_at=stamp, stock_updated_at=stamp, promotion_checked_at=stamp,
                stock_source_updated_at=stamp)
    args.update(values)
    return StoreProduct(**args)


class IndependentEvidenceReview(unittest.TestCase):
    def test_equal_text_timestamps_do_not_extend_stock_ttl_to_promotion_ttl(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        row = live(updated_at=old, price_updated_at=old, stock_updated_at=old,
                   promotion_checked_at=old, stock_source_updated_at=old)
        self.assertFalse(current_evidence(row))

    def test_original_old_stock_wins_over_recent_receipt_alias(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        row = live(stock_source_updated_at=old)
        self.assertFalse(current_evidence(row))
        self.assertEqual(evaluate_line(CartItem("cola", "콜라", 2), row).stock_state, "UNKNOWN")

    def test_catalog_does_not_create_original_timestamp_or_drop_raw_period(self):
        inventory = live(price=0, price_data_status="UNKNOWN", promotion_known=False)
        catalog = live(store_name="", address="", price=2000, price_data_status="REFERENCE",
                       price_updated_at="", promotion_updated_at="", raw_payload={"period_text": "10.01~10.31"})
        joined = integrate_records([inventory], [catalog])[0]
        self.assertEqual(joined.price_data_status, "REFERENCE")
        self.assertEqual(joined.promotion_updated_at, "")
        self.assertEqual(joined.price_updated_at, "")
        self.assertEqual(joined.raw_payload["promotion_observation"]["period_text"], "10.01~10.31")

    def test_reference_price_cannot_become_current_even_with_explicit_store_name(self):
        row = live(price_data_status="REFERENCE")
        self.assertFalse(current_evidence(row))
        result = evaluate_line(CartItem("cola", "콜라", 2), row)
        self.assertIsNone(result.payable)
        self.assertEqual(result.reference_payable, 3600)


if __name__ == "__main__":
    unittest.main()
