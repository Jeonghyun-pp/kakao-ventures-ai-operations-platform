# Installation and service contract

## 1. Local boundaries

Code lives in the clone. Runtime data and credentials do not:

```text
$KV_DATA    default ~/kv-data    ledgers, state, downloaded working data
$KV_CONFIG  default ~/kv-config  .env, connections, people, rollout gates
```

Run `./kv setup` once. It copies example configuration without overwriting existing files. `./kv doctor` checks local configuration structure, placeholders, credential-file paths and `.env` permissions. It lists explicitly enabled rollout settings; it does not verify remote credentials, API permissions or model tools.

## 2. Credentials and permissions

Use dedicated service accounts or least-privilege tokens where the provider supports them.

| Provider | Local setting | Required access |
|---|---|---|
| Notion | `NOTION_TOKEN` | Read/write only the databases used by enabled workflows |
| Slack | `SLACK_BOT_TOKEN` | Post and read reactions/files in configured channels |
| Gmail | `GMAIL_TOKEN_FILE` | Read, label, archive, and create drafts; sending is not required |
| Google Sheets/Drive | `GOOGLE_CREDENTIALS_FILE` | Read the intake sheet and referenced decks; write intake fallback rows if enabled |
| Apify | `APIFY_TOKEN` | Only needed for enabled public-source collection |
| Model runner | provider-specific key | Only needed by the model adapter selected for live judgments |

Share the Google Sheet and Drive folders with the service-account email. Share each Notion database with the integration. Invite the Slack bot to each configured channel.

## 3. Notion databases

Property names are an external API contract. The workflows validate writable identifiers and select options before mutation; an unknown or unavailable schema should stop the action rather than create a best-effort record.

### Business-plan intake

Configure `notion.bplan_database_id`. Required properties used by the workflow include:

- `기업명` (title), `접수키` (text), `DB 업데이트 시간` (created time/date)
- `이메일`, `대표명`, `연락처`, `서비스정의`, `문제정의`, `솔루션`, `비즈니스모델`, `경쟁사`, `팀이력`, `경쟁력`, `추가내용` (text)
- `사업계획서` (files/URL), `담당자` (select), `to do` (select), `점수` (select)

The values in `intake.assignee` and workflow criteria must already exist as select options.

### App Store sourcing

Configure `notion.appstore_database_id` and `notion.early_stage_database_id`. The sourcing database uses `서비스명`, `App ID`, `One Liner`, `Post URL`, `Rating`, `Genre`, and `DB로 업데이트`. Promotion targets use `법인(서비스)`, `한 줄 설명`, and `영역`.

### LinkedIn sourcing

Configure `notion.linkedin_database_id`, `notion.early_stage_database_id`, and `notion.startup_database_id`. The workflow skill document lists the review-card fields and allowed decision values. Create those options before enabling promotion.

Notion workspaces evolve, so run the relevant workflow's collect/read command against a non-production test database before opening its outbound gate.

## 4. Safe rollout

`deployment.example.yaml` closes every mutation/report gate. Keep that file's copied values while checking live reads and dry-run output. Missing files or switches stay closed instead of inheriting historical activation settings from `constraints.yaml`. Set rollout values in `$KV_CONFIG/deployment.yaml`; ordinary API limits and workflow criteria still fall back to `constraints.yaml`.

1. Connect one provider and verify read access.
2. Run the workflow's collect/judge path against test records.
3. Inspect the ledger, replay/diff output, proposed Slack cards, Notion changes, and drafts.
4. Enable the smallest allow-listed action set for one workflow.
5. Verify health/status output before scheduling it.

Final investment decisions remain human decisions. The platform can create drafts and approval requests. The mail workflow also contains an explicit sending path, disabled by the default deployment gates; review its branch permissions and required Gmail access before enabling it. A draft, an approval request and a sent message are distinct outcomes.

## 5. Scheduling and recovery

Use `./kv install` only after manual runs succeed. `./kv status`, `./kv health`, and the per-workflow reports expose incomplete runs and adapter failures. Runtime ledgers under `$KV_DATA` are the recovery source; back them up separately from replaceable credentials.

Scheduling captures the resolved `KV_CONFIG` and `KV_DATA` paths from the installing shell for both launchd and cron. Logs go to `$KV_DATA/_logs`. Reinstall schedules after changing either path. The generated paths and arguments support spaces and XML/shell punctuation; cron rejects embedded newlines.

Mail exception switches are independent: explicitly listed `mail_observe.open_kinds` can run below `outbound: on`. Optional `open_categories` narrows each listed kind; an explicitly opened kind without a category entry applies to all categories. Both maps/lists default empty. Sending also requires an explicit `send_branches` list and the existing send guards.
