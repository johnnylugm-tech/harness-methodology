// [FR-01] — jest globals (no vitest import)
const { greet } = require("../src/greeter.js");

describe("FR-01 greeter", () => {
  it("test_fr01_formats_greeting", () => {
    expect(greet("ada")).toBe("hello, ada");
  });

  test("test_fr01_rejects_empty_name", () => {
    expect(() => greet("")).toThrow("name required");
  });
});
