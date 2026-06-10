// [FR-01] Greeting formatter (JSDoc-typed for tsc --checkJs).

/**
 * Formats a greeting for a user.
 * @param {string} name - non-empty user name
 * @returns {string}
 */
function greet(name) {
  if (typeof name !== "string" || name === "") {
    throw new Error("name required");
  }
  try {
    return `hello, ${name}`;
  } catch (e) {
    throw new Error(`greet failed: ${e}`);
  }
}

module.exports = { greet };
