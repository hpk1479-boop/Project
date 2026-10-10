"""Whole-request feature eligibility. No partial stitching or permissive coercion."""
from __future__ import annotations
import gzip
import hashlib
import json
from .store import Store,dumps,sha
from calculations.identity import calculation_hash,environment_identity


class FeatureMiss(ValueError):
    def __init__(self,reason):self.reason=reason;super().__init__(reason)


def check_feature_metadata(row: dict, request: dict):
    checks=(('source_id','SOURCE_IDENTITY_MISMATCH'),('symbol','SYMBOL_MISMATCH'),
            ('timeframe','TIMEFRAME_MISMATCH'),('feature','FEATURE_MISMATCH'),
            ('parameters','PARAMETER_MISMATCH'),('calc_hash','CALC_HASH_MISMATCH'),
            ('environment','CALCULATION_ENVIRONMENT_MISMATCH'),('source_hash','SOURCE_REVISION_MISMATCH'),
            ('evaluation_policy','EVALUATION_POLICY_MISMATCH'))
    for field,reason in checks:
        if row.get(field)!=request.get(field):raise FeatureMiss(reason)
    if row.get('status')!='PASS':raise FeatureMiss('VALIDATION_NOT_PASS')
    if row['start_ts']>request['start_ts'] or row['end_ts']<request['end_ts']:
        raise FeatureMiss('FULL_RANGE_MISS')
    if row['warmup_start']>request['warmup_start']:raise FeatureMiss('WARMUP_INSUFFICIENT')
    return True


def read_feature(store,request,*,expected_timestamps):
    """Read all requested rows or raise before exposing any value to a caller."""
    names=('feature_key','source_id','symbol','timeframe','feature','parameters','calc_hash','environment',
           'source_hash','evaluation_policy','start_ts','end_ts','warmup_start','status','payload_hash','metadata')
    rows=store.db.execute('SELECT * FROM feature_sets WHERE source_id=? AND symbol=? AND timeframe=? AND feature=? ORDER BY end_ts DESC',
                          [request['source_id'],request['symbol'],request['timeframe'],request['feature']]).fetchall()
    last_reason='FEATURE_NOT_BUILT'
    for raw in rows:
        row=dict(zip(names,raw))
        for key in ('parameters','environment','metadata'):row[key]=json.loads(row[key])
        try:check_feature_metadata(row,request)
        except FeatureMiss as exc:last_reason=exc.reason;continue
        payloads=store.db.execute('SELECT timestamp,payload,payload_hash FROM feature_values WHERE feature_key=? ORDER BY timestamp',
                                  [row['feature_key']]).fetchall()
        total=hashlib.sha256();selected=[]
        for stamp,payload,checksum in payloads:
            payload=bytes(payload)
            if sha(payload)!=checksum:raise FeatureMiss('FEATURE_PAYLOAD_HASH_MISMATCH')
            total.update(str(stamp).encode()+bytes.fromhex(checksum))
            if request['start_ts']<=stamp<request['end_ts']:
                selected.append((stamp,json.loads(gzip.decompress(payload))))
        if total.hexdigest()!=row['payload_hash']:raise FeatureMiss('FEATURE_SET_HASH_MISMATCH')
        if [t for t,_ in selected]!=list(expected_timestamps):raise FeatureMiss('FEATURE_TIMESTAMPS_MISMATCH')
        return selected,row
    raise FeatureMiss(last_reason)
