/* Reuse the current shared-list browser suite rather than retaining the retired sidebar fixtures.
 * It checks generation, load, rename, promote, per-part removal/reset, full deletion,
 * AI backtest confirmation, responsive layout and the actual release validation JavaScript.
 * Real HTTP mutations and explicit Part1/Part2 menu navigation are covered alongside
 * this suite by test_strategy_http_ui101.py; no engines or AI services run here.
 */
const path = require('node:path'), {spawnSync} = require('node:child_process');
const root = path.resolve(__dirname, '../..');
const result = spawnSync(process.execPath, [path.join(root, 'verification/ui_strategy103.cjs'),
    process.argv[2], path.join(root, '검증결과/strategy101/ui')], {stdio:'inherit',windowsHide:true});
if (result.error) throw result.error;
process.exitCode = result.status === null ? 1 : result.status;
