import sqlite3
import pytest
import compare_staff_golden as golden

def database(path,value):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE cases(name TEXT,response TEXT)')
        db.execute('CREATE TABLE frames(sha TEXT,data BLOB)')
        db.execute('INSERT INTO cases VALUES (?,?)',('one',value))

def test_identical_file_skips_exact_deserialization(tmp_path,monkeypatch):
    path=tmp_path/'same.sqlite'
    database(path,'{}')
    def no_exact(*args): raise AssertionError('Unnecessary exact compare')
    monkeypatch.setattr(golden,'compare',no_exact)
    result=golden.compare_hash_first(path,path)
    assert result['equal'] and result['comparison_mode']=='file_sha256'

def test_difference_still_runs_exact_comparison(tmp_path):
    a,b=tmp_path/'a.sqlite',tmp_path/'b.sqlite'
    database(a,'{"status": 1}')
    database(b,'{"status": 2}')
    result=golden.compare_hash_first(a,b)
    assert not result['equal'] and result['different_cases']==['one']
    assert result['comparison_mode']=='exact_fallback'

def test_missing_input_is_not_passed(tmp_path):
    with pytest.raises(FileNotFoundError):
        golden.compare_hash_first(tmp_path/'missing',tmp_path/'missing')
