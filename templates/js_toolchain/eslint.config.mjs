// harness-methodology default flat config (eslint 10 — flat config only).
// Scored by the `linting` gate dimension: each error/warning costs 2 points
// (harness/tool_runners.py _score_eslint). Tighten freely; loosening rules to
// pass the gate defeats the dimension and will surface in review.
import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: [
      "node_modules/**",
      "coverage/**",
      "dist/**",
      "build/**",
      ".methodology/**",
      ".sessi-work/**",
      "reports/**",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    rules: {
      complexity: ["warn", 10],
      "max-lines-per-function": ["warn", { max: 80, skipComments: true }],
    },
  },
);
