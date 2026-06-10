// [FR-02]
import { describe, it, expect } from "vitest";
import { mapToken } from "../src/mapper";

describe("FR-02 mapper", () => {
  it("test_fr02_maps_known_token", () => {
    expect(mapToken("a")).toBe("alpha");
  });

  it("test_fr02_zero_assert_shell", () => {
    // Deliberate pilot defect: no assertion (test_assertion_quality dimension)
    mapToken("b");
  });
});
