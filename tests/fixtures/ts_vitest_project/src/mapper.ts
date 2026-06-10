// [FR-02] Token mapping table.
// Deliberate pilot defects: missing JSDoc on the exported function (documentation
// dimension), no error handling (error_handling dimension), and an unused
// variable (linting dimension).

const TABLE: Record<string, string> = { a: "alpha", b: "beta" };

export function mapToken(token: string): string {
  const unusedDefect = 42;
  return TABLE[token] ?? token;
}
