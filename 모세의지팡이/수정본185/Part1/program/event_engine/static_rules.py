"""AST checks operate on supplied text, with no filesystem or network access.

This is a development contract checker, not a security sandbox. Runtime tests
also deny sockets; dependency imports remain the existing approved libraries.
"""
import ast

FORBIDDEN_MODULES = {'time', 'datetime', 'random', 'secrets', 'socket', 'requests', 'urllib',
                     'http', 'aiohttp', 'httpx', 'zmq', 'telegram', 'pathlib', 'os', 'io',
                     'subprocess', 'shutil', 'builtins', 'importlib'}
FORBIDDEN_CALLS = {'open', 'exec', 'eval', '__import__', 'getattr', 'setattr',
                   'time', 'time_ns', 'now', 'utcnow', 'monotonic', 'perf_counter',
                   'read_text', 'read_bytes', 'write_text', 'write_bytes', 'read_csv',
                   'read_pickle', 'to_csv', 'to_pickle', 'fromfile', 'tofile', 'memmap',
                   'save', 'savez', 'load', 'urlopen', 'connect', 'request', 'send_message'}
PRIVATE_FACT_OWNERS = {'FVG_WILDER_ATR': 'strategy_FVG'}


def violations(source, *, module, registry=None, framework=False):
    tree = ast.parse(source); errors = []; aliases = {}
    private_owners = dict(PRIVATE_FACT_OWNERS, _wilder_atr='strategy_FVG')
    if registry is not None:
        for name, spec in registry.items():
            if not spec.shared:
                private_owners[name] = spec.owner
                private_owners[spec.compute.__name__] = spec.owner
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name] = alias.name
                if alias.name.split('.')[0] in FORBIDDEN_MODULES:
                    errors.append((node.lineno, 'forbidden import ' + alias.name))
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ''
            for alias in node.names:
                aliases[alias.asname or alias.name] = base + '.' + alias.name
                if alias.name in private_owners and module != private_owners[alias.name]:
                    errors.append((node.lineno, 'foreign private Fact import ' + alias.name))
            if base.split('.')[0] in FORBIDDEN_MODULES:
                errors.append((node.lineno, 'forbidden import ' + base))
        elif isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ''
            resolved = aliases.get(name, name).split('.')[-1]
            if resolved in FORBIDDEN_CALLS:
                # Framework value sealing/access and scheduler request are pure.
                pure_framework = framework and resolved in ('getattr', 'setattr', 'request')
                if not pure_framework:
                    errors.append((node.lineno, 'forbidden call ' + resolved))
            if name == 'fact' and isinstance(node.func, ast.Attribute) and not framework:
                if not node.args or not isinstance(node.args[0], ast.Constant):
                    errors.append((node.lineno, 'Fact reference must be statically declared'))
                else:
                    fact = node.args[0].value
                    if fact in PRIVATE_FACT_OWNERS and module != PRIVATE_FACT_OWNERS[fact]:
                        errors.append((node.lineno, 'foreign private Fact ' + fact))
                    elif registry is not None and fact in registry and not registry[fact].shared and registry[fact].owner != module:
                        errors.append((node.lineno, 'foreign private Fact ' + fact))
                    elif registry is not None and fact not in registry:
                        errors.append((node.lineno, 'unknown Fact ' + str(fact)))
            if not framework and name in ('_process', '_dispatch', 'number', 'take_ready', 'take_input'):
                errors.append((node.lineno, 'Ingress bypass ' + name))
        if isinstance(node, ast.Attribute) and node.attr == 'random':
            errors.append((node.lineno, 'random access'))
        if isinstance(node, ast.Attribute) and node.attr in private_owners and module != private_owners[node.attr]:
            errors.append((node.lineno, 'foreign private Fact access ' + node.attr))
    return tuple(errors)
