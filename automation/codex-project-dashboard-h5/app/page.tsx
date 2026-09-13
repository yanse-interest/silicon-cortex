import type { Metadata } from "next";
import snapshot from "../public/dashboard-snapshot.json";
import { DashboardClient } from "./dashboard-client";

export const metadata: Metadata = {
  title: "ChatGPT + Codex 项目看板",
  description: "每天汇总 ChatGPT 与 Codex 的项目进度、开放项和价值沉淀。",
};

export default function Home() {
  return <DashboardClient snapshot={snapshot} />;
}
