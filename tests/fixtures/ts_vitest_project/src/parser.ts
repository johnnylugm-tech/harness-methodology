// [FR-01] Tokenizer entry point.

/**
 * Parses a comma-separated token string into trimmed tokens.
 * Rejects empty input with an Error (FR-01 validation rule).
 */
export function parse(input: string): string[] {
  if (input.trim() === "") {
    throw new Error("empty input");
  }
  try {
    return input.split(",").map((t) => t.trim());
  } catch (e) {
    throw new Error(`parse failed: ${e}`);
  }
}
