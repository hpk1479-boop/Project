"""Read current data contracts from Python literals without executing source."""
from __future__ import annotations
import ast
import operator
from typing import Any

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv}


def literal(node: ast.AST, names: dict | None = None) -> Any:
    """Resolve only literals and previously declared constant expressions."""
    names = names or {}
    if isinstance(node, ast.Name) and node.id in names: return names[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](literal(node.left, names), literal(node.right, names))
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        values = [literal(item, names) for item in node.elts]
        return tuple(values) if isinstance(node, ast.Tuple) else values
    if isinstance(node, ast.Dict):
        return {literal(key, names): literal(value, names) for key, value in zip(node.keys, node.values)}
    return ast.literal_eval(node)


def constants(source: str) -> dict:
    """Inspect current source declarations; no source mutation API is exposed."""
    result = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try: result[node.targets[0].id] = literal(node.value, result)
            except (ValueError, TypeError, KeyError, ZeroDivisionError): pass
    return result
