"""Read immutable market input for an already-decided notification.

No conditions, indicators, delivery or I/O are calculated here.
"""
def market_signal_context(board,symbol,direction,event_time,timeframes=()):
    from event_engine.market import select
    from oz_engine.common import tf_seconds
    timeframes=tuple(timeframes)
    if any(tf_seconds(tf)<=0 for tf in timeframes):
        raise ValueError('알림의 신호 프레임이 확정되지 않았습니다.')
    available=[] if board is None else [tf for candidate,tf in board.feeds
        if candidate==symbol and len(board.snapshot(symbol,tf).time)]
    latest_tf=max(available,key=lambda tf:(board.snapshot(symbol,tf).time[-1],-tf_seconds(tf)),default=None)
    signal_tf=min(set(timeframes),key=tf_seconds) if timeframes else latest_tf
    latest=select(board,symbol,latest_tf) if latest_tf else None
    return {'signal_source':'SIGNAL','signal_tf':signal_tf,'source_tf':signal_tf,
        'symbol':symbol,'direction':direction,'event_time':event_time,
        'current_price':latest.row(-1).get('close') if latest is not None else None}
