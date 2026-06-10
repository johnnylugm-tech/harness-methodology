// [FR-01]
import { describe, it, expect } from "vitest";
import { parse } from "../src/parser";

describe("FR-01 parser", () => {
  it("test_fr01_happy_path", () => {
    expect(parse("a, b")).toEqual(["a", "b"]);
  });

  it("test_fr01_rejects_empty", () => {
    expect(() => parse("  ")).toThrow("empty input");
  });
});
