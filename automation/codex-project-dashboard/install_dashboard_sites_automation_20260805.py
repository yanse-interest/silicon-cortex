#!/usr/bin/env python3
"""Install the guarded daily Sites dashboard publisher and deconflict local refresh."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import time
import tomllib
from pathlib import Path


AUTOMATION_ID = "publish-project-dashboard-sites"
DEFAULT_AUTOMATION = Path(f"/Users/shiba/.codex/automations/{AUTOMATION_ID}/automation.toml")
DEFAULT_PLIST = Path(
    "/Users/shiba/Library/LaunchAgents/com.shiba.codex-project-dashboard-h5-refresh.plist"
)
DEFAULT_BACKUP_DIR = Path(__file__).resolve().parent / "migration-backups"
TARGET_PROJECT_ID = "ca6ec531-89e1-48f5-b975-c1fbf1f22918"


PROMPT = r"""使用 $automation-routing-policy、$sites:sites-building 和 $sites:sites-hosting。

目标：每天 07:30（Asia/Shanghai）在前一自然日 ChatGPT 与 Codex 日报来源门禁通过后，导出项目 Dashboard H5 快照、完成测试，并把新版本自动发布到既有的仅本人访问 Sites 站点。浏览器刷新本身不触发本机导出；本自动化负责让已发布版本保持最新。

账号与安全硬约束（任何读取、构建、Git 或 Sites 写入前执行）：
- 运行 `/Users/shiba/bin/routerctl status --json`，只读取状态，不执行 switch/login/bind/auto/compatibility。
- 只有 `desktop.currentAccountId == "account-1"`、`paused == true`、`autoSwitchWhenSafe == false` 且 `automationStatus == "disabled"` 时继续。
- 任一条件不满足或无法确认时立即 `user_handoff`，reason code 为 `account_1_required`；不得自动切号、回退账号 2 或修复认证。
- 使用固定 Sites 项目 `.openai/hosting.json` 中现有 `project_id`；绝不调用 create_site，不改变域名、访问模式、允许用户、环境变量、D1 或 R2。
- 仅允许 `deploy_private_site_version`。若无法验证站点是 owner-only（只有当前 owner、无 groups、无 external visitors），立即 `user_handoff`，不得改成 shared/public 发布。

固定路径：
- Dashboard 数据项目：`/Users/shiba/Documents/codex/projects/automation/codex-project-dashboard`
- H5 站点项目：`/Users/shiba/Documents/codex/projects/automation/codex-project-dashboard-h5`
- Sites project id：`appgprj_6a700e35e7ec8191ad07c64cfc04b4a0`
- Sites 打包工具：`/Users/shiba/.codex/plugins/cache/openai-bundled/sites/0.1.34/scripts/package-site.sh`

按以下顺序执行，任一步失败都不得发布半成品：
1. 计算 Asia/Shanghai 前一自然日 TARGET_DATE。确认 H5 Git 工作树没有与本次无关的 tracked 改动；导出前只允许 `public/dashboard-snapshot.json` 已有改动。若存在其他 tracked 改动，停止并输出 `needs_high_rerun` / `site_worktree_dirty`，不得自动合并、stash、丢弃或提交。
2. 在 Dashboard 数据项目运行 `python3 export_h5_snapshot.py`，不得使用 `--skip-gates`。必须得到 `ok: true`、`source_gate.ready: true`，且 `daily_updated_through == TARGET_DATE`；否则保留上一线上版本并停止。`user_handoff` 仍按账号/权限边界报告，普通日报尚未就绪使用 `failed_medium` / `daily_source_gate_not_ready`，`safe_to_retry: true`。
3. 校验导出的 `public/dashboard-snapshot.json`：所有展示为已核验的考点必须有非空答案和 evidence refs；不得含 `source_locator`、`excerpt`、`chatgpt.com/c/`、`/Users/` 或本机绝对路径。失败时停止，reason code 为 `snapshot_privacy_or_evidence_failed`。
4. 在 H5 项目运行 `npm test`。必须构建成功且测试全部通过。构建成功后必须运行 `launchctl kickstart -k gui/$(id -u)/com.shiba.codex-project-dashboard-h5`，等待服务恢复，再在 Dashboard 数据项目运行 `python3 dashboard_local_delivery_check.py`。只有 127 与 `mbp.local` 的根 HTML、静态 JSON、目标日期/生成时间/topic marker 和 no-store 策略全部匹配时才继续；不得只检查 `public/` 或 `dist/` 文件。失败使用 `failed_medium`，reason code 采用检查器返回的 `local_server_bundle_stale`、`local_snapshot_mismatch`、`local_h5_cache_policy_invalid` 或 `local_h5_unavailable`，并停止线上发布。
5. 记录快照中的 TARGET_GENERATED_AT。检查站点与版本状态。如果快照相对 Git HEAD 未变化，且 Sites 当前线上版本已对应同一 HEAD，不创建重复版本，但仍必须继续执行第 10 步的当前网络交付回读；不得仅凭 connector 状态输出 `idempotent_complete`。
6. 若快照有变化，只 stage `public/dashboard-snapshot.json`；运行 cached diff check，提交为 `Refresh dashboard snapshot TARGET_DATE`。不得 stage 其他文件，尤其不得提交日志、临时文件、Memory 内容或 secret。
7. 获取短期 Sites source repository write credential。用每命令 HTTP authorization header 将当前 `main` HEAD 推送到该 credential 返回的远端与 branch；不得把 token 写入 remote URL、Git config、文件、日志或最终输出。推送后的完整 HEAD SHA 作为 commit_sha。
8. 运行 Sites package-site.sh，以 H5 项目目录和 `/tmp` 下唯一 tar 路径打包。Archive 必须来自当前 clean HEAD，并包含 `dist/server/index.js` 与 `.openai/hosting.json`。
9. 有新版本时，以该 commit_sha 和 archive 保存一个 Sites version，随后调用 `deploy_private_site_version`，并轮询 `get_deployment_status` 直至 succeeded 或 failed。成功后删除临时 tar；失败也清理 tar，不回滚或覆盖上一成功版本。connector `succeeded` 只表示部署成功，不能单独作为 normal completion。
10. 无论本轮新部署还是 idempotent，都从 `get_site` 取得当前 production URL 与短期 `siwc_bypass_bearer_token`，只通过进程环境 `SITES_READBACK_BEARER_TOKEN` 传给 `python3 dashboard_sites_delivery_check.py --url PRODUCTION_URL --expected-date TARGET_DATE --expected-generated-at TARGET_GENERATED_AT`；不得把 bearer 写入参数、文件、日志或最终输出。检查器必须对 `dashboard-snapshot.json` 发起 authenticated/private GET，并同时回读完全相同的 `daily_updated_through` 和 `generated_at`。
11. 只有 `delivery_status == "usable"` 才报告 `routing_status: normal_complete`（无新版本且回读可用时可报告 `idempotent_complete`）。若结果是 `deployed_but_edge_blocked`，必须报告 `routing_status: user_handoff`、reason code `cloudflare_edge_blocked`、非敏感 `cf-ray`/colo、production URL 和本地回退 `http://mbp.local:8792/`；不得重部署、修改 allowlist 或转为 shared/public。marker 不一致使用 `failed_medium` / `delivery_marker_mismatch`，私有认证失败使用 `user_handoff` / `auth_block`。任何情况下都不得把“部署 succeeded”误写成“当前网络可用”。

该任务是稳定的拉取、验证、构建和发布工作，固定使用 `gpt-5.6-terra` / `medium`。遇到需要修改 skill、自动化 prompt、发布脚本、schema 或历史数据时，不在本次 medium run 内修改；输出 `needs_high_rerun` 并推荐 `gpt-5.6-sol high`。

正常摘要之后必须严格输出：

delivery_status: usable | deployed_but_edge_blocked | delivery_marker_mismatch | private_auth_failed | delivery_check_failed
routing_status: normal_complete | idempotent_complete | needs_high_rerun | user_handoff | failed_medium
reason_codes: []
recommended_next_effort: none | gpt-5.6-sol high
safe_to_retry: true | false
"""


def render_toml(created_at: int) -> str:
    now = int(time.time() * 1000)
    fields = [
        "version = 1",
        f"id = {json.dumps(AUTOMATION_ID)}",
        'kind = "cron"',
        'name = "每日发布项目 Dashboard Sites"',
        f"prompt = {json.dumps(PROMPT, ensure_ascii=False)}",
        'status = "ACTIVE"',
        'rrule = "FREQ=DAILY;BYHOUR=7;BYMINUTE=30"',
        'model = "gpt-5.6-terra"',
        'reasoning_effort = "medium"',
        'execution_environment = "local"',
        f'target = {{ type = "project", project_id = "{TARGET_PROJECT_ID}" }}',
        'cwds = ["/Users/shiba/Documents/codex/projects/automation/codex-project-dashboard-h5"]',
        f"created_at = {created_at}",
        f"updated_at = {now}",
    ]
    return "\n".join(fields) + "\n"


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--automation", type=Path, default=DEFAULT_AUTOMATION)
    parser.add_argument("--plist", type=Path, default=DEFAULT_PLIST)
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    created_at = int(time.time() * 1000)
    if args.automation.exists():
        for line in args.automation.read_text(encoding="utf-8").splitlines():
            if line.startswith("created_at = "):
                created_at = int(line.split("=", 1)[1].strip())
                break
    automation_text = render_toml(created_at)
    parsed = tomllib.loads(automation_text)
    if parsed.get("id") != AUTOMATION_ID or parsed.get("model") != "gpt-5.6-terra":
        raise RuntimeError("rendered automation TOML failed validation")

    with args.plist.open("rb") as handle:
        plist = plistlib.load(handle)
    interval = plist.get("StartCalendarInterval")
    if not isinstance(interval, dict):
        raise RuntimeError("refresh LaunchAgent has no StartCalendarInterval dictionary")
    if interval.get("Hour") != 7 or interval.get("Minute") not in {20, 30}:
        raise RuntimeError(f"unexpected existing refresh schedule: {interval!r}")
    interval["Hour"] = 7
    interval["Minute"] = 20
    plist_bytes = plistlib.dumps(plist, fmt=plistlib.FMT_XML, sort_keys=False)

    print(json.dumps({
        "automation": str(args.automation),
        "automation_schedule": "07:30",
        "model": "gpt-5.6-terra",
        "reasoning_effort": "medium",
        "local_refresh_schedule": "07:20",
        "dry_run": args.dry_run,
    }, ensure_ascii=False, indent=2))
    if args.dry_run:
        return 0

    args.backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    if args.automation.exists():
        shutil.copy2(
            args.automation,
            args.backup_dir / f"{AUTOMATION_ID}-before-{stamp}.toml",
        )
    shutil.copy2(
        args.plist,
        args.backup_dir / f"com.shiba.codex-project-dashboard-h5-refresh-before-{stamp}.plist",
    )
    atomic_write(args.automation, automation_text.encode("utf-8"))
    atomic_write(args.plist, plist_bytes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
