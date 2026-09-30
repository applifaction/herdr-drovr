#!/usr/bin/env node
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

interface Pick {
  stdout: string;
  status?: number;
}

interface Call {
  tool: "herdr" | "fzf";
  args: string[];
}

// Exercise the real entrypoint and argv protocol, not a reimplementation of
// the flow. Only the external Herdr/fzf executables are replaced.
function runFlow(mode: "tab" | "pane", picks: Pick[], options: { split?: boolean; stale?: boolean } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "drovr-flow-"));
  try {
    fs.writeFileSync(path.join(dir, "state.json"), JSON.stringify({ picks, calls: [], ...options }));
    const fake = `#!${process.execPath}
import fs from "node:fs";
import path from "node:path";
const file = path.join(process.env.DROVR_TEST_DIR, "state.json");
const state = JSON.parse(fs.readFileSync(file, "utf8"));
const tool = path.basename(process.argv[1]);
const args = process.argv.slice(2);
state.calls.push({tool, args});
let result = {};
let stdout;
let status = 0;
if (tool === "fzf") {
  const pick = state.picks.shift();
  if (!pick) throw new Error("Unexpected extra fzf call");
  stdout = pick.stdout;
  status = pick.status ?? 0;
} else {
  const command = args.slice(0, 2).join(" ");
  const rect = (x, y, width, height) => ({x, y, width, height});
  const panes = state.split
    ? [
      {pane_id: "w1:p1", rect: rect(0, 0, 60, 60), focused: true},
      {pane_id: "w1:p2", rect: rect(60, 0, 40, 30), focused: false},
      {pane_id: "w1:p3", rect: rect(60, 30, 40, 30), focused: false},
    ]
    : [{pane_id: "w1:p1", rect: rect(0, 0, 100, 60), focused: true}];
  switch (command) {
    case "pane layout": result = {layout: {
      tab_id: "w1:t1", workspace_id: "w1", area: rect(0, 0, 100, 60), panes,
      splits: state.split ? [
        {direction: "right", ratio: 0.6, rect: rect(0, 0, 100, 60)},
        {direction: "down", ratio: 0.5, rect: rect(60, 0, 40, 60)},
      ] : [],
    }}; break;
    case "pane get": result = {pane: {pane_id: "w1:p1", tab_id: "w1:t1", workspace_id: "w1"}}; break;
    case "pane list": result = {panes: state.stale ? [] : panes.map(p => ({...p, tab_id: "w1:t1"}))}; break;
    case "tab list": result = {tabs: [{tab_id: "w1:t1", workspace_id: "w1", label: "Original tab"}]}; break;
    case "workspace list": result = {workspaces: [{workspace_id: "w2", label: "Target"}]}; break;
    case "pane move": result = {move_result: {changed: true, pane: {
      pane_id: args[2].replace("w1:", "w2:"), tab_id: "w2:t2", workspace_id: "w2",
    }}}; break;
    default: throw new Error("Unexpected Herdr command: " + args.join(" "));
  }
}
fs.writeFileSync(file, JSON.stringify(state));
process.stdout.write(stdout ?? JSON.stringify({result}));
process.exitCode = status;
`;
    for (const tool of ["herdr", "fzf"]) {
      fs.writeFileSync(path.join(dir, tool), fake, { mode: 0o700 });
    }
    const result = spawnSync(process.execPath, [path.join(import.meta.dirname, "pick-and-move.ts")], {
      encoding: "utf8",
      input: "",
      timeout: 10_000,
      env: {
        ...process.env,
        PATH: `${dir}${path.delimiter}${process.env.PATH ?? ""}`,
        HERDR_BIN_PATH: path.join(dir, "herdr"),
        DROVR_MODE: mode,
        DROVR_PANE: "w1:p1",
        DROVR_TEST_DIR: dir,
      },
    });
    assert.ifError(result.error);
    const state = JSON.parse(fs.readFileSync(path.join(dir, "state.json"), "utf8")) as { calls: Call[]; picks: Pick[] };
    assert.equal(state.picks.length, 0, "all scripted picker interactions must be consumed");
    return {
      status: result.status,
      stderr: result.stderr,
      calls: state.calls,
      moves: state.calls.filter(c => c.tool === "herdr" && c.args[1] === "move").map(c => c.args),
      pickers: state.calls.filter(c => c.tool === "fzf").map(c => c.args),
    };
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

const tabNew = (query = ""): Pick => ({ stdout: `${query}\n＋ new workspace\tnew-ws\n` });
const paneNew = (query = ""): Pick => ({ stdout: `${query}\n\n＋ new workspace\tnew-ws\n` });
const name = (value: string): Pick => ({ stdout: `${value}\nCreate workspace\n` });
const valueOf = (args: string[], flag: string) => args[args.indexOf(flag) + 1];

for (const split of [false, true]) {
  test(`tab move focuses atomically in the final move (split=${split})`, () => {
    const result = runFlow("tab", [{ stdout: "\nTarget\tw2\n" }], { split });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.moves.length, split ? 3 : 1);
    for (const args of result.moves.slice(0, -1)) assert.equal(args.at(-1), "--no-focus");
    assert.equal(result.moves.at(-1)?.at(-1), "--focus");
    assert.equal(result.calls.at(-1)?.args[1], "move", "no post-move command may be needed for focus");
    if (split) {
      assert.equal(valueOf(result.moves[1]!, "--split"), "right");
      assert.equal(valueOf(result.moves[1]!, "--ratio"), "0.6");
      assert.equal(valueOf(result.moves[2]!, "--target-pane"), "w2:p2");
      assert.equal(valueOf(result.moves[2]!, "--split"), "down");
      assert.equal(valueOf(result.moves[2]!, "--ratio"), "0.5");
    }
  });
}

test("new workspace has an explicit trimmed name, while the moved tab keeps its label", () => {
  const workspaceName = `Project 'Ü' $HOME; $(touch should-not-run)`;
  const result = runFlow("tab", [tabNew("search text"), name(`  ${workspaceName}  `)]);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(valueOf(result.pickers[1]!, "--query"), "search text");
  assert.equal(valueOf(result.moves[0]!, "--label"), workspaceName);
  assert.equal(valueOf(result.moves[0]!, "--tab-label"), "Original tab");
});

for (const mode of ["tab", "pane"] as const) {
  const pickNew = mode === "tab" ? tabNew : paneNew;
  test(`${mode}: blank workspace name reopens the prompt; never uses the tab name`, () => {
    const result = runFlow(mode, [pickNew(), name("  \t "), name("New project")]);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(valueOf(result.pickers[1]!, "--query"), "");
    assert.match(valueOf(result.pickers[2]!, "--header")!, /cannot be empty/);
    assert.equal(valueOf(result.moves[0]!, "--label"), "New project");
    assert.equal(result.moves[0]?.at(-1), "--focus");
  });
  test(`${mode}: cancelling the name prompt makes no changes`, () => {
    const result = runFlow(mode, [pickNew(), { stdout: "", status: 130 }]);
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(result.moves, []);
  });
  test(`${mode}: a failed name prompt makes no changes`, () => {
    const result = runFlow(mode, [pickNew(), { stdout: "", status: 2 }]);
    assert.equal(result.status, 1);
    assert.deepEqual(result.moves, []);
  });
  test(`${mode}: revalidates source after the name prompt`, () => {
    const result = runFlow(mode, [pickNew(), name("Project")], { stale: true });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /source .* changed/);
    assert.deepEqual(result.moves, []);
  });
  test(`${mode}: cancelling the destination picker makes no changes`, () => {
    const result = runFlow(mode, [{ stdout: "", status: 130 }]);
    assert.equal(result.status, 0);
    assert.deepEqual(result.moves, []);
  });
}

test("pane move into an existing tab keeps alt-d and focuses in the move", () => {
  const result = runFlow("pane", [{ stdout: "\nalt-d\nTarget\ttab:w2:t1\n" }]);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(valueOf(result.moves[0]!, "--tab"), "w2:t1");
  assert.equal(valueOf(result.moves[0]!, "--split"), "down");
  assert.equal(result.moves[0]?.at(-1), "--focus");
  assert.equal(result.pickers.length, 1);
});

test("new tab still uses the search query without a workspace-name prompt", () => {
  const result = runFlow("pane", [{ stdout: "  Logs  \n\n＋ new tab\tnew-tab\n" }]);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(valueOf(result.moves[0]!, "--label"), "Logs");
  assert.equal(result.moves[0]?.at(-1), "--focus");
  assert.equal(result.pickers.length, 1);
});
