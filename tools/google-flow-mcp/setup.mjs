#!/usr/bin/env node
// One-shot installer: Google Flow Browser MCP -> Claude Desktop.
// Runs on macOS, Windows and Linux. Needs Node >= 18, git, and Google Chrome.
//
//   node setup.mjs --email you@gmail.com [--profile "Profile 3"] [--user-data-dir <dir>]
//                  [--dir <install dir>] [--skip-browser] [--force]
//
// Steps: clone + npm install, copy config, find the Chrome profile signed in to
// --email, write config, apply claude-desktop-fixes.patch, start Chrome with CDP,
// smoke-test the MCP server over stdio, and add it to claude_desktop_config.json
// (backed up first; other entries are left untouched).
import fs from 'fs';
import os from 'os';
import path from 'path';
import { spawn, spawnSync } from 'child_process';
import { fileURLToPath } from 'url';

const REPO_URL = 'https://github.com/TMSSS05/google-flow-browser-mcp.git';
const PINNED_COMMIT = '0c8e80ae4acb4475f8d7ba3b8c4e3b92c6b64b6a'; // the patch is made against this
const SERVER_NAME = 'google-flow-browser';
const HERE = path.dirname(fileURLToPath(import.meta.url));
const PATCH = path.join(HERE, 'claude-desktop-fixes.patch');

const opts = parseArgs(process.argv.slice(2));
const INSTALL_DIR = path.resolve(opts.dir || path.join(os.homedir(), 'mcp-servers', 'google-flow-browser-mcp'));

const step = (n, m) => console.log(`\n[${n}] ${m}`);
const ok = (m) => console.log(`    ✓ ${m}`);
const note = (m) => console.log(`    • ${m}`);
function die(m) { console.error(`\n✗ ${m}`); process.exit(1); }

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('--')) continue;
    const key = a.slice(2).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    const next = argv[i + 1];
    if (next === undefined || next.startsWith('--')) out[key] = true;
    else { out[key] = next; i++; }
  }
  return out;
}

// npm is npm.cmd on Windows and needs a shell; everything else runs directly so
// paths with spaces (C:\Program Files\...) aren't split.
const needsShell = (cmd) => process.platform === 'win32' && cmd === 'npm';

function run(cmd, args, cwd) {
  const r = spawnSync(cmd, args, { cwd, stdio: 'inherit', shell: needsShell(cmd) });
  if (r.status !== 0) die(`${cmd} ${args.join(' ')} failed (exit ${r.status})`);
}

function quiet(cmd, args, cwd) {
  return spawnSync(cmd, args, { cwd, encoding: 'utf8', shell: needsShell(cmd) });
}

function chromeUserDataDir() {
  if (process.platform === 'darwin') return path.join(os.homedir(), 'Library/Application Support/Google/Chrome');
  if (process.platform === 'win32') return path.join(process.env.LOCALAPPDATA || '', 'Google', 'Chrome', 'User Data');
  return path.join(os.homedir(), '.config/google-chrome');
}

function claudeDesktopConfigPath() {
  if (process.platform === 'darwin') return path.join(os.homedir(), 'Library/Application Support/Claude/claude_desktop_config.json');
  if (process.platform === 'win32') {
    const classic = path.join(process.env.APPDATA || '', 'Claude', 'claude_desktop_config.json');
    if (fs.existsSync(path.dirname(classic))) return classic;
    // Microsoft Store install keeps its config in a virtualized AppData
    const pkgs = path.join(process.env.LOCALAPPDATA || '', 'Packages');
    const store = fs.existsSync(pkgs) && fs.readdirSync(pkgs).find(d => d.startsWith('Claude_'));
    if (store) return path.join(pkgs, store, 'LocalCache', 'Roaming', 'Claude', 'claude_desktop_config.json');
    return classic;
  }
  return path.join(os.homedir(), '.config/Claude/claude_desktop_config.json');
}

function listProfiles(userDataDir) {
  const localState = path.join(userDataDir, 'Local State');
  if (!fs.existsSync(localState)) return [];
  const cache = JSON.parse(fs.readFileSync(localState, 'utf8'))?.profile?.info_cache || {};
  return Object.entries(cache).map(([folder, info]) => ({
    folder,
    name: info.name || '',
    email: (info.user_name || '').toLowerCase(),
  }));
}

// ---------------------------------------------------------------------------

const email = typeof opts.email === 'string' ? opts.email.trim() : '';
if (!email) die('Pass your Google account: node setup.mjs --email you@gmail.com');
if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) die(`"${email}" doesn't look like an email address`);
if (/@gnail\.com$/i.test(email)) note(`"${email}" ends in gnail.com — did you mean gmail.com?`);
if (Number(process.versions.node.split('.')[0]) < 18) die(`Node >= 18 required (found ${process.version})`);
if (quiet('git', ['--version']).status !== 0) die('git is required');
if (!fs.existsSync(PATCH)) die(`Missing ${PATCH} — run this script from its own folder in the repo`);

step(1, `Clone + npm install into ${INSTALL_DIR}`);
if (fs.existsSync(path.join(INSTALL_DIR, '.git'))) {
  ok('Already cloned');
} else {
  fs.mkdirSync(path.dirname(INSTALL_DIR), { recursive: true });
  run('git', ['clone', REPO_URL, INSTALL_DIR]);
  run('git', ['checkout', '--quiet', PINNED_COMMIT], INSTALL_DIR);
}
if (quiet('git', ['apply', '--reverse', '--check', PATCH], INSTALL_DIR).status === 0) {
  ok('Claude Desktop fixes already applied');
} else if (quiet('git', ['apply', '--check', PATCH], INSTALL_DIR).status === 0) {
  run('git', ['apply', PATCH], INSTALL_DIR);
  ok('Applied claude-desktop-fixes.patch (stderr logging, config-driven Chrome paths)');
} else {
  die(`Could not apply ${PATCH} — the clone at ${INSTALL_DIR} has local changes or a different commit`);
}
run('npm', ['install', '--no-fund', '--no-audit'], INSTALL_DIR);
ok('npm install done');

step(2, 'Create config/flow.config.json');
const configPath = path.join(INSTALL_DIR, 'config', 'flow.config.json');
if (fs.existsSync(configPath)) ok('Already exists — updating it in place');
else { fs.copyFileSync(path.join(INSTALL_DIR, 'config', 'flow.config.example.json'), configPath); ok('Copied from example'); }

step(3, 'Find your Chrome profile');
const userDataDir = path.resolve(typeof opts.userDataDir === 'string' ? opts.userDataDir : chromeUserDataDir());
if (!fs.existsSync(userDataDir)) die(`Chrome user data folder not found at ${userDataDir} — pass --user-data-dir (see chrome://version → Profile Path)`);
const profiles = listProfiles(userDataDir);
let profile = typeof opts.profile === 'string' ? opts.profile : '';
if (!profile) {
  const match = profiles.find(p => p.email === email.toLowerCase());
  if (match) profile = match.folder;
  else {
    console.log(`    No Chrome profile is signed in as ${email}. Profiles found:`);
    for (const p of profiles) console.log(`      "${p.folder}"  ${p.name}  ${p.email || '(not signed in)'}`);
    die('Re-run with --profile "<folder>" (or sign that account into Chrome first)');
  }
}
if (!fs.existsSync(path.join(userDataDir, profile))) die(`Profile folder "${profile}" not found in ${userDataDir}`);
ok(`User data directory: ${userDataDir}`);
ok(`Profile folder:      ${profile}`);

step(4, 'Write config');
const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
Object.assign(config, { expectedAccount: email, chromeProfile: profile, chromeUserDataDir: userDataDir, headless: false });
fs.writeFileSync(configPath, JSON.stringify(config, null, 2) + '\n');
ok(`expectedAccount=${email}, chromeProfile="${profile}", headless=false`);

step(5, 'Make scripts executable');
if (process.platform === 'win32') note('Skipped on Windows (use the .mjs scripts with node)');
else {
  for (const f of fs.readdirSync(path.join(INSTALL_DIR, 'scripts'))) {
    if (f.endsWith('.sh') || f.endsWith('.mjs')) fs.chmodSync(path.join(INSTALL_DIR, 'scripts', f), 0o755);
  }
  ok('chmod +x scripts/*.sh');
}

step(6, 'Start Chrome (CDP) and test the MCP server');
if (opts.skipBrowser) note('Skipped browser launch (--skip-browser)');
else run(process.execPath, [path.join(INSTALL_DIR, 'scripts', 'start-browser.mjs')], INSTALL_DIR);
await smokeTest();

step(7, 'Register in Claude Desktop');
registerClaudeDesktop();

console.log(`
Done.
  • Quit Claude Desktop completely and reopen it (closing the window is not enough —
    use Quit from the menu bar / system tray). "${SERVER_NAME}" should then appear in
    Claude Desktop's connectors/tools list.
  • The Chrome window uses its own profile copy in ${path.join(INSTALL_DIR, 'chrome-profile-kiara')}.
    If Google Flow shows a sign-in page there, sign in as ${email} once; it will be remembered.
  • Before using it from Claude, make sure that Chrome is running:
      node "${path.join(INSTALL_DIR, 'scripts', 'start-browser.mjs')}"
`);

// ---------------------------------------------------------------------------

async function smokeTest() {
  const child = spawn(process.execPath, [path.join(INSTALL_DIR, 'src', 'index.js')], { cwd: os.homedir(), stdio: ['pipe', 'pipe', 'pipe'] });
  let stdout = '';
  let stderr = '';
  child.stdout.on('data', d => { stdout += d; });
  child.stderr.on('data', d => { stderr += d; });
  const send = (msg) => child.stdin.write(JSON.stringify({ jsonrpc: '2.0', ...msg }) + '\n');
  send({ id: 1, method: 'initialize', params: { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'setup', version: '1' } } });
  send({ method: 'notifications/initialized' });
  send({ id: 2, method: 'tools/list' });
  send({ id: 3, method: 'tools/call', params: { name: 'flow_status', arguments: {} } });

  const deadline = Date.now() + 15000;
  while (Date.now() < deadline && stdout.split('\n').filter(Boolean).length < 3) await new Promise(r => setTimeout(r, 200));
  child.kill();

  const lines = stdout.split('\n').filter(Boolean);
  const bad = lines.filter(l => { try { JSON.parse(l); return false; } catch { return true; } });
  if (bad.length) die(`MCP server wrote non-JSON to stdout (Claude Desktop would reject it):\n${bad.slice(0, 3).join('\n')}`);
  const tools = lines.map(l => JSON.parse(l)).find(m => m.id === 2)?.result?.tools;
  if (!tools) die(`MCP server did not answer tools/list.\nstderr:\n${stderr}`);
  if (/ERROR|\[CONFIG\] Failed/.test(stderr)) die(`MCP server logged errors:\n${stderr}`);
  ok(`MCP server started cleanly and exposes ${tools.length} tools`);
}

function registerClaudeDesktop() {
  const file = claudeDesktopConfigPath();
  let current = {};
  if (fs.existsSync(file)) {
    const raw = fs.readFileSync(file, 'utf8');
    try { current = raw.trim() ? JSON.parse(raw) : {}; }
    catch (e) { die(`${file} is not valid JSON (${e.message}) — fix it by hand; nothing was changed`); }
  }
  current.mcpServers ??= {};
  if (current.mcpServers[SERVER_NAME] && !opts.force) {
    note(`"${SERVER_NAME}" already registered in ${file} — left as is (use --force to replace)`);
    return;
  }
  if (fs.existsSync(file)) {
    const backup = `${file}.backup-${new Date().toISOString().replace(/[:.]/g, '-')}`;
    fs.copyFileSync(file, backup);
    ok(`Backup: ${backup}`);
  } else {
    fs.mkdirSync(path.dirname(file), { recursive: true });
  }
  // Absolute node path: Claude Desktop doesn't inherit your shell PATH (nvm, Homebrew).
  current.mcpServers[SERVER_NAME] = {
    command: process.execPath,
    args: [path.join(INSTALL_DIR, 'src', 'index.js')],
  };
  fs.writeFileSync(file, JSON.stringify(current, null, 2) + '\n');
  ok(`Added "${SERVER_NAME}" to ${file}`);
  const others = Object.keys(current.mcpServers).filter(k => k !== SERVER_NAME);
  if (others.length) ok(`Kept existing servers: ${others.join(', ')}`);
}
