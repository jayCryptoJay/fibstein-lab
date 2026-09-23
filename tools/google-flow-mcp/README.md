# Google Flow MCP → Claude Desktop

Installs [TMSSS05/google-flow-browser-mcp](https://github.com/TMSSS05/google-flow-browser-mcp)
and registers it with Claude Desktop. Run it on the computer where Claude Desktop
and Chrome are installed. It works on macOS, Windows and Linux.

It needs Node 18 or newer, git and Google Chrome.

```bash
# from this folder
node setup.mjs --email 49erjjay@gmail.com
```

The script does these steps in order:

1. Clones the server into `~/mcp-servers/google-flow-browser-mcp`, pinned to the commit
   the patch below was made against, and runs `npm install`.
2. Copies `config/flow.config.example.json` to `config/flow.config.json`.
3. Finds the Chrome user data folder for your OS and the profile folder signed in
   as `--email`. It reads Chrome's `Local State` file, which holds the same
   information `chrome://version` shows. If no profile matches, it lists the
   profiles and stops; run it again with `--profile "Profile N"`.
4. Writes `expectedAccount`, `chromeProfile`, `chromeUserDataDir` and `headless: false`
   to the config.
5. Makes `scripts/*.sh` executable (not needed on Windows).
6. Starts Chrome with remote debugging on port 9222. Then it starts the MCP server over
   stdio and checks that it answers `initialize` and `tools/list`, and that
   everything it prints to stdout is valid JSON.
7. Adds `google-flow-browser` under `mcpServers` in `claude_desktop_config.json`.
   It backs up the file first and leaves your other entries alone. The entry uses the
   absolute path to `node`, because Claude Desktop doesn't use your shell's PATH.

Options: `--profile`, `--user-data-dir`, `--dir <install dir>`, `--skip-browser`,
`--force` (replaces an existing `google-flow-browser` entry).

When it's done, **quit Claude Desktop fully** (from the menu bar or system tray, not just
the window) and open it again.

## Fixes applied to the upstream server (`claude-desktop-fixes.patch`)

The upstream server targets OpenCode on Linux. It needs these fixes to work with
Claude Desktop:

- **Logs went to stdout.** MCP stdio uses stdout for JSON-RPC messages, and every
  `logger.info` line was mixed into them. Claude Desktop rejects that output
  ("Unexpected token … is not valid JSON"). The patch sends logs to stderr.
- **The Chrome path and profile were hardcoded** to `/opt/google/chrome/chrome` and
  `~/.config/google-chrome/Profile 3`. The config's `chromeProfile` and
  `chromeUserDataDir` settings were ignored. The patch reads them from the config,
  detects Chrome on macOS, Windows and Linux, and adds an optional `chromePath` setting.
- **Wrong `Local State` path.** The code looked one folder above the user data folder.
- **`start-browser.sh` used Chrome's default profile folder.** Since Chrome 136, Chrome
  ignores `--remote-debugging-port` when it runs on the default profile folder.
  `start-browser.mjs`, which `start-browser.sh` now calls, uses a separate folder
  instead: `chrome-profile-kiara/`. The first time it runs, it copies your profile
  into that folder. If Google asks you to sign in there, sign in once and it stays
  signed in.
- `flow_connect` now reuses a Chrome that is already running before it checks the
  profile path.

## Every day use

Chrome must be running with remote debugging before you use the Flow tools from Claude:

```bash
node ~/mcp-servers/google-flow-browser-mcp/scripts/start-browser.mjs   # or ./scripts/start-browser.sh
```

Claude Desktop starts the MCP server itself, so you don't need to run `start-mcp.sh`.
