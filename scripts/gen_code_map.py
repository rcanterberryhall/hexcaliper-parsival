"""Generate per-subpackage READMEs + a top-level code map for a Python project.

Vendored from gatehouse (STANDARDS.md DOC-006..008). Copy this file into your
repo (conventionally ``tools/`` or ``scripts/``) and wire it into pre-commit
and CI — see ``tools/README.md`` in gatehouse for the hook/CI snippets.

What it maintains, from docstrings alone:

- ``<src-root>/<pkg>/README.md`` — a "Public API" section per subpackage
  (signatures + docstring summaries) inside marker-delimited blocks. The
  narrative header (purpose, key concepts, relations, pitfalls) is
  hand-written and never touched; ``--init`` bootstraps it with TODOs.
- ``<docs-dir>/code_map.md`` — an index table of every subpackage and its
  role (the first line of its ``__init__.py`` docstring).

``--flat`` covers the single-package project: a source tree that is one
package of flat modules with no subpackages, where the default discovery
(immediate child directories containing ``__init__.py``) would find nothing
and emit an empty map. With it, ``--src-root`` is itself the one package —
its README carries the API block for every flat module, and the index table
is the single row describing it.

Usage:
    python tools/gen_code_map.py --src-root src/myapp          # regenerate
    python tools/gen_code_map.py --src-root src/myapp --init   # bootstrap
    python tools/gen_code_map.py --src-root src/myapp --check  # CI: fail on drift
    python tools/gen_code_map.py --src-root api --flat         # one flat package
"""

from __future__ import annotations

import argparse
import ast
import difflib
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


def discover_subpackages(root: Path) -> list[Path]:
    """Return immediate child directories of ``root`` containing ``__init__.py``.

    Excludes any directory whose name starts with ``_`` (covers ``__pycache__``
    and explicit private packages). The result is sorted alphabetically by
    directory name.
    """
    return sorted(
        (
            p
            for p in root.iterdir()
            if p.is_dir() and not p.name.startswith("_") and (p / "__init__.py").is_file()
        ),
        key=lambda p: p.name,
    )


def discover_packages(root: Path, *, flat: bool) -> list[Path]:
    """Return the package directories that each get their own README.

    In the default (nested) layout that is every immediate subpackage of
    ``root``. In flat mode ``root`` is itself the single package, so the list
    is just ``[root]`` — used when a project is one package of flat modules
    and ``discover_subpackages`` would return nothing.
    """
    return [root] if flat else discover_subpackages(root)


def package_dotted_name(root: Path, package_dir: Path) -> str:
    """Return the dotted name for ``package_dir`` relative to the source root.

    The root package is its own bare name; a subpackage is qualified by it.
    """
    if package_dir == root:
        return root.name
    return f"{root.name}.{package_dir.name}"


def read_dunder_all(init_path: Path) -> list[str] | None:
    """Extract ``__all__`` from a package ``__init__.py`` as a list of strings.

    Returns ``None`` if no ``__all__`` assignment is present. Returns an empty
    list if ``__all__ = []`` or ``__all__ = ()`` is set.
    """
    tree = ast.parse(init_path.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == "__all__":
                value = node.value
                if isinstance(value, (ast.List, ast.Tuple)):
                    return [
                        elt.value
                        for elt in value.elts
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                    ]
    return None


@dataclass
class Symbol:
    """One public symbol extracted from a module."""

    name: str
    kind: str  # "function" | "class" | "constant" | "method" | "property" | "field" | "classmethod" | "staticmethod"  # noqa: E501
    signature: str
    docstring_first_line: str
    docstring_rest: str
    # For classes: nested public members (methods, properties, fields).
    members: list[Symbol] = field(default_factory=list)


def _is_public(name: str, dunder_all_filter: list[str] | None) -> bool:
    """A name is public if it's in __all__ (when set) or doesn't start with _."""
    if dunder_all_filter is not None:
        return name in dunder_all_filter
    return not name.startswith("_")


def extract_module_symbols(module_path: Path, dunder_all_filter: list[str] | None) -> list[Symbol]:
    """Walk ``module_path`` with ``ast`` and return its public symbols in source order.

    ``dunder_all_filter`` mirrors the package's ``__all__``: when not None, only
    names in the list are returned (and may override the leading-underscore rule).

    For module-level constants (``AnnAssign``), if the very next statement is a
    bare string expression, that string is treated as the constant's docstring
    (PEP 258 convention).
    """
    tree = ast.parse(module_path.read_text())
    out: list[Symbol] = []
    body = tree.body
    for i, node in enumerate(body):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not _is_public(node.name, dunder_all_filter):
                continue
            out.append(
                Symbol(
                    name=node.name,
                    kind="function",
                    signature=_render_function_signature(node),
                    docstring_first_line=_doc_first_line(node),
                    docstring_rest=_doc_rest(node),
                )
            )
        elif isinstance(node, ast.ClassDef):
            if not _is_public(node.name, dunder_all_filter):
                continue
            out.append(
                Symbol(
                    name=node.name,
                    kind="class",
                    signature=node.name,
                    docstring_first_line=_doc_first_line(node),
                    docstring_rest=_doc_rest(node),
                    members=_extract_class_members(node),
                )
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            if not _is_public(name, dunder_all_filter):
                continue
            sig = f"{name}: {ast.unparse(node.annotation)}"
            if node.value is not None:
                sig += f" = {ast.unparse(node.value)}"
            # PEP 258: bare string literal on the next line is the constant docstring.
            const_doc = ""
            if i + 1 < len(body):
                nxt = body[i + 1]
                if (
                    isinstance(nxt, ast.Expr)
                    and isinstance(nxt.value, ast.Constant)
                    and isinstance(nxt.value.value, str)
                ):
                    const_doc = nxt.value.value
            out.append(
                Symbol(
                    name=name,
                    kind="constant",
                    signature=sig,
                    docstring_first_line=const_doc.splitlines()[0] if const_doc else "",
                    docstring_rest="\n".join(const_doc.splitlines()[1:]).lstrip("\n"),
                )
            )
    return out


_INCLUDED_DUNDER_METHODS = frozenset({"__init__", "__post_init__"})


def _extract_class_members(class_node: ast.ClassDef) -> list[Symbol]:
    """Walk a class body and return public methods, properties, dataclass fields.

    Includes ``__init__`` and ``__post_init__`` when explicitly defined.
    """
    members: list[Symbol] = []
    for node in class_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = node.name
            # Skip private methods unless they're the included dunders.
            if name.startswith("_") and name not in _INCLUDED_DUNDER_METHODS:
                continue
            kind = "method"
            for deco in node.decorator_list:
                if isinstance(deco, ast.Name):
                    if deco.id == "property":
                        kind = "property"
                        break
                    if deco.id == "classmethod":
                        kind = "classmethod"
                        break
                    if deco.id == "staticmethod":
                        kind = "staticmethod"
                        break
            members.append(
                Symbol(
                    name=name,
                    kind=kind,
                    signature=_render_function_signature(node),
                    docstring_first_line=_doc_first_line(node),
                    docstring_rest=_doc_rest(node),
                )
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            field_name = node.target.id
            if field_name.startswith("_"):
                continue
            sig = f"{field_name}: {ast.unparse(node.annotation)}"
            if node.value is not None:
                sig += f" = {ast.unparse(node.value)}"
            members.append(
                Symbol(
                    name=field_name,
                    kind="field",
                    signature=sig,
                    docstring_first_line="",
                    docstring_rest="",
                )
            )
    return members


def _render_function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Render a function signature string from its AST node, with type annotations.

    Coroutine functions (``async def``) are prefixed with ``async`` so the
    rendered signature unambiguously identifies awaitable callables.
    """
    args_src = ast.unparse(node.args)
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
    return f"{prefix}{node.name}({args_src}){returns}"


# The node kinds ``ast.get_docstring`` accepts. Annotating the helpers below with
# a bare ``ast.AST`` type-checks as an error, since most AST nodes cannot carry a
# docstring; every call site passes one of these four.
_Documentable = ast.AsyncFunctionDef | ast.FunctionDef | ast.ClassDef | ast.Module


def _doc_first_line(node: _Documentable) -> str:
    doc = ast.get_docstring(node) or ""
    return doc.splitlines()[0] if doc else ""


def _doc_rest(node: _Documentable) -> str:
    doc = ast.get_docstring(node) or ""
    lines = doc.splitlines()
    return "\n".join(lines[1:]).lstrip("\n") if len(lines) > 1 else ""


def render_module_block(
    module_dotted_path: str,
    module_docstring_first_line: str,
    symbols: list[Symbol],
) -> str:
    """Render the markdown section for one module.

    Output shape:
        ### `<dotted.path>`
        *<module docstring first line>* | *(no module docstring)*

        - `signature` — first docstring line | (undocumented)
          <details><summary>full docstring</summary>

          rest of docstring
          </details>
    """
    lines: list[str] = []
    lines.append(f"### `{module_dotted_path}`")
    if module_docstring_first_line:
        lines.append(f"*{module_docstring_first_line}*")
    else:
        lines.append("*(no module docstring)*")
    lines.append("")  # blank line between heading and list
    if not symbols:
        lines.append("*(no public symbols)*")
        lines.append("")
        return "\n".join(lines)
    for sym in symbols:
        lines.extend(_render_symbol_bullet(sym, indent=0))
    lines.append("")  # trailing blank line
    return "\n".join(lines)


def _render_symbol_bullet(sym: Symbol, indent: int) -> list[str]:
    pad = "  " * indent
    if sym.docstring_first_line:
        head = f"{pad}- `{sym.signature}` — {sym.docstring_first_line}"
    else:
        head = f"{pad}- `{sym.signature}` — (undocumented)"
    out = [head]
    if sym.docstring_rest:
        out.append(f"{pad}  <details><summary>full docstring</summary>")
        out.append("")
        for line in sym.docstring_rest.splitlines():
            out.append(f"{pad}  {line}" if line else "")
        out.append(f"{pad}  </details>")
    for member in sym.members:
        out.extend(_render_symbol_bullet(member, indent=indent + 1))
    return out


def render_package_public_api(package_dir: Path, package_dotted: str) -> str:
    """Render the full Public API section for one subpackage.

    Determines public modules:
      - all ``*.py`` files in ``package_dir`` not starting with ``_``;
      - all immediate-child subpackages (directories with ``__init__.py``)
        whose names don't start with ``_``;
      - PLUS any private module name or subpackage referenced via
        ``__init__.__all__`` (which may target a flat ``.py`` or a directory).
    Lists modules alphabetically. Symbols re-exported from a private or
    subpackage source appear under the defining source's heading.
    """
    init_path = package_dir / "__init__.py"
    dunder_all = read_dunder_all(init_path)

    # Maps module-name (relative to package_dir) → filter for that module.
    # None = include all public-by-name symbols; list = restrict to these names.
    module_files: dict[str, list[str] | None] = {}

    # Auto-discover public flat modules.
    for py in package_dir.glob("*.py"):
        if py.name == "__init__.py":
            continue
        stem = py.stem
        if stem.startswith("_"):
            continue
        module_files[stem] = None

    # Auto-discover public subpackages.
    for sub in package_dir.iterdir():
        if not sub.is_dir() or sub.name.startswith("_"):
            continue
        if not (sub / "__init__.py").is_file():
            continue
        module_files[sub.name] = None

    # __all__-referenced names that point at a private flat module or any subpackage
    # (private or public — public subpackages are already in module_files but this
    # path lets __all__-restricted filters override).
    if dunder_all:
        defining_to_names: dict[str, list[str]] = {}
        for name in dunder_all:
            defining = _find_defining_module(init_path, name)
            if defining is None:
                continue
            # If the defining target is a public subpackage already auto-discovered with
            # a None filter, prefer the __all__-restricted filter to avoid listing
            # symbols the package didn't choose to export.
            flat_path = package_dir / f"{defining}.py"
            sub_init = package_dir / defining / "__init__.py"
            if flat_path.is_file() and defining.startswith("_"):
                defining_to_names.setdefault(defining, []).append(name)
            elif sub_init.is_file():
                # Subpackage (public or private). Restrict to the __all__ names.
                defining_to_names.setdefault(defining, []).append(name)
        for mod_name, names in defining_to_names.items():
            module_files[mod_name] = names

    if not module_files:
        return "*(none — package re-exports nothing publicly)*\n"

    parts: list[str] = []
    for mod_name in sorted(module_files):
        flat_path = package_dir / f"{mod_name}.py"
        sub_init = package_dir / mod_name / "__init__.py"
        if flat_path.is_file():
            mod_path = flat_path
        elif sub_init.is_file():
            mod_path = sub_init
        else:
            continue
        filter_for_mod = module_files[mod_name]
        symbols = extract_module_symbols(mod_path, dunder_all_filter=filter_for_mod)
        mod_doc = ast.get_docstring(ast.parse(mod_path.read_text())) or ""
        first_line = mod_doc.splitlines()[0] if mod_doc else ""
        parts.append(
            render_module_block(
                module_dotted_path=f"{package_dotted}.{mod_name}",
                module_docstring_first_line=first_line,
                symbols=symbols,
            )
        )
    return "\n".join(parts)


def _find_defining_module(init_path: Path, name: str) -> str | None:
    """Find the module that defines ``name`` by inspecting ``__init__.py``'s imports.

    Matches both relative (``from .module import name``) and absolute
    (``from package.module import name``) import styles. Returns the last
    segment of the module path (without leading dot) or ``None`` if not found.

    Callers must independently verify the returned module name corresponds to a
    real ``*.py`` file in the package directory (the caller in
    ``render_package_public_api`` already does this).
    """
    tree = ast.parse(init_path.read_text())
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if (alias.asname or alias.name) == name:
                    return node.module.split(".")[-1]
    return None


class MarkerError(RuntimeError):
    """Raised when an expected marker is missing or out of order."""


def replace_marked_block(
    *,
    content: str,
    begin_marker: str,
    end_marker: str,
    new_inner: str,
) -> str:
    """Replace the text between ``begin_marker`` and ``end_marker`` in ``content``.

    The markers themselves are preserved verbatim. The output uses the form:

        <begin_marker>
        <new_inner>
        <end_marker>

    Raises ``MarkerError`` if either marker is missing or the end precedes the begin.
    """
    b = content.find(begin_marker)
    if b < 0:
        raise MarkerError(f"begin marker not found: {begin_marker!r}")
    e = content.find(end_marker, b + len(begin_marker))
    if e < 0:
        raise MarkerError(f"end marker not found after begin: {end_marker!r}")
    if not new_inner.endswith("\n"):
        new_inner += "\n"
    return content[: b + len(begin_marker)] + "\n" + new_inner + content[e:]


def render_package_index_table(
    *,
    src_root: Path,
    package_dotted_prefix: str,
    readme_path_template: str,
    flat: bool = False,
) -> str:
    """Render the markdown table mapping each package to its role + README link.

    Covers every subpackage of ``src_root``, or ``src_root`` itself when
    ``flat`` is set. ``readme_path_template`` may contain a ``{pkg}`` field for
    the package directory name; in flat mode it is a fixed path with no field.
    """
    rows: list[str] = ["| Package | Role | README |", "|---|---|---|"]
    for pkg_dir in discover_packages(src_root, flat=flat):
        init_doc = ast.get_docstring(ast.parse((pkg_dir / "__init__.py").read_text())) or ""
        role = init_doc.splitlines()[0] if init_doc else "(no package docstring)"
        readme = readme_path_template.format(pkg=pkg_dir.name)
        dotted = (
            package_dotted_prefix
            if pkg_dir == src_root
            else f"{package_dotted_prefix}.{pkg_dir.name}"
        )
        rows.append(f"| `{dotted}` | {role} | [{readme}]({readme}) |")
    return "\n".join(rows) + "\n"


PKG_BEGIN = "<!-- BEGIN: AUTO-GENERATED PUBLIC API (do not edit) -->"
PKG_END = "<!-- END: AUTO-GENERATED PUBLIC API -->"
INDEX_BEGIN = "<!-- BEGIN: AUTO-GENERATED PACKAGE INDEX -->"
INDEX_END = "<!-- END: AUTO-GENERATED PACKAGE INDEX -->"


def package_readme_template(*, package_dotted: str, auto_generated_inner: str) -> str:
    """Return the bootstrap content for a per-package README with _TODO_ placeholders."""
    inner = (
        auto_generated_inner if auto_generated_inner.endswith("\n") else auto_generated_inner + "\n"
    )
    return (
        f"# {package_dotted}\n"
        "\n"
        "> _TODO_: one-sentence elevator pitch.\n"
        "\n"
        "## Purpose\n"
        "_TODO_: 2-4 paragraphs on this package's role in the project.\n"
        "\n"
        "## Key concepts\n"
        "- _TODO_\n"
        "\n"
        "## How it relates to other packages\n"
        "_TODO_: inbound and outbound dependencies in plain English.\n"
        "\n"
        "## Common pitfalls\n"
        "_TODO_ (optional — delete this section if none apply).\n"
        "\n"
        "## Public API\n"
        f"{PKG_BEGIN}\n"
        f"{inner}"
        f"{PKG_END}\n"
    )


def code_map_template(*, project_name: str, auto_generated_inner: str) -> str:
    """Return the bootstrap content for the code map with _TODO_ placeholders."""
    inner = (
        auto_generated_inner if auto_generated_inner.endswith("\n") else auto_generated_inner + "\n"
    )
    return (
        f"# {project_name} code map\n"
        "\n"
        "## How to read this\n"
        "_TODO_: short preamble — the project's overall shape (pipeline stages,"
        " layers, main data flow) and how the packages divide it. The per-package"
        " READMEs explain each slice; this page is the index.\n"
        "\n"
        "## Packages\n"
        f"{INDEX_BEGIN}\n"
        f"{inner}"
        f"{INDEX_END}\n"
        "\n"
        "## Module-level entry points\n"
        "_TODO_: short bullets for top-level modules that aren't packages"
        " (CLI entry point, app factory, shared types/logging modules).\n"
    )


def main(argv: list[str] | None = None, repo_root: Path | None = None) -> int:
    """Generator entry point.

    Args:
        argv: command-line args (without program name). Defaults to sys.argv[1:].
        repo_root: repo root. Defaults to the parent of this script's directory
            (i.e. vendoring the script under ``<repo>/tools/`` or
            ``<repo>/scripts/`` just works).

    Returns:
        0 on success or no-op; 1 if ``--check`` detected drift; 2 on configuration
        errors (missing markers, missing README files in non-init mode, etc.).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--src-root",
        required=True,
        help="path (relative to repo root) of the top-level package directory, e.g. src/myapp",
    )
    parser.add_argument(
        "--docs-dir",
        default="docs",
        help="path (relative to repo root) of the docs directory for code_map.md (default: docs)",
    )
    parser.add_argument("--check", action="store_true", help="exit 1 if anything would change")
    parser.add_argument("--init", action="store_true", help="create missing READMEs from template")
    parser.add_argument(
        "--flat",
        action="store_true",
        help="treat --src-root itself as the single package (no subpackages)",
    )
    ns = parser.parse_args(argv)

    if repo_root is None:
        repo_root = Path(__file__).resolve().parent.parent
    src_root = repo_root / ns.src_root
    docs_dir = repo_root / ns.docs_dir
    package_name = src_root.name
    if not src_root.is_dir() or not (src_root / "__init__.py").is_file():
        print(f"❌ --src-root {ns.src_root!r} is not a package directory", file=sys.stderr)
        return 2

    packages = discover_packages(src_root, flat=ns.flat)

    desired: dict[Path, str] = {}
    for pkg_dir in packages:
        pkg_dotted = package_dotted_name(src_root, pkg_dir)
        readme = pkg_dir / "README.md"
        existing = readme.read_text() if readme.is_file() else None
        if existing is None and not ns.init:
            print(
                f"❌ Missing README: {readme.relative_to(repo_root)}. "
                "Run with --init to create it.",
                file=sys.stderr,
            )
            return 2
        try:
            desired[readme] = render_package_readme(
                package_dir=pkg_dir,
                package_dotted=pkg_dotted,
                existing_content=existing,
            )
        except MarkerError as exc:
            print(f"❌ {readme.relative_to(repo_root)}: {exc}", file=sys.stderr)
            return 2

    # Top-level code map.
    readme_link_prefix = os.path.relpath(src_root, docs_dir)
    index_table = render_package_index_table(
        src_root=src_root,
        package_dotted_prefix=package_name,
        readme_path_template=(
            f"{readme_link_prefix}/README.md"
            if ns.flat
            else f"{readme_link_prefix}/{{pkg}}/README.md"
        ),
        flat=ns.flat,
    )
    code_map_path = docs_dir / "code_map.md"
    existing_map = code_map_path.read_text() if code_map_path.is_file() else None
    if existing_map is None and not ns.init:
        print(
            f"❌ Missing {code_map_path.relative_to(repo_root)}. Run with --init.",
            file=sys.stderr,
        )
        return 2
    if existing_map is None:
        desired[code_map_path] = code_map_template(
            project_name=package_name, auto_generated_inner=index_table
        )
    else:
        try:
            desired[code_map_path] = replace_marked_block(
                content=existing_map,
                begin_marker=INDEX_BEGIN,
                end_marker=INDEX_END,
                new_inner=index_table,
            )
        except MarkerError as exc:
            print(f"❌ {code_map_path.relative_to(repo_root)}: {exc}", file=sys.stderr)
            return 2

    if ns.check:
        any_drift = False
        for path, want in desired.items():
            have = path.read_text() if path.is_file() else ""
            if have != want:
                any_drift = True
                diff = difflib.unified_diff(
                    have.splitlines(keepends=True),
                    want.splitlines(keepends=True),
                    fromfile=str(path),
                    tofile=str(path) + " (generated)",
                )
                sys.stderr.writelines(diff)
        if any_drift:
            flat_arg = " --flat" if ns.flat else ""
            print(
                f"❌ code map is stale. Run: python {Path(__file__).name} "
                f"--src-root {ns.src_root}{flat_arg}",
                file=sys.stderr,
            )
            return 1
        return 0

    for path, want in desired.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file() or path.read_text() != want:
            path.write_text(want)
    return 0


def render_package_readme(
    *,
    package_dir: Path,
    package_dotted: str,
    existing_content: str | None,
) -> str:
    """Compute the desired README content for one subpackage.

    If ``existing_content`` is None, returns the bootstrap template (Pass 1).
    Otherwise, replaces just the marked auto-generated block in the existing
    content (steady-state operation).
    """
    inner = render_package_public_api(package_dir, package_dotted=package_dotted)
    if existing_content is None:
        return package_readme_template(package_dotted=package_dotted, auto_generated_inner=inner)
    return replace_marked_block(
        content=existing_content,
        begin_marker=PKG_BEGIN,
        end_marker=PKG_END,
        new_inner=inner,
    )


if __name__ == "__main__":
    sys.exit(main())
