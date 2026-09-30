#!/usr/bin/env node
// Popup geometry is fixed at creation and Herdr permits only one popup.
// This short-lived, detached opener survives the old picker exiting, then
// opens the compact name form with the pending move context intact.
import { spawn, spawnSync } from "node:child_process";
import { setTimeout } from "node:timers/promises";

export function openWorkspaceNameDialog(request: string): number {
  const child = spawn(process.execPath, [import.meta.filename, request], {
    detached: true,
    stdio: "ignore",
    env: process.env,
  });
  // spawn failures are asynchronous; keep an error listener even when a PID
  // was returned. No shell interpolation or files containing move state.
  child.on("error", (err) => console.error(`drovr: could not open name dialog: ${err.message}`));
  if (!child.pid) throw new Error("could not start the workspace-name dialog");
  child.unref();
  return 0;
}

async function main(): Promise<number> {
  const hb = process.env.HERDR_BIN_PATH || "herdr";
  const request = process.argv[2];
  if (!request) throw new Error("missing workspace move context");
  let message = "the previous picker did not close";
  // Wait only for the old popup to be reaped. Retry no other failure, and
  // never close an arbitrary popup or retry the actual pane move.
  const deadline = Date.now() + 5_000;
  do {
    const result = spawnSync(hb, [
      "plugin", "pane", "open",
      "--plugin", process.env.HERDR_PLUGIN_ID || "drovr",
      "--entrypoint", "workspace-name",
      "--env", `DROVR_WORKSPACE_MOVE=${request}`,
    ], { encoding: "utf8", timeout: Math.max(1, deadline - Date.now()) });
    if (result.status === 0) return 0;
    message = result.error?.message || (result.stderr || result.stdout || "could not open dialog").trim();
    // Current Herdr: ui_busy / "a popup pane is already open". Older
    // popup launch paths report "popup already open" instead.
    if (!/\bpopup(?: pane)? (?:is )?already open\b/.test(message)) break;
    await setTimeout(50);
  } while (Date.now() < deadline);
  spawnSync(hb, ["notification", "show", "drovr: workspace dialog failed", "--body", message], {
    stdio: "ignore",
    timeout: 5_000,
  });
  return 1;
}

if (process.argv[1] === import.meta.filename) {
  try {
    process.exitCode = await main();
  } catch (err) {
    console.error(err instanceof Error ? err.message : String(err));
    process.exitCode = 1;
  }
}
