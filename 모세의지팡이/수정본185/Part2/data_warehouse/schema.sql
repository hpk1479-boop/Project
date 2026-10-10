-- Integer epoch seconds for completed M1; nanoseconds only for original tick replay.
-- Every identity is content-/source-based. No installation or drive path is stored.
CREATE TABLE IF NOT EXISTS warehouse_meta (key VARCHAR PRIMARY KEY, value VARCHAR NOT NULL);
CREATE TABLE IF NOT EXISTS sources (
 source_id VARCHAR PRIMARY KEY, broker VARCHAR NOT NULL, server VARCHAR NOT NULL,
 symbol VARCHAR NOT NULL, instrument_json VARCHAR NOT NULL);
CREATE TABLE IF NOT EXISTS raw_m1 (
 source_id VARCHAR NOT NULL, timestamp BIGINT NOT NULL,
 open DOUBLE NOT NULL, high DOUBLE NOT NULL, low DOUBLE NOT NULL, close DOUBLE NOT NULL,
 tick_volume UBIGINT NOT NULL, spread INTEGER NOT NULL, real_volume UBIGINT NOT NULL,
 PRIMARY KEY (source_id, timestamp));
CREATE TABLE IF NOT EXISTS raw_validations (
 validation_id VARCHAR PRIMARY KEY, source_id VARCHAR NOT NULL,
 requested_start BIGINT NOT NULL, requested_end BIGINT NOT NULL,
 available_start BIGINT, available_end BIGINT, row_count BIGINT NOT NULL,
 validation VARCHAR NOT NULL, coverage VARCHAR NOT NULL, raw_sha256 VARCHAR NOT NULL,
 detail_json VARCHAR NOT NULL, checked_at VARCHAR NOT NULL);
CREATE TABLE IF NOT EXISTS timeframe_bars (
 source_id VARCHAR NOT NULL, timeframe VARCHAR NOT NULL, timestamp BIGINT NOT NULL,
 open DOUBLE NOT NULL, high DOUBLE NOT NULL, low DOUBLE NOT NULL, close DOUBLE NOT NULL,
 tick_volume UBIGINT NOT NULL, complete BOOLEAN NOT NULL, calc_hash VARCHAR NOT NULL,
 PRIMARY KEY (source_id, timeframe, timestamp));
CREATE TABLE IF NOT EXISTS feature_sets (
 feature_key VARCHAR PRIMARY KEY, source_id VARCHAR NOT NULL, symbol VARCHAR NOT NULL,
 timeframe VARCHAR NOT NULL, feature VARCHAR NOT NULL, parameters_json VARCHAR NOT NULL,
 calc_hash VARCHAR NOT NULL, environment_json VARCHAR NOT NULL, source_hash VARCHAR NOT NULL,
 evaluation_policy VARCHAR NOT NULL, start_ts BIGINT NOT NULL, end_ts BIGINT NOT NULL,
 warmup_start BIGINT NOT NULL, status VARCHAR NOT NULL, payload_hash VARCHAR NOT NULL,
 metadata_json VARCHAR NOT NULL);
CREATE TABLE IF NOT EXISTS feature_values (
 feature_key VARCHAR NOT NULL, timestamp BIGINT NOT NULL, payload BLOB NOT NULL,
 payload_hash VARCHAR NOT NULL, PRIMARY KEY (feature_key, timestamp));
CREATE TABLE IF NOT EXISTS calculation_checkpoints (
 checkpoint_key VARCHAR PRIMARY KEY, source_id VARCHAR NOT NULL, feature VARCHAR NOT NULL,
 last_timestamp BIGINT NOT NULL, calc_hash VARCHAR NOT NULL, source_prefix_hash VARCHAR NOT NULL,
 payload BLOB NOT NULL, payload_hash VARCHAR NOT NULL);
CREATE TABLE IF NOT EXISTS tick_archives (
 archive_id VARCHAR PRIMARY KEY, source_id VARCHAR NOT NULL, symbol VARCHAR NOT NULL,
 coverage_start_ns BIGINT NOT NULL, coverage_end_ns BIGINT NOT NULL,
 manifest_json VARCHAR NOT NULL, status VARCHAR NOT NULL);
CREATE TABLE IF NOT EXISTS tick_archive_chunks (
 archive_id VARCHAR NOT NULL, chunk_number INTEGER NOT NULL, payload BLOB NOT NULL,
 raw_sha256 VARCHAR NOT NULL, PRIMARY KEY (archive_id, chunk_number));
CREATE TABLE IF NOT EXISTS numerical_replays (
 replay_key VARCHAR PRIMARY KEY, descriptor_json VARCHAR NOT NULL, calc_hash VARCHAR NOT NULL,
 manifest_json VARCHAR NOT NULL, status VARCHAR NOT NULL);
CREATE TABLE IF NOT EXISTS numerical_replay_blocks (
 replay_key VARCHAR NOT NULL, stream_key VARCHAR NOT NULL, block_number INTEGER NOT NULL,
 payload BLOB NOT NULL, payload_hash VARCHAR NOT NULL,
 PRIMARY KEY (replay_key, stream_key, block_number));
CREATE TABLE IF NOT EXISTS tick_collection_jobs (
 job_id VARCHAR PRIMARY KEY, source_id VARCHAR NOT NULL, start_ns BIGINT NOT NULL,
 end_ns BIGINT NOT NULL, instrument_json VARCHAR NOT NULL, status VARCHAR NOT NULL);
CREATE TABLE IF NOT EXISTS tick_collection_chunks (
 job_id VARCHAR NOT NULL, chunk_number INTEGER NOT NULL, descriptor_json VARCHAR NOT NULL,
 payload BLOB NOT NULL, PRIMARY KEY (job_id, chunk_number));

-- LIVE-first independently validated numerical requests.

CREATE TABLE IF NOT EXISTS observation_jobs (
 job_key VARCHAR PRIMARY KEY, descriptor_json VARCHAR NOT NULL, calc_hash VARCHAR NOT NULL,
 environment_json VARCHAR NOT NULL, status VARCHAR NOT NULL, row_count BIGINT NOT NULL,
 input_hash VARCHAR NOT NULL, output_hash VARCHAR NOT NULL, detail_json VARCHAR NOT NULL);
CREATE TABLE IF NOT EXISTS observation_job_blocks (
 job_key VARCHAR NOT NULL, block_number INTEGER NOT NULL, row_count INTEGER NOT NULL,
 inputs BLOB NOT NULL, input_hash VARCHAR NOT NULL, outputs BLOB, output_hash VARCHAR,
 PRIMARY KEY(job_key,block_number));

-- Whole-request common numerical memo; no decisions

CREATE TABLE IF NOT EXISTS common_feature_jobs (
 job_key VARCHAR PRIMARY KEY, descriptor_json VARCHAR NOT NULL, scope_json VARCHAR NOT NULL,
 calc_hash VARCHAR NOT NULL, environment_json VARCHAR NOT NULL, status VARCHAR NOT NULL,
 row_count BIGINT NOT NULL, payload_hash VARCHAR NOT NULL);
CREATE TABLE IF NOT EXISTS common_feature_values (
 job_key VARCHAR NOT NULL, entry_key VARCHAR NOT NULL, checksum VARCHAR NOT NULL, payload BLOB NOT NULL,
 PRIMARY KEY(job_key,entry_key));

-- MT5-native Strategy Tester snapshots. Additive V1 extension.
CREATE TABLE IF NOT EXISTS native_builds (
 build_id VARCHAR PRIMARY KEY, source_id VARCHAR NOT NULL, symbol VARCHAR NOT NULL,
 requested_start BIGINT NOT NULL, requested_end BIGINT NOT NULL,
 recorded_start BIGINT, recorded_end BIGINT, timeframe_count INTEGER NOT NULL,
 row_count BIGINT NOT NULL, format_version INTEGER NOT NULL,
 manifest_json VARCHAR NOT NULL, payload_sha256 VARCHAR NOT NULL,
 status VARCHAR NOT NULL, imported_at VARCHAR NOT NULL);
CREATE TABLE IF NOT EXISTS native_snapshots (
 source_id VARCHAR NOT NULL, timeframe VARCHAR NOT NULL,
 observed_time BIGINT NOT NULL, bar_time BIGINT NOT NULL,
 open DOUBLE NOT NULL, high DOUBLE NOT NULL, low DOUBLE NOT NULL, close DOUBLE NOT NULL,
 hma_6 DOUBLE NOT NULL, hma_17 DOUBLE NOT NULL,
 price_value DOUBLE NOT NULL, price_lower DOUBLE NOT NULL, price_upper DOUBLE NOT NULL, price_basis DOUBLE NOT NULL,
 price_regime_upper DOUBLE NOT NULL, price_regime_lower DOUBLE NOT NULL, price_lower_out DOUBLE NOT NULL, price_upper_out DOUBLE NOT NULL, price_regime_slope DOUBLE NOT NULL,
 rsi_value DOUBLE NOT NULL, rsi_lower DOUBLE NOT NULL, rsi_upper DOUBLE NOT NULL, rsi_basis DOUBLE NOT NULL,
 rsi_regime_upper DOUBLE NOT NULL, rsi_regime_lower DOUBLE NOT NULL, rsi_lower_out DOUBLE NOT NULL, rsi_upper_out DOUBLE NOT NULL, rsi_regime_slope DOUBLE NOT NULL,
 sto_value DOUBLE NOT NULL, sto_lower DOUBLE NOT NULL, sto_upper DOUBLE NOT NULL, sto_basis DOUBLE NOT NULL,
 sto_regime_upper DOUBLE NOT NULL, sto_regime_lower DOUBLE NOT NULL, sto_lower_out DOUBLE NOT NULL, sto_upper_out DOUBLE NOT NULL, sto_regime_slope DOUBLE NOT NULL,
 di_value DOUBLE NOT NULL, di_lower DOUBLE NOT NULL, di_upper DOUBLE NOT NULL, di_basis DOUBLE NOT NULL,
 di_regime_upper DOUBLE NOT NULL, di_regime_lower DOUBLE NOT NULL, di_lower_out DOUBLE NOT NULL, di_upper_out DOUBLE NOT NULL, di_regime_slope DOUBLE NOT NULL,
 build_id VARCHAR NOT NULL,
 PRIMARY KEY(source_id,timeframe,observed_time));
