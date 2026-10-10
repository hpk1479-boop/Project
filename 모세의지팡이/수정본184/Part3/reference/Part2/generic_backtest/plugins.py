"""Discovery is literal AST inspection, never strategy execution."""
import ast
import hashlib
from dataclasses import dataclass
from .canonical import identity, file_hash, finite
from .contracts import API, ROOT, GenericError, StrategyRequirements, TIMEFRAMES
from .paths import plain_path


@dataclass(frozen=True)
class PluginMetadata:
    path: str
    sha256: str
    metadata: dict
    source_bytes: bytes | None = None


def parse_literal_metadata(path):
    path = plain_path(path)
    if path.suffix != '.py': raise GenericError('E_PLUGIN_SCHEMA', 'Python source required')
    raw = path.read_bytes()
    tree = ast.parse(raw, filename=str(path))
    found = [n.value for n in tree.body if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == 'BACKTEST_PLUGIN' for t in n.targets)]
    if not found: return None
    if len(found) != 1: raise GenericError('E_PLUGIN_SCHEMA', 'one literal metadata assignment required')
    try: meta = ast.literal_eval(found[0])
    except Exception as exc: raise GenericError('E_PLUGIN_SCHEMA', 'literal metadata required') from exc
    if not isinstance(meta, dict) or meta.get('api_version') != API or meta.get('plugin_id') != path.stem:
        raise GenericError('E_PLUGIN_SCHEMA', 'api_version / filename')
    for key in ('display_name','plugin_version','supported_modes','parameters_schema','risk_anchors'):
        if key not in meta: raise GenericError('E_PLUGIN_SCHEMA', key)
    if any(not isinstance(meta[k],str) or not meta[k] for k in ('display_name','plugin_version')):
        raise GenericError('E_PLUGIN_SCHEMA','display_name/plugin_version must be nonempty strings')
    if not meta['supported_modes'] or not set(meta['supported_modes']) <= {'ALERT_ONLY','TRADE'}:
        raise GenericError('E_PLUGIN_SCHEMA', 'modes')
    if not isinstance(meta['parameters_schema'], dict) or not isinstance(meta['risk_anchors'], dict):
        raise GenericError('E_PLUGIN_SCHEMA', 'schema/anchors')
    if any(not isinstance(k,str) or not k or not isinstance(v,str) for k,v in meta['risk_anchors'].items()):
        raise GenericError('E_PLUGIN_SCHEMA','anchor names/descriptions')
    for name,spec in meta['parameters_schema'].items():
        if not isinstance(name,str) or not isinstance(spec,dict) or spec.get('type') not in ('integer','number','string','boolean'):
            raise GenericError('E_PLUGIN_SCHEMA','parameter declaration')
    identity(meta)  # JSON-compatible finite literal only.
    return PluginMetadata(str(path), hashlib.sha256(raw).hexdigest(), meta, raw)


def discover_plugins(root=None):
    root = plain_path(root or ROOT / 'BACKTEST_SPECIAL')
    result = []
    for path in sorted(root.glob('*.py')):
        if path.name.startswith('_'): continue
        try:
            record = parse_literal_metadata(path)
            if record: result.append({'plugin_id': path.stem, 'sha256': record.sha256,
                'metadata': record.metadata, 'path': record.path, 'error': None})
        except Exception as exc:
            result.append({'plugin_id': path.stem, 'path': str(path), 'error': str(exc)})
    return result


def validate_parameters(metadata, supplied):
    schema = metadata['parameters_schema']
    if not isinstance(supplied, dict) or set(supplied) - set(schema):
        raise GenericError('E_PLUGIN_SCHEMA', 'unknown parameters')
    out = {}
    types = {'integer': int, 'number': (int,float), 'string': str, 'boolean': bool}
    for name, spec in schema.items():
        if spec.get('type') not in types: raise GenericError('E_PLUGIN_SCHEMA', name)
        value = supplied.get(name, spec.get('default'))
        expected = types[spec['type']]
        if not isinstance(value, expected) or (spec['type'] in ('number','integer') and not finite(value)):
            raise GenericError('E_PLUGIN_SCHEMA', name + ': type')
        if ('minimum' in spec and value < spec['minimum']) or ('maximum' in spec and value > spec['maximum']):
            raise GenericError('E_PLUGIN_SCHEMA', name + ': range')
        if 'enum' in spec and value not in spec['enum']: raise GenericError('E_PLUGIN_SCHEMA', name + ': enum')
        out[name] = value
    return out


def validate_requirements(value, budget):
    if isinstance(value, StrategyRequirements):
        from .canonical import plain
        value = plain(value)
    if not isinstance(value, dict): raise GenericError('E_PLUGIN_SCHEMA', 'requirements')
    req = StrategyRequirements(**value)
    if len(set(req.required_timeframes)) != len(req.required_timeframes) or not set(req.required_timeframes) <= set(TIMEFRAMES):
        raise GenericError('E_PLUGIN_SCHEMA', 'timeframes')
    if set(req.completed_lookback_by_tf) != set(req.required_timeframes):
        raise GenericError('E_PLUGIN_SCHEMA', 'explicit per-TF lookbacks required')
    if any(type(n) != int or n < 0 or n > budget for n in req.completed_lookback_by_tf.values()) or sum(req.completed_lookback_by_tf.values())>budget:
        raise GenericError('E_RESOURCE_LIMIT', 'history budget')
    if type(req.raw_tick_warmup_ns) != int or req.raw_tick_warmup_ns < 0 or req.readiness_behavior not in ('WAIT','ERROR'):
        raise GenericError('E_PLUGIN_SCHEMA', 'warmup/readiness')
    for f in req.features:
        if f.get('timeframe') not in req.required_timeframes or f.get('kind') not in ('HMA_OPEN','MOSES_PERCENTILE'):
            raise GenericError('E_FEATURE_UNAVAILABLE', f)
    from dataclasses import replace
    return replace(req, required_timeframes=tuple(sorted(req.required_timeframes)),
                   features=tuple(sorted(req.features,key=lambda f:f['name'])))
