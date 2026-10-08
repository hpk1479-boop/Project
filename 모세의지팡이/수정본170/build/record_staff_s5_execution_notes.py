"""Preserve the distinction between failed preflight and the one executed G2."""
from staff_s5_evidence import *

write(OUT/'execution_notes.json',{
    'G2_initial_attempt':{'log':'final_G2.log','result':'aborted in immutable preflight; no candidate golden computation ran'},
    'G2_executed_once':{'log':'final_G2_after_preflight.log','synthetic':read(OUT/'synthetic_execution.json'),
                        'actual':read(OUT/'actual_execution.json')},
    'runtime_drift_policy':'Do not restore or modify revision11 runtime files. Keep original_and_goldens_frozen false. Continue independent result gates only when original source and protected copied evidence remain byte-identical.',
    'development_failures':[
        {'log':'related_first.log','cause':'New S5 parametrization used pytest reserved request name; collection stopped'},
        {'log':'related_second.log','cause':'Unchanged collection error after wrong relative edit path; no tests ran'},
        {'log':'related_third.log','result':'94 passed; 1 temporary directory PermissionError'},
        {'log':'related_audit.log','result':'Isolated import retry passed; 110 passed/64 subtests; pre-registration source integrity failure was expected'},
        {'log':'related_event.log','result':'49 passed; 6 old tests referenced removed STAFF wonbi function'},
        {'log':'related_root_migration.log','result':'Only the 6 identified wonbi tests rerun; all passed'}],
    'performance_diagnosis':{'log':'request0_diagnosis.log','purpose':'Failure-only cProfile; does not replace official samples',
                             'gate_rerun':False,'policy_changed':False}})
