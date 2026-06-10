// harness-methodology vitest template — coverage settings feed the
// test_coverage / integration_coverage gate dimensions, which read
// coverage/coverage-summary.json (json-summary reporter is forced on the CLI
// by the harness; the include/exclude scope below is what you own).
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: [
      "tests/**/*.{test,spec}.{js,jsx,ts,tsx,mjs}",
      "03-development/tests/**/*.{test,spec}.{js,jsx,ts,tsx,mjs}",
    ],
    coverage: {
      provider: "v8",
      include: ["src/**", "03-development/src/**"],
      exclude: ["**/*.d.ts", "**/node_modules/**"],
      reportsDirectory: "coverage",
    },
  },
});
