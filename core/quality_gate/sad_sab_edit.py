"""Text edits to the SAB block in SAD.md §5 — the authority, not its rendering.

`.methodology/SAB.json` is rendered from SAD.md §5 by `scripts/generate_sab.py`,
so an architecture amendment that only rewrites SAB.json is undone by the next
regeneration (a dropped phantom comes back out of SAD.md) and one that cannot
reach SAD.md at all leaves a Phase 3 module with no legal place (taskq-open's
`__main__`). These two functions edit the YAML inside the block as text, so the
author's comments and layout survive, and `edited_spec` re-parses the result so
the caller can prove the edit means exactly what it intended before writing.

The shapes handled are the ones the corpus uses — a `modules:` list written as
block strings, block `- name:` mappings (identity from `implemented_in` when
present, as `sab_amender.sab_module_candidate` reads it), or a single-line flow
list; an `fr_module_traceability` value written as a scalar, a flow list or a
block list. Replayed over every SAD.md in the corpus: 118/118 layer insertions
and 928/928 retarget/drop rewrites re-parse to exactly the intended change.
Anything else raises `SadEditError`, and nothing is written.

`amend_sad` is that proof; `declare_module` (the `amend-sab --declare` body)
and `sab_amender.resolve_phantom` are its two callers. They live here rather
than in `sab_amender` because that module sits just under the god-file line.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from core.atomic_io import atomic_write_json, atomic_write_text
from core.quality_gate.sab_amender import (
    _DEFAULT_SRC_DIR,
    _MIN_REASON_CHARS,
    ArchitectureAmendmentError,
    _append_adr_amendment,
    _safe_load,
    discover_modules,
    normalize_sab_module_to_dotted,
    unplaceable_modules,
)

_START = "<!-- SAB:START -->"
_END = "<!-- SAB:END -->"
_SCALAR = r"""(?:"[^"\n]*"|'[^'\n]*'|[^\s,\[\]#'"{}]+)"""


class SadEditError(ValueError):
    """The SAB block is not in a shape this module can edit and prove."""


def _block(text: str) -> "tuple[int, int]":
    start = text.find(_START)
    if start < 0:
        raise SadEditError("SAD.md has no <!-- SAB:START --> block")
    end = text.find(_END, start)
    return start, (len(text) if end < 0 else end)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    return raw


def insert_module(text: str, layer: str, module: str) -> str:
    """*text* with *module* appended to *layer*'s `modules:` list."""
    start, end = _block(text)
    lines = text[start:end].split("\n")
    head = next((i for i, ln in enumerate(lines) if re.match(
        r"\s*-\s+name:\s*['\"]?" + re.escape(layer) + r"['\"]?\s*(#.*)?$", ln)), None)
    if head is None:
        raise SadEditError(f"layer {layer!r} has no `- name:` item in the SAB block")
    mod_line = None
    for i in range(head + 1, len(lines)):
        if lines[i].strip() and _indent(lines[i]) <= _indent(lines[head]):
            break
        if re.match(r"\s*modules:", lines[i]):
            mod_line = i
            break
    if mod_line is None:
        raise SadEditError(f"layer {layer!r} has no `modules:` key")

    line = lines[mod_line]
    rest = line.split("modules:", 1)[1].split("#", 1)[0].strip()
    if rest.startswith("["):
        if not rest.endswith("]"):
            raise SadEditError(f"layer {layer!r}: multi-line flow list shape is not supported")
        close = line.rindex("]")
        sep = ", " if line[line.index("[") + 1:close].strip() else ""
        lines[mod_line] = line[:close].rstrip() + f'{sep}"{module}"' + line[close:]
    elif rest:
        raise SadEditError(f"layer {layer!r}: unrecognized `modules:` shape {rest!r}")
    else:
        first = last = None
        for i in range(mod_line + 1, len(lines)):
            if not lines[i].strip():
                continue
            if _indent(lines[i]) <= _indent(line) and not (
                    _indent(lines[i]) == _indent(line) and lines[i].lstrip().startswith("- ")):
                break
            if first is None:
                if not lines[i].lstrip().startswith("- "):
                    raise SadEditError(f"layer {layer!r}: unrecognized block list shape")
                first = lines[i]
            last = i
        if first is None or last is None:
            raise SadEditError(f"layer {layer!r}: empty block list shape is not supported")
        dict_items = first.lstrip()[2:].startswith("name:")
        item = f'- name: "{module}"' if dict_items else f'- "{module}"'
        lines.insert(last + 1, " " * _indent(first) + item)
    return text[:start] + "\n".join(lines) + text[end:]


def rewrite_module(text: str, matches, to: "str | None") -> str:
    """*text* with every reference *matches* accepts replaced by *to*, or removed.

    `matches(raw_scalar) -> bool` decides identity, so the caller's own
    normalization (path vs dotted form, `src_dir` prefix) is the one applied.
    Covers `modules:` items and `fr_module_traceability` values; a scalar FR
    value that is dropped becomes `[]`, as `_rewrite_module_reference` leaves it.
    """
    start, end = _block(text)
    lines = text[start:end].split("\n")
    quoted = f'"{to}"' if to else None
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        item = re.match(r"^(\s*)-\s+(name:\s*)?(" + _SCALAR + r")\s*(#.*)?$", line)
        if item:
            j = i + 1
            while (j < len(lines) and lines[j].strip()
                   and _indent(lines[j]) > _indent(line)
                   and not lines[j].lstrip().startswith("- ")):
                j += 1
            ident = item.group(3)
            if item.group(2):
                for k in range(i + 1, j):
                    impl = re.match(r"^\s*implemented_in:\s*(" + _SCALAR + r")\s*(#.*)?$", lines[k])
                    if impl and _unquote(impl.group(1)).strip():
                        ident = impl.group(1)
            if matches(_unquote(ident)):
                if quoted:
                    out.append(f"{item.group(1)}- {quoted}")
                i = j
                continue
        scalar = re.match(r"^(\s*FR-\d+[\w.-]*:\s*)(" + _SCALAR + r")\s*(#.*)?$", line)
        if scalar and matches(_unquote(scalar.group(2))):
            out.append(scalar.group(1) + (quoted or "[]"))
            i += 1
            continue
        flow = re.match(r"^(\s*[\w.-]+:\s*)\[(.*)\](\s*(#.*)?)$", line)
        if flow:
            items = re.findall(_SCALAR, flow.group(2))
            if any(matches(_unquote(x)) for x in items):
                kept = [x for x in items if not matches(_unquote(x))]
                if quoted:
                    kept = [quoted if matches(_unquote(x)) else x for x in items]
                out.append(flow.group(1) + "[" + ", ".join(kept) + "]" + flow.group(3))
                i += 1
                continue
        out.append(line)
        i += 1
    return text[:start] + "\n".join(out) + text[end:]


def edited_spec(text: str):
    """Parse *text* as a SAD.md the way every reader does (`extract_sab_from_sad`)."""
    from core.quality_gate.sab_parser import extract_sab_from_sad

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "SAD.md"
        path.write_text(text, encoding="utf-8")
        return extract_sab_from_sad(path)


def amend_sad(project_root: Path, edit, expect) -> "tuple[Path, str] | None":
    """Apply *edit* to SAD.md §5's text and prove it with *expect*, or refuse.

    `edit(text) -> text` is `insert_module` or `rewrite_module` above;
    `expect(placements, traceability)` returns the pair the edited SAD must
    parse to. Returns `(sad_path, new_text)` to write, or None when
    the SAD has no SAB block (an older project whose SAB.json is the only
    record). Writes nothing.
    """
    from core.quality_gate.sab_parser import extract_sab_from_sad
    from core.utils.project_layout import ProjectLayout

    sad_path = ProjectLayout(project_root).sad_path
    if not sad_path.is_file() or "<!-- SAB:START -->" not in sad_path.read_text(encoding="utf-8"):
        return None
    before = extract_sab_from_sad(sad_path)
    old_text = sad_path.read_text(encoding="utf-8")
    try:
        new_text = edit(old_text)
    except SadEditError as exc:
        raise ArchitectureAmendmentError(
            f"SAD.md §5 cannot be amended safely — unsupported shape: {exc}") from exc
    after = edited_spec(new_text)
    if _sad_view(after) != expect(*_sad_view(before)):
        raise ArchitectureAmendmentError(
            "SAD.md §5 cannot be amended safely — unsupported shape: the edited "
            "block does not re-parse to exactly the intended change")
    return sad_path, new_text


def _sad_view(spec) -> "tuple[set, dict]":
    """(layer placements, fr_module_traceability) of a parsed SAD, normalized."""
    if spec is None:
        return set(), {}
    placements = {(str(layer.get("name")), normalize_sab_module_to_dotted(m))
                  for layer in spec.layers for m in (layer.get("modules") or [])}
    trace = {}
    for fr, value in (getattr(spec, "fr_module_traceability", None) or {}).items():
        values = [value] if isinstance(value, str) else (value or [])
        trace[fr] = sorted(normalize_sab_module_to_dotted(v) or str(v) for v in values)
    return placements, trace


def declare_module(project_root: Path, module: str, layer: str, reason: str,
                   src_dir: str = _DEFAULT_SRC_DIR) -> str:
    """Place an unplaceable module in a declared layer, in SAD.md §5 itself.

    The code -> SAB counterpart of `resolve_phantom`. A module whose name
    states no layer the SAB declares is refused by Gate 1 and by `amend_sab`
    (Round 101: this framework does not choose a layer). The project chooses
    one here, on the record:

      * only a module `unplaceable_modules` reports — anything else is either
        already placed or placed by its own name, and this is not a move tool;
      * only a layer SAD.md §5 already declares — no new layers;
      * `reason` is mandatory (>= _MIN_REASON_CHARS);
      * the SAD edit must re-parse to exactly the old placements plus this one.

    Order: ADR.md, SAD.md, SAB.json — a crash cannot leave a placement without
    its reason, and SAB.json is never ahead of the document it renders.
    """
    module = normalize_sab_module_to_dotted(module, src_dir) or module
    reason = (reason or "").strip()
    if len(reason) < _MIN_REASON_CHARS:
        raise ArchitectureAmendmentError(
            f"--reason must be at least {_MIN_REASON_CHARS} characters of actual "
            f"justification (got {len(reason)}); it is the architecture record")
    sab_path = project_root / ".methodology" / "SAB.json"
    sab = _safe_load(sab_path) if sab_path.is_file() else {}
    if not isinstance(sab, dict) or not sab.get("layers"):
        raise ArchitectureAmendmentError(f"no SAB layers at {sab_path}")
    declared_layers = [str(entry.get("name")) for entry in sab["layers"]]
    if layer not in declared_layers:
        raise ArchitectureAmendmentError(
            f"layer {layer!r} is not declared (declared: {', '.join(declared_layers)}); "
            f"--declare places a module in an existing layer, it does not add one")
    unplaceable = unplaceable_modules(sab, discover_modules(project_root, src_dir), src_dir)
    if module not in unplaceable:
        raise ArchitectureAmendmentError(
            f"{module!r} is not unplaceable (unplaceable now: {unplaceable or 'none'}) — "
            f"a module on disk whose name states its layer is placed by plain "
            f"`amend-sab`, and a registered one is not moved by --declare")

    amended = amend_sad(
        project_root,
        lambda text: insert_module(text, layer, module),
        lambda placements, trace: (placements | {(layer, module)}, trace),
    )
    _append_adr_amendment(project_root, module, f"declared in layer `{layer}`",
                          reason, [f"layer {layer!r}"], command="--declare")
    if amended:
        sad_path, new_text = amended
        atomic_write_text(sad_path, new_text)
    next(entry for entry in sab["layers"] if entry.get("name") == layer) \
        .setdefault("modules", []).append(module)
    atomic_write_json(sab_path, sab)
    return (f"[amend-sab] architecture amended: `{module}` declared in layer "
            f"`{layer}`; reason recorded in 02-architecture/ADR.md")
