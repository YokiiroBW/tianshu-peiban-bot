// Node-only synthetic process proof for the existing Dockge Linux context.
const fs = require("fs");
const cp = require("child_process");
const path = require("path");
const crypto = require("crypto");
const assert = require("assert/strict");

async function main() {
  const directory = process.argv[1];
  assert(/^\/tmp\/tianshu-control-proof-[a-z0-9_-]+$/.test(directory));
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  const input = JSON.parse(Buffer.concat(chunks));
  for (const name of ["wrapper", "probe"]) {
    assert.equal(crypto.createHash("sha256").update(input[name]).digest("hex"), input[name + "_sha256"]);
  }
  fs.mkdirSync(directory, { mode: 0o700 });
  function launch(marker, seconds, argv) {
    const process = cp.spawn(global.process.execPath, ["-e", input.wrapper, marker, seconds, JSON.stringify(argv)], {
      stdio: ["pipe", "pipe", "pipe"],
    });
    return process;
  }
  async function closed(process) {
    return await new Promise((resolve, reject) => {
      const deadline = setTimeout(() => { process.kill("SIGKILL"); reject(new Error("node_proof_timeout")); }, 6000);
      process.on("error", reject);
      process.on("close", (code) => { clearTimeout(deadline); resolve(code); });
    });
  }
  const marker = path.join(directory, "timeout.json");
  const grandchild = path.join(directory, "grandchild.pid");
  const code = "const cp=require('child_process'),fs=require('fs');const p=cp.spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore'});fs.writeFileSync(process.argv[1],String(p.pid));process.stdout.write('synthetic-sensitive-output');setInterval(()=>{},1000)";
  const timeout = launch(marker, ".6", [process.execPath, "-e", code, grandchild]);
  timeout.stdout.resume();
  timeout.stderr.resume();
  timeout.stdin.end("synthetic-private-input");
  assert.equal(await closed(timeout), 124);
  const result = JSON.parse(fs.readFileSync(marker, "utf8"));
  assert.equal(result.state, "complete");
  assert.equal(result.timed_out, true);
  assert(!fs.readFileSync(marker, "utf8").includes("synthetic"));
  const pid = Number(fs.readFileSync(grandchild, "utf8"));
  let descendantTerminated = false;
  try {
    const stat = fs.readFileSync("/proc/" + pid + "/stat", "utf8");
    descendantTerminated = stat.slice(stat.lastIndexOf(")") + 1).trim().split(/\s+/)[0] === "Z";
  } catch (error) { descendantTerminated = error.code === "ENOENT"; }
  assert(descendantTerminated);
  const probe = cp.spawnSync(process.execPath, ["-e", input.probe, String(pid), "unmatched-creation-tick"], { encoding: "utf8" });
  assert.equal(probe.status, 0);
  assert.deepEqual(JSON.parse(probe.stdout), { active: false });

  const completion = path.join(directory, "closed-output.json");
  const finish = "process.stdin.resume();process.stdin.on('end',()=>setTimeout(()=>{process.stdout.write('synthetic-secret-output');process.stderr.write('synthetic-secret-error');},200))";
  const disconnected = launch(completion, "2", [process.execPath, "-e", finish]);
  disconnected.stdout.destroy();
  disconnected.stderr.destroy();
  disconnected.stdin.end("synthetic-private-input");
  assert.equal(await closed(disconnected), 0);
  assert.deepEqual(JSON.parse(fs.readFileSync(completion, "utf8")), { state: "complete", exit_code: 0, timed_out: false });
  process.stdout.write(JSON.stringify({ status: "accepted", scope: "dockge_node_synthetic_processes", tests: 2,
    timeout_killed_descendant: true, closed_output_completion_preserved: true, original_probe_checked: true,
    node: process.version, docker_or_compose_mutation: false, proof_directory: directory }) + "\n");
}

main().catch(() => { process.stderr.write("control_node_proof_failed\n"); process.exitCode = 2; });
