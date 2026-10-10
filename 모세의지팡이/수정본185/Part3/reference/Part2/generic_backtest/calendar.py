"""Explicit research grid or reviewed, effective-date broker segments."""
from bisect import bisect_right
from .contracts import ROOT, GenericError, TIMEFRAMES
from .canonical import identity, read_json, file_hash
from .paths import plain_path,internal_path


class CalendarRegistry:
    def __init__(self, definition):
        self.definition = definition
        self.kind = definition.get('kind')
        self.revision = identity(definition)
        if self.kind == 'UTC_GRID_RESEARCH_V1':
            if definition.get('explicit_research_choice') is not True:
                raise GenericError('E_CALENDAR_UNVERIFIED', 'explicit UTC research choice required')
            self.segments = ()
        elif self.kind == 'BROKER_ALIGNED_V1':
            evidence_hash=definition.get('reviewed_evidence_sha256')
            if not isinstance(evidence_hash,str) or len(evidence_hash)!=64:
                raise GenericError('E_CALENDAR_UNVERIFIED', 'historical broker evidence required')
            approval=plain_path(ROOT/'generic_approvals/calendars'/(identity(definition)+'.json'))
            if not approval.is_file(): raise GenericError('E_CALENDAR_UNVERIFIED','calendar has no explicit review approval')
            record=read_json(approval)
            if record.get('status')!='APPROVED' or record.get('definition_hash')!=identity(definition):
                raise GenericError('E_CALENDAR_UNVERIFIED','calendar approval differs')
            evidence=internal_path(record['evidence_path'])
            if file_hash(evidence)!=evidence_hash: raise GenericError('E_CALENDAR_UNVERIFIED','historical evidence drift')
            self.segments = tuple(definition.get('segments', ()))
            if not self.segments or any(s['end_ns'] <= s['start_ns'] for s in self.segments):
                raise GenericError('E_CALENDAR_UNVERIFIED', 'effective dates')
            if any(a['end_ns'] > b['start_ns'] for a,b in zip(self.segments,self.segments[1:])):
                raise GenericError('E_CALENDAR_UNVERIFIED', 'overlapping segments')
        else: raise GenericError('E_CALENDAR_UNVERIFIED', self.kind)

    def interval_for(self, symbol, tf, utc_ns):
        if not symbol or tf not in TIMEFRAMES: raise GenericError('E_CALENDAR_UNVERIFIED', tf)
        origin = 0
        if self.segments:
            segment = next((s for s in self.segments if s['start_ns'] <= utc_ns < s['end_ns']), None)
            if segment is None: raise GenericError('E_CALENDAR_UNVERIFIED', 'outside effective dates')
            origin = segment['origin_ns']
        width = TIMEFRAMES[tf] * 1_000_000_000
        start = origin + (utc_ns-origin)//width*width
        if self.segments and (start < segment['start_ns'] or start+width > segment['end_ns']):
            raise GenericError('E_CALENDAR_UNVERIFIED', 'bar spans calendar revision')
        return start, start+width
