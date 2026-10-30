# History and public verification

This repository preserves 128 operational commits and all 174 commits reachable from the earlier automation repository's refs. Each source commit retains its original parent relationships, author date, committer date, and substantive message. Privacy edits change object IDs. Three otherwise identical legacy commits have a `Source-Commit` trailer so removing private material does not collapse distinct original commits.

Code, prompts, policy revisions, regression tests, ADRs, failure reviews, and simplification decisions are sanitized in the commits where they originally appeared. The lineage merge changes no operational files. The following publication commits explicitly capture previously uncommitted work, portable configuration, and deployment documentation; their changes are not backdated.

## Explore the design history

```bash
git log --graph --decorate --oneline --all
git log --follow -- skills/mail/flow.py
git log --all -- docs/adr docs/reviews docs/lessons
git ls-tree -r --name-only legacy-final-2026-09-26 docs
git show legacy-final-2026-09-26:docs/REBUILD-BRIEF.md
```

The four operational baseline tags preserve the source milestones. `legacy-final-2026-09-26` is the earlier system before retirement. `legacy-decision-archive` marks the tree-neutral join that makes every historical branch tip reachable from `main`; its first parent is the operational system, and its other parents retain the earlier branches. No additional public branches are required.

## Privacy and evidence boundaries

Production snapshots, runtime audit logs, private local settings, and reviewer rosters are excluded. Personal names, company examples associated with private reviews, contact details, external identifiers, session links, and coauthor trailers are removed or replaced consistently throughout reachable history. Third-party license attribution is retained.

The 199-row App Store regression fixture uses synthetic seller identities and masked text/script/length signals. Reconstruction checks that all 199 rule verdicts match the source fixture. It tests regression behavior, not public accuracy or investment performance. Historical aggregate observations and design decisions remain documentary evidence; the private records that produced them are not distributed.

Use `main` for installation. Historical commits document the development process and may depend on superseded configuration, removed production inputs, or the environment that existed at the time. Live provider access, scheduled deployment, model judgment quality, and business outcomes require an independently configured environment; offline tests do not establish those outcomes.
