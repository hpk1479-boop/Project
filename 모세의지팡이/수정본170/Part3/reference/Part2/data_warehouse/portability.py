"""Checkpoint, copy, SHA-256 verification and only then remove the source."""
from __future__ import annotations
import os
from pathlib import Path
import shutil
import uuid
from .legacy import file_hash
from .store import Store


def copy_database(source, destination, *, move=False):
    source=Path(source).resolve(); destination=Path(destination).resolve()
    if source==destination: raise ValueError('동일한 원본/대상 경로입니다.')
    if not source.is_file(): raise FileNotFoundError(source)
    if destination.exists(): raise FileExistsError('대상 파일이 이미 있습니다. 덮어쓰지 않습니다.')
    destination.parent.mkdir(parents=True,exist_ok=True)
    staging=destination.with_name(destination.name+'.copy-'+uuid.uuid4().hex)
    # Keep the read/write connection open through copy + hash verification.
    # DuckDB's file lock rejects another process writer; GUI serializes all jobs.
    try:
        with Store(source) as store:
            store.checkpoint_file()
            if Path(str(source)+'.wal').exists() and Path(str(source)+'.wal').stat().st_size:
                raise RuntimeError('CHECKPOINT 이후 WAL이 남아 있어 복사를 중단합니다.')
            before=(source.stat().st_size,file_hash(source))
            with source.open('rb') as src, staging.open('xb') as dst:
                shutil.copyfileobj(src,dst,1<<20);dst.flush();os.fsync(dst.fileno())
            after=(staging.stat().st_size,file_hash(staging))
            if before!=after or before!=(source.stat().st_size,file_hash(source)):
                raise RuntimeError('DB_SIZE_OR_SHA256_MISMATCH: 원본을 보존합니다.')
        # Also open the completed copy as a database before publication/deletion.
        with Store(staging,read_only=True): pass
        # Exclusive creation also supports external FAT/exFAT drives (no hard link).
        published=False
        try:
            with destination.open('xb') as dst:
                published=True
                with staging.open('rb') as src:shutil.copyfileobj(src,dst,1<<20)
                dst.flush();os.fsync(dst.fileno())
            if before!=(destination.stat().st_size,file_hash(destination)):
                raise RuntimeError('PUBLISHED_COPY_HASH_MISMATCH')
        except BaseException:
            if published:destination.unlink(missing_ok=True)
            raise
        staging.unlink()
        if move:
            wal=Path(str(source)+'.wal')
            if wal.exists() and wal.stat().st_size:
                raise RuntimeError('SOURCE_WAL_CHANGED: 원본과 복사본을 보존합니다.')
            if before!=(source.stat().st_size,file_hash(source)):
                raise RuntimeError('SOURCE_CHANGED_AFTER_COPY: 복사본과 원본을 모두 보존합니다.')
            source.unlink()
        return {'status':'PASS','operation':'MOVE' if move else 'COPY',
                'bytes':before[0],'sha256':before[1],'destination':str(destination)}
    finally:
        staging.unlink(missing_ok=True)
