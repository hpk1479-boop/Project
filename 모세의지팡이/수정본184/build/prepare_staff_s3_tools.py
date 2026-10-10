from pathlib import Path
root=Path(__file__).resolve().parents[1]
for before,after in [('run_staff_s2_gates.py','run_staff_s3_gates.py'),
                     ('inventory_part3_legacy.py','inventory_part3_legacy_s3.py')]:
    text=(root/'build'/before).read_text('utf-8-sig')
    text=text.replace('staff_s2_evidence','staff_s3_evidence').replace('staff_s2/','staff_s3/')
    text=text.replace("STAFF_STAGE='S2'","STAFF_STAGE='S3'").replace("'stage':'S2'","'stage':'S3'")
    text=text.replace('s1_frozen_manifest.json','s2_frozen_manifest.json')
    if before=='run_staff_s2_gates.py':
        text=text.replace('S2 gate','S3 gate')
    (root/'build'/after).write_text(text,encoding='utf-8')
