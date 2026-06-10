// harness-methodology benchmark runner — the `performance` gate dimension
// runs `node benchmarks/run.mjs` and parses the JSON printed below
// (harness/tool_runners.py _score_js_bench):
//   mean > 3000 ms → −50 per benchmark, mean > 1000 ms → −25.
//
// Add cases by importing your code and bench.add()-ing scenarios. Keep this
// file's OUTPUT CONTRACT: a single JSON object on stdout with
// {"benchmarks": [{"name": string, "mean_ms": number}]}. The same contract
// works for vitest and jest projects (runner-agnostic), so NFR latency
// targets are measured identically everywhere.
import { Bench } from "tinybench";

const bench = new Bench({ time: 500, warmupTime: 100 });

// ── Register benchmarks ──────────────────────────────────────────────────────
// import { myHotPath } from "../src/my_module.js";
// bench.add("FR-XX myHotPath happy path", () => { myHotPath(input); });

if (bench.tasks.length === 0) {
  // No benchmarks registered yet — emit an empty set (scores 100, like a
  // pytest-benchmark run with all means under threshold). Delete this guard
  // once real benchmarks exist.
  console.log(JSON.stringify({ benchmarks: [] }));
  process.exit(0);
}

await bench.run();

const benchmarks = bench.tasks.map((task) => ({
  name: task.name,
  // tinybench reports latency in milliseconds
  mean_ms: task.result?.latency?.mean ?? task.result?.mean ?? 0,
}));

console.log(JSON.stringify({ benchmarks }, null, 2));
