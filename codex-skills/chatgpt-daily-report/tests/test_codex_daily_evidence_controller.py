from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "codex_daily_evidence_controller.py"
SPEC = importlib.util.spec_from_file_location("codex_daily_evidence_controller", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def bridge(tasks: list[dict]) -> dict:
    return {
        "schema_version": 1,
        "generated_at": "2026-08-06T00:00:00Z",
        "retention_days": 14,
        "tasks": tasks,
    }


def task(thread: str, turn: str | None, *, status: str = "completed") -> dict:
    return {
        "thread_id": thread,
        "turn_id": turn,
        "status": status,
        "started_at": "2026-08-05T01:00:00Z",
        "updated_at": "2026-08-05T02:00:00Z",
        "ended_at": "2026-08-05T02:00:00Z",
        "cwd": "/workspace/project",
        "changed_files": [{"path": "src/app.py", "sha256": "x"}],
    }


def fake_server(path: Path, behavior: str = "success") -> None:
    path.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import json, sys
            reads = 0
            for line in sys.stdin:
                msg = json.loads(line)
                if "id" not in msg:
                    continue
                method = msg["method"]
                if method == "initialize":
                    result = {{"userAgent":"fake","codexHome":"/tmp","platformFamily":"unix","platformOs":"macos"}}
                    print(json.dumps({{"id":msg["id"],"result":result}}), flush=True)
                    continue
                reads += 1
                behavior = {behavior!r}
                if behavior == "method_missing":
                    print(json.dumps({{"id":msg["id"],"error":{{"code":-32601,"message":"Method not found"}}}}), flush=True)
                    continue
                if behavior == "transient_once" and reads == 1:
                    print(json.dumps({{"id":msg["id"],"error":{{"code":-32000,"message":"database is locked"}}}}), flush=True)
                    continue
                thread_id = msg["params"]["threadId"]
                if behavior == "first_thread_unavailable" and thread_id == "thread-1":
                    print(json.dumps({{"id":msg["id"],"error":{{"code":-32000,"message":"thread not loaded"}}}}), flush=True)
                    continue
                turns = []
                if msg["params"].get("includeTurns"):
                    turns = [{{"id":"turn-1","status":"completed","items":[
                        {{"type":"userMessage","content":[{{"type":"text","text":"Build it password=secret-value"}}]}},
                        {{"type":"reasoning","content":["private reasoning"]}},
                        {{"type":"commandExecution","command":"dangerous command","output":"secret tool output"}},
                        {{"type":"fileChange","changes":[{{"path":"src/new.py","kind":{{"type":"add"}},"diff":"secret diff"}}]}},
                        {{"type":"agentMessage","phase":"final_answer","text":"Implemented; 3 tests passed."}}
                    ]}}]
                print(json.dumps({{"id":msg["id"],"result":{{"thread":{{
                    "id":thread_id,
                    "projectId":"project-authoritative",
                    "cwd":"/workspace/project-from-thread",
                    "turns":turns
                }}}}}}), flush=True)
            """
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)


class ControllerTests(unittest.TestCase):
    def test_manifest_deduplicates_and_tracks_placeholders(self) -> None:
        data = bridge([task("thread-1", "turn-1"), task("thread-1", "turn-1"), task("thread-2", None)])
        manifest = MODULE.build_manifest(data, "2026-08-05", "Asia/Shanghai")
        self.assertEqual(len(manifest.refs), 1)
        self.assertEqual(manifest.duplicate_references, 1)
        self.assertEqual(manifest.null_turn_references, 1)

    def test_empty_valid_manifest_is_no_tasks_without_rpc(self) -> None:
        manifest = MODULE.build_manifest(bridge([]), "2026-08-05", "Asia/Shanghai")
        result = MODULE.collect_evidence(
            manifest,
            codex_binary=Path("/does/not/exist"),
            codex_home=Path("/tmp"),
            max_workers=2,
            max_attempts=3,
            request_timeout=1,
        )
        self.assertEqual(result["status"], "no_tasks")
        self.assertEqual(result["coverage"], "complete")

    def test_projection_is_bounded_and_omits_forbidden_items(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "fake-codex"
            fake_server(binary)
            manifest = MODULE.build_manifest(bridge([task("thread-1", "turn-1")]), "2026-08-05", "Asia/Shanghai")
            result = MODULE.collect_evidence(
                manifest,
                codex_binary=binary,
                codex_home=Path(directory),
                max_workers=2,
                max_attempts=3,
                request_timeout=2,
            )
            serialized = json.dumps(result, ensure_ascii=False)
            self.assertEqual(result["status"], "ready")
            self.assertIn("Implemented; 3 tests passed.", serialized)
            self.assertIn("password=[REDACTED]", serialized)
            self.assertNotIn("private reasoning", serialized)
            self.assertNotIn("dangerous command", serialized)
            self.assertNotIn("secret tool output", serialized)
            self.assertNotIn("secret diff", serialized)
            self.assertEqual(result["threads"][0]["project_context"], {
                "project_id": "project-authoritative",
                "cwd": "/workspace/project-from-thread",
                "identity_source": "thread_project_id",
            })

    def test_project_context_falls_back_to_selector_cwd_without_guessing(self) -> None:
        ref = MODULE.TaskRef(
            thread_id="thread-1", turn_id="turn-1", bridge_status="completed",
            started_at="", updated_at="", ended_at="", cwd="/workspace/narrow",
            changed_files=(),
        )
        self.assertEqual(
            MODULE._thread_project_context({"id": "thread-1"}, (ref,)),
            {
                "project_id": "",
                "cwd": "/workspace/narrow",
                "identity_source": "bridge_cwd",
            },
        )

    def test_canary_retries_transient_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "fake-codex"
            fake_server(binary, "transient_once")
            manifest = MODULE.build_manifest(bridge([task("thread-1", "turn-1")]), "2026-08-05", "Asia/Shanghai")
            result = MODULE.collect_evidence(
                manifest,
                codex_binary=binary,
                codex_home=Path(directory),
                max_workers=1,
                max_attempts=3,
                request_timeout=2,
            )
            self.assertEqual(result["canary"]["attempts"], 2)
            self.assertEqual(result["status"], "ready")

    def test_canary_fails_fast_on_missing_method(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "fake-codex"
            fake_server(binary, "method_missing")
            manifest = MODULE.build_manifest(bridge([task("thread-1", "turn-1")]), "2026-08-05", "Asia/Shanghai")
            with self.assertRaises(MODULE.ControllerError) as raised:
                MODULE.collect_evidence(
                    manifest,
                    codex_binary=binary,
                    codex_home=Path(directory),
                    max_workers=1,
                    max_attempts=3,
                    request_timeout=2,
                )
            self.assertEqual(raised.exception.code, "rpc_method_unavailable")

    def test_canary_skips_unavailable_thread_and_retains_partial_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "fake-codex"
            fake_server(binary, "first_thread_unavailable")
            manifest = MODULE.build_manifest(
                bridge([task("thread-1", "turn-1"), task("thread-2", "turn-1")]),
                "2026-08-05",
                "Asia/Shanghai",
            )
            result = MODULE.collect_evidence(
                manifest,
                codex_binary=binary,
                codex_home=Path(directory),
                max_workers=2,
                max_attempts=3,
                request_timeout=2,
            )
            self.assertEqual(result["canary"]["thread_id"], "thread-2")
            self.assertEqual(result["canary"]["data_failures"], 1)
            self.assertEqual(result["summary"]["found_turns"], 1)
            self.assertEqual(result["summary"]["missing_turns"], 1)
            self.assertEqual(result["coverage"], "partial")
            self.assertEqual(result["status"], "access_incomplete")


if __name__ == "__main__":
    unittest.main()
