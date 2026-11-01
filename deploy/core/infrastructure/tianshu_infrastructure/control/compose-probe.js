// Read-only identity probe in Dockge's PID namespace.
const fs = require("fs");
const [pid, expected] = process.argv.slice(1);
try {
  const stat = fs.readFileSync("/proc/" + pid + "/stat", "utf8");
  const fields = stat.slice(stat.lastIndexOf(")") + 1).trim().split(/\s+/);
  process.stdout.write(JSON.stringify({ active: fields[0] !== "Z" && fields[19] === expected }));
} catch (error) {
  process.stdout.write(JSON.stringify({ active: error.code === "ENOENT" ? false : null }));
}
