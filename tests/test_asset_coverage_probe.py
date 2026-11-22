"""Discovery eligibility tests without exchange requests or private trade data."""
import unittest
from unittest.mock import patch

from research.asset_coverage_probe import explain, request_current_public_instrument


class PublicCoverageTests(unittest.TestCase):
    def test_curated_absence_must_not_be_confused_with_exchange_delisting(self):
        d=explain("ORCA")
        self.assertFalse(d["is_in_curated_base_allowlist"])
        self.assertFalse(d["can_enter_collector_curated_selection"])
        self.assertIsNone(d["public_okx_contract_check"])
        self.assertTrue(d["provider_status_unknown_if_not_checked"])
        self.assertFalse(d["user_trade_or_return_recorded"])
        self.assertFalse(d["real_trade_verified"])

    def test_current_listed_instrument_is_not_historical_trade_evidence(self):
        d=explain("ORCA",instruments=[{
            "instId":"ORCA-USDT-SWAP","state":"live","tickSz":"0.001"}])
        self.assertTrue(d["public_okx_contract_check"][
            "current_usdt_swap_contract_exists"])
        self.assertFalse(d["public_okx_contract_check"][
            "historical_listing_at_signal_time_verified"])
        self.assertFalse(d["scanner_trade_generated"])
        self.assertFalse(d["filters_or_production_changed"])

    def test_provider_missing_contract_does_not_forge_timing_or_fills(self):
        d=explain("ORCA",instruments=[])
        self.assertFalse(d["public_okx_contract_check"][
            "current_usdt_swap_contract_exists"])
        self.assertFalse(d["external_venue_equivalence_proven"])

    def test_snapshot_absence_and_allowlist_are_separate(self):
        snap={"status":"COLLECTED","market_type":"USDT_PERPETUAL",
              "collected_utc":"2026-11-22T16:00:00Z",
              "universe":[{"symbol":"SOL-USDT-SWAP"}]}
        a=explain("SOL",market_snapshot=snap)
        b=explain("ORCA",market_snapshot=snap)
        self.assertTrue(a["is_in_curated_base_allowlist"])
        self.assertTrue(a["is_in_given_scanner_snapshot"])
        self.assertFalse(b["is_in_given_scanner_snapshot"])
        self.assertFalse(b["user_trade_or_return_recorded"])

    def test_okx_51001_means_current_instrument_id_unknown(self):
        with patch("research.asset_coverage_probe.request_json",
                   return_value={"code":"51001","data":[],
                                 "msg":"Instrument ID doesn't exist."}):
            rows,code=request_current_public_instrument("ORCA")
        self.assertEqual(rows,[])
        self.assertEqual(code,"51001")
        report=explain("ORCA",instruments=rows,provider_response_code=code)
        self.assertFalse(report["public_okx_contract_check"][
            "current_usdt_swap_contract_exists"])
        self.assertEqual(report["public_okx_contract_check"][
            "provider_response_code"],"51001")
        self.assertFalse(report["public_okx_contract_check"][
            "historical_listing_at_signal_time_verified"])

    def test_other_provider_error_is_not_mislabeled_as_delisting(self):
        with patch("research.asset_coverage_probe.request_json",
                   return_value={"code":"50000","data":[],
                                 "msg":"temporary"}):
            with self.assertRaises(RuntimeError):
                request_current_public_instrument("ORCA")

    def test_invalid_ticker_and_duplicate_provider_fails_closed(self):
        with self.assertRaises(ValueError):
            explain("../ORCA")
        with self.assertRaises(ValueError):
            explain("ORCA",instruments=[{"instId":"ORCA-USDT-SWAP"},
                                        {"instId":"ORCA-USDT-SWAP"}])


if __name__=="__main__":
    unittest.main()
