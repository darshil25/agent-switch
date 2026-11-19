# agent-switch

Hit your usage limit in Claude Code? Type `sw kiro` and carry on in Kiro CLI, with the context of what you were doing. Type `sw claude` to go back.

```
$ sw kiro
sw: claude -> kiro: new session with 12 message(s) from Claude Code. Saved to ~/.sw/handoff-....md
```

It works by reading the session transcript the first agent already saved on disk. No AI model is involved in the switch, so it costs no tokens and works even when you are out of quota.

Supported agents: **Claude Code** and **Kiro CLI**. Adding another is one file, see [Adding an agent](#adding-an-agent).

## Install

You need Python 3.9 or newer, and at least one of the supported CLIs installed and logged in.

```bash
pipx install git+https://github.com/darshil25/agent-switch
```

Plain pip works too (`pip install git+https://github.com/darshil25/agent-switch`). This installs one command, `sw`.

## Use

Run it from the same folder where you started the agent.

```bash
sw kiro      # continue in Kiro
sw claude    # continue in Claude Code
```

The first time, the other agent gets a short summary of the work so far and starts a new session. After that, switching back **resumes the session that agent already had in this folder** and only tells it what happened elsewhere since it last stopped.

If that agent has several earlier sessions in the folder, you get a short menu (Enter picks the latest):

```
Earlier Kiro sessions in this folder:
  1) Improve animation   [sess_ff5b66ff]  2 min ago   <- default
  2) Fix login bug       [sess_c4b8c1c6]  3 days ago
  n) start a new session with the full handoff
Choose [1]:
```

| Command or flag | What it does |
|---|---|
| `sw kiro -y` | Skip the menu, resume the latest session |
| `sw kiro --new` | Do not resume, start a new session with the full summary |
| `sw kiro --session ID` | Resume a specific session (the start of the ID is enough) |
| `sw kiro --from claude` | Read from a named agent instead of the most recent one |
| `sw kiro --dry-run` | Print the message, start nothing, save nothing |
| `sw kiro --max-chars N` | Size cap for the summary (default 30000, maximum 100000) |
| `sw kiro -- --trust-all-tools` | Everything after `--` is passed to the agent |
| `sw list` | List the sessions in this folder |
| `sw clean` | Delete saved summaries (see below) |

Without a terminal (a script, for example) there is no menu and the latest session is resumed.

## How it works

1. It finds the newest session for the current folder in the other agent's own storage.
2. It condenses it: your first message as the goal, the recent conversation, the files that were edited and a one-line note per action. Thinking, file reads and command output are dropped.
3. It adds `git status` for the folder (or for git repos one level down).
4. It starts the target agent with that text as the first message, using the agent's own resume feature when a matching session exists. `sw` replaces itself with the agent, so nothing keeps running in the background.

The summary is not the full transcript. The next agent can read your files for detail.

## What is stored where

`sw` only reads the agents' folders and writes in one place.

| Location | Written by | What | How to remove |
|---|---|---|---|
| `~/.sw/` (or `$SW_HOME`) | `sw` | A copy of each summary it sent, `handoff-*.md`, plus `latest.md` | `sw clean` (older than 7 days), `sw clean --all`, or delete the folder |
| `~/.claude/projects/<folder>/<id>.jsonl` | Claude Code | Claude sessions (read only by `sw`) | Delete the file |
| `~/.kiro/sessions/` | Kiro CLI | Kiro sessions (read only by `sw`) | `kiro-cli chat --delete-session <ID>` |

Footprint: `sw` keeps the newest 20 copies and deletes older ones automatically (`SW_KEEP=N` changes that). Each is typically a few KB and never more than about 100 KB. There is no database, daemon or cache.

Privacy: the saved copies contain conversation text from your sessions. The folder is private to your user (permissions 0700, files 0600). `sw clean` only deletes files `sw` created. The summary is also sent to the target agent, exactly like text you would type into it.

## Limits

- Developed and tested on macOS with Claude Code 2.1 and Kiro CLI 3.2. The test suite also runs on Linux in CI, but real Linux use is untested by the author. Windows is not supported.
- Kiro is supported on its v3 engine (the default). Older v2 sessions are not read.
- Only Claude Code and Kiro CLI are implemented. Codex is not.
- Run `sw` from the folder where the agent was started. If nothing is found it tells you where the latest session was.
- Do not keep the same session open in two terminals at once.
- The summary is a condensed record and can miss detail. Agents are told to check the real files first.
- The agents' transcript formats are not public APIs. If an update changes them, `sw` may stop finding sessions. The tests describe the shapes it expects.

## Adding an agent

Each agent is one file in `src/agent_switch/agents/` that subclasses `Agent` (`agents/base.py`) and implements five things: `sessions(cwd)`, `parse(session)`, `describe(session)`, `latest_folder()` and `argv(prompt, resume_id, extra)`. Register it in `agents/__init__.py` and it appears as `sw <name>`. Look at `agents/kiro.py` for a short example. Please base the parser on real session files and add fixtures to `tests/`.

## Development

```bash
python -m unittest discover -s tests -t .
```

The tests use only the standard library. They run `sw` against fake `claude` and `kiro-cli` programs and synthetic session files, so no real agent or login is needed.

## License

MIT
