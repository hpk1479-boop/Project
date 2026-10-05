"""Read every delivered file; freeze hashes and extract a navigable AST inventory.

This is an explicit baseline creation tool, never invoked by regression tests.
Configuration VALUES are deliberately excluded from the generated inventory.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit" / "fixtures"


def inventory():
    manifest, symbols, contracts = {}, {}, []
    paths = [ROOT / "AGENTS.md.txt", ROOT / "OZ_SYSTEM CONTROL.pyw"]
    paths += sorted((ROOT / "program").rglob("*"))
    for path in paths:
        if not path.is_file() or any(p in {"logs", "__pycache__"} for p in path.parts):
            continue
        data = path.read_bytes()
        name = path.relative_to(ROOT).as_posix()
        manifest[name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        if path.suffix not in {".py", ".pyw"}:
            continue
        source = data.decode("utf-8-sig")
        tree = ast.parse(source, filename=name)
        parent = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parent[child] = node
        def owner(node):
            names = []
            while node in parent:
                node = parent[node]
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.append(node.name)
            return ".".join(reversed(names))
        symbols[name] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols[name].append({"name": ".".join(filter(None, [owner(node), node.name])),
                                      "line": node.lineno, "end": node.end_lineno})
            # Full payload constructions, field reads, routing comparisons, I/O calls.
            kind = None
            if isinstance(node, ast.Dict) and any(isinstance(k, ast.Constant) and k.value in {"kind", "action"} for k in node.keys):
                kind = "payload"
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"send_pyobj", "recv_pyobj", "bind", "connect", "append", "publish", "send_event"}:
                    kind = "io"
                elif node.func.attr == "get" and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    kind = "field_read"
            elif isinstance(node, ast.Compare) and any(isinstance(n, ast.Name) and n.id in {"kind", "action", "watch_type"} for n in ast.walk(node)):
                kind = "dispatch"
            if kind:
                contracts.append({"file": name, "function": owner(node), "line": node.lineno,
                                  "category": kind, "source": ast.get_source_segment(source, node)})
        symbols[name].sort(key=lambda x: x["line"])
    OUT.mkdir(parents=True, exist_ok=True)
    for filename, payload in (("source_manifest.json", manifest), ("source_index.json", symbols),
                              ("contract_inventory.json", contracts)):
        (OUT / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Read {len(manifest)} delivered files; indexed {sum(map(len, symbols.values()))} definitions; {len(contracts)} contract sites.")


if __name__ == "__main__":
    inventory()
