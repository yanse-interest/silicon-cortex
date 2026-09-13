import json
import unittest

from dashboard_local_delivery_check import classify_local_delivery


class DashboardLocalDeliveryCheckTests(unittest.TestCase):
    def setUp(self):
        self.expected = {
            "generated_at": "2026-08-05T22:50:32+08:00",
            "daily_updated_through": "2026-08-04",
            "capability_domains": [{"items": [{"topic": "LockMass配置"}, {"topic": "流路配置"}]}],
        }
        self.headers = {"cache-control": "no-store, max-age=0"}

    def test_running_service_must_render_current_imported_snapshot(self):
        body = json.dumps(self.expected, ensure_ascii=False).encode()
        result = classify_local_delivery(
            expected=self.expected,
            root_status=200,
            root_headers=self.headers,
            root_body=body,
            snapshot_status=200,
            snapshot_headers=self.headers,
            snapshot_body=body,
        )

        self.assertEqual(result["local_delivery_status"], "usable")
        self.assertEqual(result["routing_status"], "normal_complete")

    def test_new_static_json_with_old_server_bundle_is_rejected(self):
        old_root = json.dumps({
            **self.expected,
            "generated_at": "2026-08-05T21:25:27+08:00",
            "capability_domains": [{"items": [{"topic": "LockMass 采集与处理的两阶段配置"}]}],
        }, ensure_ascii=False).encode()
        current_snapshot = json.dumps(self.expected, ensure_ascii=False).encode()

        result = classify_local_delivery(
            expected=self.expected,
            root_status=200,
            root_headers=self.headers,
            root_body=old_root,
            snapshot_status=200,
            snapshot_headers=self.headers,
            snapshot_body=current_snapshot,
        )

        self.assertEqual(result["local_delivery_status"], "stale_or_cacheable")
        self.assertIn("local_server_bundle_stale", result["reason_codes"])
        self.assertTrue(result["snapshot_matches"])
        self.assertFalse(result["rendered_markers_match"])


if __name__ == "__main__":
    unittest.main()
