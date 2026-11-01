// Fixed Dockge Node wrapper. Configuration arrives on stdin, never in receipts.
const fs = require("fs");
const cp = require("child_process");
const path = require("path");
const [marker, seconds, encoded] = process.argv.slice(1);
const argv = JSON.parse(encoded);

function write(value) {
  const temporary = marker + ".pending";
  const fd = fs.openSync(temporary, "w", 0o600);
  fs.writeSync(fd, JSON.stringify(value));
  fs.fsyncSync(fd);
  fs.closeSync(fd);
  fs.renameSync(temporary, marker);
  const directory = fs.openSync(path.dirname(marker), "r");
  fs.fsyncSync(directory);
  fs.closeSync(directory);
}

write({ state: "launching" });
let child;
const environment = { ...process.env, LC_ALL: "C" };
for (const key of Object.keys(environment)) {
  if (key.startsWith("COMPOSE_") || ["DOCKER_HOST", "DOCKER_CONTEXT", "SSH_AUTH_SOCK"].includes(key)) delete environment[key];
}
try {
  child = cp.spawn(argv[0], argv.slice(1), {
    detached: true,
    stdio: ["pipe", "pipe", "pipe"],
    env: environment,
  });
} catch (error) {
  write({ state: "complete", exit_code: 127, timed_out: false });
  process.exit(127);
}

let ended = false;
let timed = false;
process.stdout.on("error", () => {});
process.stderr.on("error", () => {});
child.stdout.on("data", (bytes) => {
  if (!process.stdout.destroyed) process.stdout.write(bytes);
});
child.stderr.on("data", (bytes) => {
  if (!process.stderr.destroyed) process.stderr.write(bytes);
});
child.stdin.on("error", () => {});
process.stdin.on("error", () => {});
process.stdin.pipe(child.stdin);

function stopGroup() {
  if (child.pid) {
    try { process.kill(-child.pid, "SIGKILL"); } catch (error) {}
  }
}

child.on("spawn", () => {
  try {
    const stat = fs.readFileSync("/proc/" + child.pid + "/stat", "utf8");
    const fields = stat.slice(stat.lastIndexOf(")") + 1).trim().split(/\s+/);
    write({ state: "running", pid: child.pid, created: fields[19] });
  } catch (error) {
    // A fast child can exit before /proc is sampled; close supplies its receipt.
  }
});

const timer = setTimeout(() => {
  timed = true;
  stopGroup();
}, Number(seconds) * 1000);

child.on("error", () => {
  if (ended) return;
  ended = true;
  clearTimeout(timer);
  stopGroup();
  write({ state: "complete", exit_code: 127, timed_out: false });
  process.exit(127);
});

child.on("close", (code) => {
  if (ended) return;
  ended = true;
  clearTimeout(timer);
  stopGroup();
  write({ state: "complete", exit_code: code === null ? 128 : code, timed_out: timed });
  process.exit(timed ? 124 : code === null ? 128 : code);
});
