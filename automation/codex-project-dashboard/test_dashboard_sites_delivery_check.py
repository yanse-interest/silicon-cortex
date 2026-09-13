import json
import unittest

from dashboard_sites_delivery_check import classify_delivery


class DashboardSitesDeliveryCheckTests(unittest.TestCase):
    def test_authenticated_readback_requires_exact_snapshot_markers(self):
        body = json.dumps({
            "daily_updated_through": "2026-08-04",
            "generated_at": "2026-08-05T22:50:32+08:00",
        }).encode()

        result = classify_delivery(
            status=200,
            headers={"content-type": "application/json"},
            body=body,
            expected_date="2026-08-04",
            expected_generated_at="2026-08-05T22:50:32+08:00",
        )

        self.assertEqual(result["delivery_status"], "usable")
        self.assertEqual(result["routing_status"], "normal_complete")
        self.assertEqual(result["reason_codes"], [])

    def test_cloudflare_hkg_403_is_distinct_from_deployment_failure(self):
        result = classify_delivery(
            status=403,
            headers={"server": "cloudflare", "cf-ray": "a266a41b3d300a04-HKG"},
            body=b"<h1>Sorry, you have been blocked</h1>",
            expected_date="2026-08-04",
            expected_generated_at="2026-08-05T22:50:32+08:00",
        )

        self.assertEqual(result["delivery_status"], "deployed_but_edge_blocked")
        self.assertEqual(result["routing_status"], "user_handoff")
        self.assertEqual(result["reason_codes"], ["cloudflare_edge_blocked"])
        self.assertEqual(result["cf_ray"], "a266a41b3d300a04-HKG")
        self.assertEqual(result["cf_colo"], "HKG")
        self.assertEqual(result["local_h5_fallback"], "http://mbp.local:8792/")

    def test_200_with_stale_markers_does_not_report_complete(self):
        body = json.dumps({
            "daily_updated_through": "2026-08-02",
            "generated_at": "2026-08-05T22:34:03+08:00",
        }).encode()

        result = classify_delivery(
            status=200,
            headers={},
            body=body,
            expected_date="2026-08-04",
            expected_generated_at="2026-08-05T22:50:32+08:00",
        )

        self.assertEqual(result["delivery_status"], "delivery_marker_mismatch")
        self.assertEqual(result["routing_status"], "failed_medium")
        self.assertEqual(result["reason_codes"], ["delivery_marker_mismatch"])


if __name__ == "__main__":
    unittest.main()
