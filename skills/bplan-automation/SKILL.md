---
name: bplan-automation
description: >-
  Automate Kakao Ventures Bplan (business-plan) intake processing. Use when the user
  wants to (1) SYNC new Bplan submissions from Google Sheets into the Notion DB and move
  the matching Gmail into the assignee label, or (2) generate DROP-mail (rejection) draft
  replies in Gmail for Notion "To Drop" companies. Triggers include: "bplan sync",
  "bplan drop", "비플랜 동기화", "접수건 동기화", "드랍메일", "드랍 메일 초안", "Bplan 자동화".
---

# Bplan Automation

Wraps the Kakao Ventures Bplan intake tool (`project/main.py`) which orchestrates
Google Sheets → Notion → Gmail. Everything (code, `.venv`, credentials) is bundled
under this skill's `project/` directory and uses absolute paths, so it runs from any
working directory.

## Project location

```
~/.claude/skills/bplan-automation/project
```

- Runner (rebuilt-per-machine virtualenv): `project/.venv/bin/python`
- Entry point: `project/main.py`
- Credentials (already present, do NOT print, share, or commit): `.env`,
  `credentials.json`, `gmail_credentials.json`, `token.json`

Define once for convenience in your shell commands:
`PROJ="$HOME/.claude/skills/bplan-automation/project"`

## Two commands

The tool has exactly two subcommands. Each prompts interactively for a Gmail label
(and, for sync, a Notion assignee tag). When running non-interactively via Bash,
**pipe newlines to accept the configured defaults**, or pipe the desired values.

Defaults live at the top of `main.py`: `DEFAULT_GMAIL_LABEL = "3. Evan"`,
`DEFAULT_NOTION_TAG = "에반 Evan"`.

### sync — Google Sheet new submissions → Notion + Gmail label
Reads new rows from the spreadsheet (tracked by the `Setting!A1` counter), creates a
Notion page per company, and moves each matching intake email out of INBOX into the
assignee label.

```bash
# accept defaults (label "3. Evan", tag "에반 Evan")
printf '\n\n' | "$PROJ/.venv/bin/python" "$PROJ/main.py" sync
# or specify label + tag explicitly
printf '3. Evan\n에반 Evan\n' | "$PROJ/.venv/bin/python" "$PROJ/main.py" sync
```

### drop — Notion "To Drop" companies → Gmail rejection draft
Finds Notion pages where `To do = "To Drop"` AND the `드랍메일` checkbox is false,
generates a Korean/English rejection draft as a reply on the original email thread,
and checks the `드랍메일` box. Drafts are **created only, never sent** — the user
reviews and sends manually in Gmail.

```bash
printf '\n' | "$PROJ/.venv/bin/python" "$PROJ/main.py" drop        # default label
printf '3. Evan\n' | "$PROJ/.venv/bin/python" "$PROJ/main.py" drop # explicit label
```

## How to run it

1. Ask the user which command they want (sync or drop) if not stated, and confirm the
   Gmail label / Notion tag if they are not using the default assignee (Evan).
2. Run the corresponding command above with piped input.
3. The stdout emits progress lines (`✅`, `🎉`, or error guidance in Korean). Relay the
   summary — how many companies were processed, and surface any line telling the user to
   contact the admin (관리자에게 문의) or check Notion/Gmail manually.
4. Ignore benign startup noise: `FutureWarning` about Python 3.9 EOL and the
   `NotOpenSSLWarning` from urllib3/LibreSSL. These are not errors.

## Gmail re-authentication

If a run reports `Gmail API 연결 실패` / token expired, the OAuth token needs refreshing.
This requires a **browser and user interaction**, so do not try to run it silently — tell
the user to run this themselves (it opens a browser to log in with the bot Gmail account):

```bash
"$HOME/.claude/skills/bplan-automation/project/.venv/bin/python" \
  "$HOME/.claude/skills/bplan-automation/project/reauth.py"
```

## Changing the assignee (handoff to a new intern)

Edit the two lines near the top of `project/main.py`:
`DEFAULT_GMAIL_LABEL` (their Gmail label) and `DEFAULT_NOTION_TAG` (their Notion
"담당자" tag). The Gmail label name and Notion tag must exactly match names that
actually exist in Gmail and Notion.

## Known limitations / cautions

- **Positional cursor**: sync tracks progress by a row-offset counter in `Setting!A1`.
  Inserting/deleting/sorting sheet rows can skip or duplicate records.
- **Halts on first missing email**: in sync, if an intake email cannot be found in the
  inbox, the whole run stops and stays stuck at that row on future runs until fixed.
- **Non-atomic writes**: if the Notion create (sync) or draft create (drop) succeeds but
  the follow-up counter/checkbox write fails, a re-run can produce a duplicate. If a run
  errors mid-way, check Notion/Gmail before blindly re-running.
- If `python3 -m venv` / the `.venv` is missing or broken (e.g. after moving machines),
  rebuild it: `cd "$PROJ" && rm -rf .venv && python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt`.
