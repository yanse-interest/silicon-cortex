import importlib.util
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_codex_memory.py"
SPEC = importlib.util.spec_from_file_location("audit_codex_memory", SCRIPT)
audit_module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = audit_module
SPEC.loader.exec_module(audit_module)


def resource(paths, classification, owner="test owner", *, exclude_paths=None):
    item = {
        "paths": paths,
        "classification": classification,
        "canonical_use": "forbidden" if classification in audit_module.NONCANONICAL_CLASSES else "allowed",
        "owner_role": owner,
        "producer": "test producer",
        "consumers": ["test consumer"],
        "mutability": "test controlled",
        "rebuildability": {"rebuildable": True, "method": "test replay"},
        "authoritative_fields": ["test field"],
        "legacy_transition_notes": "test transition",
    }
    if exclude_paths:
        item["exclude_paths"] = exclude_paths
    return item


class AuditCodexMemoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for relative, text in {
            "_index.md": "# Root\n\n- [[wiki/workflows/codex-memory-system-map]]\n",
            "AGENTS.memory.md": "# Rules\n",
            "inbox.md": "# Inbox\n\nPending capture.\n",
            "wiki/index.md": "# Wiki\n\n- [[workflows/codex-memory-system-map]]\n",
            "wiki/log.md": "# Log\n\n## [2026-08-27] lint | Test\n",
            "wiki/workflows/codex-memory-system-architecture.md": "# Architecture\n\n[[codex-memory-system-map]]\n",
            "wiki/workflows/codex-memory-system-map.md": (
                "# Map\n\n[manifest](../source-of-truth-manifest.json)\n"
                "[status](../system-convergence-status.json)\n"
            ),
        }.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        for relative in audit_module.REQUIRED_WIKI_DIRS:
            (self.root / relative).mkdir(parents=True, exist_ok=True)
        self.contract = self.root / "external/compiled_source_contract.py"
        self.contract.parent.mkdir(parents=True, exist_ok=True)
        self.contract.write_text('RECEIPT_REQUIRED_FROM = "2026-08-23"\n', encoding="utf-8")
        self.write_convergence_status()
        self.write_manifest()

    def tearDown(self):
        self.temp.cleanup()

    def write_manifest(self, extra=None):
        resources = [
            resource(["wiki/source-of-truth-manifest.json"], "canonical_state", "manifest"),
            resource([audit_module.CONVERGENCE_STATUS_PATH], "canonical_state", "convergence"),
            resource([audit_module.SYSTEM_MAP_PATH], "curated_knowledge", "system map"),
            resource(["wiki/workflows/codex-memory-system-architecture.md"], "curated_knowledge", "architecture"),
            resource(["AGENTS.memory.md", "wiki/log.md"], "canonical_state", "rules"),
            resource(["_index.md", "wiki/index.md"], "derived_view", "indexes"),
            resource(["inbox.md"], "temporary", "inbox"),
        ]
        resources.extend(extra or [])
        manifest = {
            "schema_version": "1.0",
            "manifest_id": "test",
            "as_of_date": "2026-08-27",
            "scope": "vault",
            "path_format": "vault_relative",
            "classifications": sorted(audit_module.MANIFEST_CLASSIFICATIONS),
            "policies": {"daily_receipt_enforcement_start": "2026-08-23"},
            "resources": resources,
        }
        path = self.root / audit_module.MANIFEST_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return manifest

    def write_convergence_status(self, *, counts=None, evidence_paths=None):
        checklist = [
            {
                "id": "test-convergence",
                "label": "Test convergence",
                "status": "achieved",
                "evidence_paths": evidence_paths or ["_index.md", str(self.contract)],
                "exact_next_action": "Keep validating.",
            }
        ]
        payload = {
            "schema_version": "1.0",
            "artifact_type": "codex_memory_system_convergence_status",
            "as_of_date": "2026-08-29",
            "allowed_statuses": sorted(audit_module.CONVERGENCE_ALLOWED_STATUSES),
            "external_evidence_paths": [
                {"path": str(self.contract), "role": "canonical_contract", "canonical": True}
            ],
            "status_counts": counts or {
                "achieved": 1,
                "waiting_external": 0,
                "requires_approval": 0,
                "incomplete": 0,
                "total": 1,
            },
            "checklist": checklist,
        }
        path = self.root / audit_module.CONVERGENCE_STATUS_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return payload

    def test_valid_manifest_covers_all_live_paths(self):
        result = audit_module.audit_manifest(self.root)
        self.assertTrue(result["valid"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["unmatched_live_paths"], [])

    def test_manifest_rejects_absolute_paths_and_canonical_derived_use(self):
        bad = resource(["/Users/example/secret.md"], "derived_view", "bad")
        bad["canonical_use"] = "allowed"
        self.write_manifest([bad])
        result = audit_module.audit_manifest(self.root)
        rendered = "\n".join(result["errors"])
        self.assertIn("non-vault-relative", rendered)
        self.assertIn("canonical_use", rendered)

    def test_manifest_reports_conflicting_live_ownership(self):
        self.write_manifest([resource(["wiki/log.md"], "curated_knowledge", "conflict")])
        result = audit_module.audit_manifest(self.root)
        self.assertFalse(result["valid"])
        self.assertTrue(any(item["conflict"] for item in result["conflicting_paths"]))

    def test_manifest_reports_unmatched_major_live_path(self):
        (self.root / "wiki/orphan.json").write_text("{}\n", encoding="utf-8")
        result = audit_module.audit_manifest(self.root)
        self.assertIn("wiki/orphan.json", result["unmatched_live_paths"])

    def test_system_convergence_status_and_map_links_are_valid(self):
        manifest = self.write_manifest()

        self.assertEqual(audit_module.audit_system_convergence(self.root, manifest), [])

    def test_system_convergence_rejects_count_drift_and_derived_health_evidence(self):
        health = self.root / "wiki/memory-system-health.json"
        health.write_text("{}\n", encoding="utf-8")
        self.write_convergence_status(
            counts={
                "achieved": 0,
                "waiting_external": 0,
                "requires_approval": 0,
                "incomplete": 0,
                "total": 0,
            },
            evidence_paths=["wiki/memory-system-health.json"],
        )
        manifest = self.write_manifest(
            [resource(["wiki/memory-system-health.json"], "derived_view", "health")]
        )

        issues = audit_module.audit_system_convergence(self.root, manifest)

        self.assertTrue(any("status_counts drift" in issue for issue in issues))
        self.assertTrue(any("uses derived health" in issue for issue in issues))

    def test_system_convergence_rejects_missing_evidence_and_map_link(self):
        self.write_convergence_status(evidence_paths=["wiki/missing-evidence.json"])
        (self.root / "wiki/index.md").write_text("# Wiki without map\n", encoding="utf-8")
        manifest = self.write_manifest()

        issues = audit_module.audit_system_convergence(self.root, manifest)

        self.assertTrue(any("evidence path is missing" in issue for issue in issues))
        self.assertIn("system map link missing from wiki/index.md", issues)

    def test_system_convergence_rejects_contract_manifest_date_drift(self):
        self.contract.write_text('RECEIPT_REQUIRED_FROM = "2026-08-24"\n', encoding="utf-8")
        manifest = self.write_manifest()

        issues = audit_module.audit_system_convergence(self.root, manifest)

        self.assertIn("receipt enforcement drift: contract 2026-08-24 != manifest 2026-08-23", issues)

    def test_audit_reports_generated_log_or_index_drift(self):
        audit_module._MAINTAIN_MODULE.migrate_legacy_log(self.root)
        compatibility = self.root / "wiki/log.md"
        compatibility.write_text("# Drifted compatibility view\n", encoding="utf-8")
        result = audit_module.audit(self.root, 220)
        self.assertTrue(
            any("generated artifact drift: wiki/log.md" in item for item in result["maintenance_generation_issues"])
        )

    def test_legacy_absolute_and_external_links_are_not_broken(self):
        target = self.root / "wiki/target.md"
        target.write_text("# Target\n", encoding="utf-8")
        encoded = quote(str(target), safe="/")
        source = self.root / "raw/example.md"
        source.parent.mkdir(parents=True)
        source.write_text(
            f"[inside]({encoded})\n[external](/Users/example/archive/memory.md)\n",
            encoding="utf-8",
        )
        self.write_manifest(
            [
                resource(["wiki/target.md"], "curated_knowledge", "target"),
                resource(["raw/example.md"], "canonical_evidence", "raw"),
            ]
        )
        result = audit_module.audit(self.root, 220)
        self.assertEqual(result["broken_links"], [])
        self.assertEqual({item["kind"] for item in result["legacy_links"]}, {"legacy_absolute", "legacy_external"})

    def test_index_wikilink_alias_may_contain_bracketed_date(self):
        target = self.root / "wiki/decision.md"
        target.write_text("# [2026-08-28] Decision\n", encoding="utf-8")
        (self.root / "wiki/index.md").write_text(
            "# Wiki\n\n- [[decision|[2026-08-28] Decision]]\n", encoding="utf-8"
        )
        self.write_manifest([resource(["wiki/decision.md"], "curated_knowledge", "decision")])

        result = audit_module.audit(self.root, 220)

        self.assertEqual(result["broken_links"], [])
        self.assertNotIn("wiki/decision.md", result["unindexed_wiki_pages"])

    def _write_cold_inventory(self, *, summary=None):
        original = self.root / ".backups/legacy-item"
        current = self.root / ".backups/cold-archive/legacy-item"
        current.mkdir(parents=True, exist_ok=True)
        (current / "receipt.txt").write_text("frozen bytes\n", encoding="utf-8")
        fingerprint = audit_module.tree_fingerprint(current)
        protected = {
            "canonical_registry": {
                "path": str(self.root / "wiki/project-dashboard-case-registry.json"),
                "tree_sha256": hashlib.sha256(b"registry").hexdigest(),
                "file_count": 1,
                "byte_count": 8,
            }
        }
        inventory = {
            "schema_version": "1.0",
            "scope_roots": [str(self.root)],
            "policy": {
                "cold_assets_are_canonical_inputs": False,
                "ambiguous_or_referenced_assets_move": False,
            },
            "summary": summary
            or {
                "moved_assets": 1,
                "retained_referenced_assets": 0,
                "deleted_reproducible_caches": 0,
                "retained_ambiguous_caches": 0,
            },
            "entries": [
                {
                    "asset_id": "cold-test",
                    "classification": "unreferenced_cold_backup",
                    "action": "moved_to_cold_archive",
                    "original_path": str(original),
                    "current_path": str(current),
                    **fingerprint,
                    "reference_evidence": [],
                }
            ],
            "migration_receipt": {
                "protected_before": protected,
                "protected_after": protected,
                "protected_hashes_equal": True,
                "moved_hashes_equal": True,
            },
        }
        path = self.root / audit_module.COLD_ASSET_INVENTORY_PATH
        path.write_text(json.dumps(inventory), encoding="utf-8")
        self.write_manifest(
            [resource([audit_module.COLD_ASSET_INVENTORY_PATH], "canonical_state", "cold inventory")]
        )
        return inventory, original, current

    def test_cold_inventory_rejects_action_count_drift(self):
        self._write_cold_inventory(
            summary={
                "moved_assets": 2,
                "retained_referenced_assets": 0,
                "deleted_reproducible_caches": 0,
                "retained_ambiguous_caches": 0,
            }
        )

        issues = audit_module.audit_cold_asset_inventory(self.root)

        self.assertIn("cold asset inventory summary drift: moved_assets", issues)

    def test_cold_inventory_rejects_hash_and_path_drift(self):
        inventory, original, current = self._write_cold_inventory()
        (current / "receipt.txt").write_text("changed bytes\n", encoding="utf-8")
        original.mkdir(parents=True)

        issues = audit_module.audit_cold_asset_inventory(self.root)

        self.assertIn(f"cold asset cold-test original path reappeared: {original.resolve()}", issues)
        self.assertIn("cold asset cold-test inventory drift: tree_sha256", issues)
        self.assertIn("cold asset cold-test inventory drift: byte_count", issues)
        self.assertEqual(inventory["summary"]["moved_assets"], 1)

    def test_cold_inventory_rejects_original_or_archived_locator_in_active_canonical_file(self):
        _, original, current = self._write_cold_inventory()
        canonical = self.root / "wiki/canonical.md"
        canonical.write_text(f"recovery source: {original}\n", encoding="utf-8")
        self.write_manifest(
            [
                resource([audit_module.COLD_ASSET_INVENTORY_PATH], "canonical_state", "cold inventory"),
                resource(["wiki/canonical.md"], "curated_knowledge", "knowledge"),
            ]
        )

        issues = audit_module.audit_cold_asset_inventory(self.root)

        self.assertIn("cold asset cold-test is referenced by active canonical file: wiki/canonical.md", issues)
        canonical.write_text(f"recovery source: {current}\n", encoding="utf-8")
        issues = audit_module.audit_cold_asset_inventory(self.root)
        self.assertIn("cold asset cold-test is referenced by active canonical file: wiki/canonical.md", issues)

    def test_cold_inventory_ignores_locator_in_derived_view(self):
        _, _, current = self._write_cold_inventory()
        derived = self.root / "wiki/derived.md"
        derived.write_text(f"display only: {current}\n", encoding="utf-8")
        self.write_manifest(
            [
                resource([audit_module.COLD_ASSET_INVENTORY_PATH], "canonical_state", "cold inventory"),
                resource(["wiki/derived.md"], "derived_view", "view"),
            ]
        )

        self.assertEqual(audit_module.audit_cold_asset_inventory(self.root), [])

    def _write_daily(self, family, date, *, coverage, status, candidates):
        if family == "chatgpt":
            raw_dir = self.root / "raw/conversations/chatgpt-daily/2026"
            source_dir = self.root / "wiki/sources/conversations/chatgpt-daily/2026"
            name = f"chatgpt-daily-report-{date}.md"
            receipt_name = f"chatgpt-daily-deposition-{date}.json"
        else:
            raw_dir = self.root / "raw/conversations/codex-daily/2026"
            source_dir = self.root / "wiki/sources/conversations/codex-daily/2026"
            name = f"codex-daily-report-{date}.md"
            receipt_name = f"codex-daily-deposition-{date}.json"
        raw_dir.mkdir(parents=True, exist_ok=True)
        source_dir.mkdir(parents=True, exist_ok=True)
        raw = raw_dir / name
        source = source_dir / name
        raw.write_text(f"---\ndate: {date}\n---\n# Raw\n", encoding="utf-8")
        source.write_text(
            f"---\ndate: {date}\ncoverage: {coverage}\nstatus: {status}\n---\n"
            "# Source\n\n## Structured Candidates\n\n```json\n"
            + json.dumps(candidates, ensure_ascii=False)
            + "\n```\n",
            encoding="utf-8",
        )
        receipt_dir = self.root / "wiki/review-cycles/daily-deposition/2026"
        receipt_dir.mkdir(parents=True, exist_ok=True)
        ids = [item["candidate_id"] for item in candidates]
        (receipt_dir / receipt_name).write_text(
            json.dumps(
                {
                    "source_family": family,
                    "source_date": date,
                    "status": "completed",
                    "raw_sha256": audit_module.sha256_file(raw),
                    "source_sha256": audit_module.sha256_file(source),
                    "candidate_count": len(ids),
                    "candidate_ids": ids,
                }
            ),
            encoding="utf-8",
        )

    def test_health_model_and_render_share_one_deterministic_model(self):
        candidate = {"candidate_id": "cand-test"}
        self._write_daily("chatgpt", "2026-08-23", coverage="partial", status="access_incomplete", candidates=[candidate])
        self._write_daily("codex", "2026-08-23", coverage="complete", status="ready", candidates=[])
        ledger = self.root / "wiki/review-cycles/promotion-ledger.json"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        # Production promotion-ledger.json uses a candidate-ID-keyed object.
        ledger.write_text(json.dumps({"candidates": {"cand-test": candidate}}), encoding="utf-8")
        registry = self.root / "wiki/project-dashboard-case-registry.json"
        registry.write_text(
            json.dumps({"version": 4, "cases": [{}], "events": [{}, {}], "value_candidates": [candidate]}),
            encoding="utf-8",
        )
        (self.root / "wiki/project-dashboard.md").write_text(
            "# 项目进度与价值沉淀（生成兼容视图）\n\n- 更新时间：fixed\n",
            encoding="utf-8",
        )
        extra = [
            resource(["raw/conversations/**"], "canonical_evidence", "raw"),
            resource(["wiki/sources/conversations/**"], "canonical_evidence", "source"),
            resource(["wiki/review-cycles/**", "wiki/project-dashboard-case-registry.json"], "canonical_state", "state"),
            resource(["wiki/project-dashboard.md", "wiki/memory-system-health.*"], "derived_view", "views"),
        ]
        self.write_manifest(extra)
        audit_report = audit_module.audit(self.root, 220)
        model = audit_module.build_health_model(
            self.root,
            audit_report,
            observed_at="2026-08-27T22:00:00+08:00",
            h5_health={"status": "unavailable", "reason": "test"},
        )
        self.assertEqual(model["daily"]["families"]["chatgpt"][0]["state"], "partial_access_incomplete")
        self.assertEqual(model["daily"]["families"]["codex"][0]["state"], "healthy")
        self.assertEqual(model["daily"]["summary"]["receipt_candidate_ids_missing_from_ledger"], [])
        self.assertEqual(model["derived_views"]["h5"]["status"], "unavailable")

        audit_module.write_health_artifacts(
            self.root,
            model,
            audit_module.HEALTH_JSON_PATH,
            audit_module.HEALTH_MARKDOWN_PATH,
        )
        first_json = (self.root / audit_module.HEALTH_JSON_PATH).read_bytes()
        first_md = (self.root / audit_module.HEALTH_MARKDOWN_PATH).read_bytes()
        audit_module.write_health_artifacts(
            self.root,
            model,
            audit_module.HEALTH_JSON_PATH,
            audit_module.HEALTH_MARKDOWN_PATH,
        )
        self.assertEqual(first_json, (self.root / audit_module.HEALTH_JSON_PATH).read_bytes())
        self.assertEqual(first_md, (self.root / audit_module.HEALTH_MARKDOWN_PATH).read_bytes())
        self.assertIn(b"generated by codex-memory-maintainer", first_md)


if __name__ == "__main__":
    unittest.main()
