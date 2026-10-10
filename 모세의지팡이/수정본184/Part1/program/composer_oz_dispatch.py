"""Independent SPECIAL decisions over one shared OZ observation.

The transaction delays shared-child cancellation, not strategy decisions.
Output arbitration belongs exclusively to manager_KIM.SignalOutput.
"""
import logging
from durable_protocol import identity


def strategy_name(spec_id):
    return str(spec_id)


def source_ids(payload):
    return set(str(x) for x in (payload.get('source_spec_ids') or ()) if x) | ({str(payload['source_spec_id'])} if payload.get('source_spec_id') else set())


def dispatch(manager, event):
    if str(event.get('kind') or 'FINAL_ALERT').upper() != 'FINAL_ALERT':
        return None
    sources = manager._special_oz_source_ids(event)
    if not sources:
        return None
    ids = list(dict.fromkeys([*(event.get('watch_ids') or ()), *([event['watch_id']] if event.get('watch_id') else [])]))
    declared = event.get('watch_sources') or {}
    owners = {}
    for wid in ids:
        payload = manager._active_children.get(wid) or manager._config_chain_active.get(wid) or {}
        owners[wid] = source_ids(payload) or ({str(declared[wid])} if declared.get(wid) else set())
    # Old persisted event receipts have no per-watch attribution. Keep every
    # registered participant rather than silently dropping an unknown owner.
    if not any(owners.values()):
        owners = {wid: set(sources) for wid in ids}
    context = manager._delivery_context
    previous_id = getattr(context, 'event_id', None)
    previous_event = getattr(context, 'oz_event', None)
    transaction = {'ids': set(ids), 'pending': set(), 'completions': [],
                   'originals': {wid: manager._active_children.get(wid) for wid in ids}}
    manager._oz_dispatch_transaction = transaction
    outcomes = {}
    market = event.get('market_event_id') or event.get('event_id')
    try:
        for spec_id in sorted(set(sources)):
            own = [wid for wid in ids if spec_id in owners[wid]]
            if ids and not own:
                continue
            scoped = dict(event, watch_ids=own, source_spec_id=spec_id, source_spec_ids=[spec_id],
                          signal_strategy=strategy_name(spec_id), market_event_id=market,
                          event_id=identity('OZ_STRATEGY', market, spec_id))
            scoped.pop('watch_id', None)
            context.event_id = scoped['event_id']; context.oz_event = scoped
            handler = manager._special_oz_event_handlers.get(spec_id)
            callback = getattr(handler, 'handle_oz_event', None)
            try:
                filtered, expired = manager._filter_config_chain_deadline_event(scoped)
                if not expired:
                    filtered, expired = manager.watch_orchestrator.filter_deadline_event(filtered)
                if expired:
                    result = {'ok': True, 'delivered': True, 'suppressed': True, 'expired': True}
                else:
                    result = callback(filtered) if callable(callback) else None
                    if result is None:
                        result = manager._handle_oz_event_core(filtered)
                outcomes[spec_id] = result
            except Exception as exc:
                logging.exception('SPECIAL OZ 독립 판정 실패 · %s', spec_id,
                                  extra={'trace_module': strategy_name(spec_id)})
                outcomes[spec_id] = {'ok': False, 'delivered': False, 'error': type(exc).__name__ + ': ' + str(exc)}
        unowned = [wid for wid in ids if not owners[wid]]
        if unowned:
            context.event_id = previous_id
            context.oz_event = dict(event, watch_ids=unowned, source_spec_ids=[], source_spec_id=None)
            outcomes['WATCH'] = manager._handle_oz_event_core(context.oz_event)
    finally:
        manager._oz_dispatch_transaction = None
        context.event_id = previous_id; context.oz_event = previous_event
        # A failed participant retains the shared input for a retry. Successful
        # participants' strategy-specific notification receipts prevent resend.
        if outcomes and all(value.get('ok') for value in outcomes.values()):
            manager.special_api.cancel_oz_watches(sorted(wid for wid in transaction['pending']
                if manager._active_children.get(wid) is transaction['originals'].get(wid)))
            for completed in transaction['completions']:
                manager.watch_orchestrator.complete_final_oz(completed, delivered=True)
    if not outcomes:
        return None
    return {'ok': all(v.get('ok') for v in outcomes.values()),
            'delivered': any(v.get('delivered') for v in outcomes.values()),
            'strategy_results': outcomes}
