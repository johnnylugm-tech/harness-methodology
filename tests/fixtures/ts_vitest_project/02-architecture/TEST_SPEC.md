# TEST_SPEC.md — ts-vitest-pilot

## Functional Requirement Test Cases

### FR-01: comma-separated token parsing

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_fr01_happy_path` | input="a, b"; expected=["a","b"] | happy_path | Q1 |
| 2 | `test_fr01_rejects_empty` | input="  "; expected=Error("empty input") | validation | Q2 |

### FR-02: token mapping

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_fr02_maps_known_token` | token="a"; expected="alpha" | happy_path | Q1 |
| 2 | `test_fr02_zero_assert_shell` | token="b"; expected="beta" | happy_path | Q1 |
