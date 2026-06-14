# NOTES — non-upstream changes on this fork

This document tracks the **divergences between this branch and upstream
`autoclaw-cc/xiaohongshu-skills`**. Anything documented here was applied
manually, has caveats, or may need rework when the upstream landscape
changes.

> 👉 If you're reading this on `flipped0119/xiaohongshu-skills`, the branch
> you want is `feat/headless-cdp-adapter`. The default `main` branch is
> kept in sync with upstream and intentionally carries no custom commits.

## TL;DR — what changed and why

| Commit | Topic | Source | Status |
|--------|-------|--------|--------|
| `185423f` | Headless Chromium CDP adapter (no GUI / no extension) | this branch — original | active |
| `ed0b65d` | Inline risk-control rules into explore + content-ops SKILL.md | upstream PR #89 (open) | active |
| `ed0b65d` | `CommentPicture` dataclass + `Comment.pictures` field | upstream PR #61 (open) | active |
| `ed0b65d` | English README section + bilingual navigation | upstream PR #25 (open, **heavily reworked**) | active |
| `3c69f2f` | `shareUrl` on Feed + `get-share-url` CLI command | upstream PR #66 (open) | active |
| `b25b7cf` | `get-notifications` CLI command + new module | upstream PR #46 (open) | active |

All five upstream PRs were **OPEN** at the time of adoption (not merged into
upstream `main`). Adopting them early gives the headless variant the same
niceties as the desktop variant.

---

## Detailed change log

### `185423f` — headless adapter (this branch's reason for being)

Adds two scripts and minimal `cli.py` changes so the skill runs on a
headless Linux box (no GUI, no Chrome extension). All business modules
under `scripts/xhs/` (feeds, search, login, publish, comment, like, etc.)
are **untouched** — they already use `cdp.Page`, which works the same
whether driven by an extension bridge or a local headless Chromium.

- `scripts/headless_launcher.py` — spawns `/usr/bin/google-chrome-stable`
  with `--headless=new`, `--no-sandbox`, custom UA (no `HeadlessChrome`
  suffix), `--remote-debugging-port=9222`, persistent
  `~/.xhs/chrome-profile/`, and `start_new_session=True` (same approach
  as upstream PR #60).
- `scripts/xhs-headless.py` — thin wrapper that forwards all `cli.py`
  args; mirrors the latest QR PNG to `<skill-root>/.qrcode.png`; supports
  `--shutdown`.
- `scripts/cli.py` — `_ensure_bridge_ready()` is now a one-liner; legacy
  bridge startup removed; `_DummyBrowser` holds a real `cdp.Browser`
  ref; `_connect()` returns a real `cdp.Page`.
- `.gitignore` — skip adapter runtime artifacts.

### `ed0b65d` — community PRs batch 1 (no business-core changes)

Three upstream PRs cherry-picked together; all touch only docs or
`scripts/xhs/types.py`.

#### #89 — Inline risk-control rules into `xhs-explore` and `xhs-content-ops`

- **What**: replaces the "control overall frequency" note with a
  detailed inline risk-control table (search cadence, detail-fetch
  cadence, anti-batch rules). Removes the dependency on a separate
  `xhs-risk-control` skill.
- **Files**: `skills/xhs-explore/SKILL.md`, `skills/xhs-content-ops/SKILL.md`
- **Upstream status**: OPEN at adoption. May eventually be merged with
  different wording — easy to rebase.
- **Risk**: low. Docs only.

#### #61 — `CommentPicture` dataclass + `Comment.pictures`

- **What**: image-based comments (穿搭 / 晒产品) now carry their
  picture data through `get-feed-detail` instead of being silently
  dropped.
- **Files**: `scripts/xhs/types.py`
- **Upstream status**: OPEN at adoption. Pure types.py addition.
- **Risk**: low. Additive; no breaking change.

#### #25 — English README section + bilingual navigation (reworked)

- **What**: adds a Chinese/English section split and an English
  translation of the feature overview, install instructions, etc.
- **Why reworked**: the upstream PR is based on the **pre-#64** README
  (which described a CDP-based engine). Upstream PR #64 rewrote the
  README to describe the Extension Bridge model. The two versions
  conflict on the first 30 lines. The patch is therefore
  **hand-resolved**: the top of the file keeps the upstream #64 text,
  and the English section is added at the bottom of the file (rather
  than inserted mid-document as the original PR did).
- **Files**: `README.md`
- **Upstream status**: OPEN at adoption; likely **stale** — the author
  would need to rebase against #64 for it to apply cleanly upstream.
- **Risk**: low. Docs only.

### `3c69f2f` — community PR #66, shareUrl + `get-share-url`

- **What**: `search-feeds` output now includes `shareUrl` per result;
  adds a new `get-share-url --feed-id <id> --xsec-token <token>`
  command that returns a clickable `https://www.xiaohongshu.com/explore/<id>?xsec_token=...&xsec_source=pc_search` link.
- **Files**: `scripts/cli.py` (manually merged), `scripts/xhs/types.py`,
  `SKILL.md`
- **Upstream status**: OPEN at adoption.
- **Why manually merged**: the upstream PR's `cli.py` hunks targeted
  line numbers from before PR #86 landed. PR #86 (which is what our
  `main` is based on) added 70+ lines to `cli.py`, shifting everything
  below by that amount. We extracted the `SKILL.md` + `types.py`
  hunks (clean apply) and re-placed the two `cli.py` insertion points
  by hand.
- **Risk**: low. New command + new dataclass field; doesn't break
  existing callers.

### `b25b7cf` — community PR #46, `get-notifications`

- **What**: new CLI command `get-notifications --num <n>` that
  scrapes the Vue store on `https://www.xiaohongshu.com/notification`
  and returns the latest N comment / reply / like notifications.
- **Files**: `scripts/xhs/notifications.py` (new module, clean
  apply), `scripts/cli.py` (manually merged)
- **Upstream status**: OPEN at adoption.
- **Why manually merged**: same `cli.py` line-shift issue as #66.
- **Risk**: low. New command, no breakage.

---

## Compatibility & upgrade playbook

When upstream `autoclaw-cc/xiaohongshu-skills` advances, sync this
branch with:

```bash
cd ~/work/xiaohongshu-skills
git fetch upstream                # add upstream remote pointing at autoclaw-cc/xiaohongshu-skills
git checkout main
git merge upstream/main           # fast-forward main to upstream HEAD
git checkout feat/headless-cdp-adapter
git rebase main                   # re-apply our 4 commits on top of new main
```

### Predicted conflict zones on rebase

- **`scripts/cli.py`** — most likely to conflict. The headless adapter
  rewrote `_DummyBrowser` and `_ensure_bridge_ready`; the community
  PRs (and any future upstream cli.py changes) sit in the same file.
  Resolution: re-do the headless launcher wiring, then re-insert
  community PR hunks (keep `cmd_get_share_url`, `cmd_get_notifications`
  and their subparsers; remove the legacy bridge startup code).
- **`scripts/xhs/types.py`** — additive changes from #61 (CommentPicture)
  and #66 (Feed.share_url). Conflicts unlikely; if they happen, both
  sides are additive and a manual merge is straightforward.
- **`SKILL.md`, `README.md`, `skills/*/SKILL.md`** — text conflicts
  are easy to resolve: keep our version for English/bilingual sections,
  keep upstream's Chinese updates.
- **`.gitignore`** — append-only on both sides, no real conflict.

### What we explicitly do NOT pick up

To keep this branch rebaseable, we **avoid** PRs that modify
business-core files:

- `scripts/xhs/cdp.py` — touched by #58 (WSL path translation),
  #62 (Linux editor detection). Both are nice but the CDP path is
  shared with all other modules; changes here mean a 2-way rebase
  every time, not a 3-way.
- `scripts/xhs/publish.py` — touched by #63, #75, #76.
- `scripts/xhs/login.py` / `search.py` / `feeds.py` — touched by
  #65, #79.
- `scripts/xhs/bridge.py` — the Extension Bridge mode is
  intentionally **not** supported by this branch.

We will reconsider if upstream merges a critical fix into one of
these files; in that case the next rebase will need careful handling.

---

## How to run

```bash
# Login (one-time)
cd ~/.hermes/skills/xiaohongshu-skills
.venv/bin/python scripts/xhs-headless.py check-login
# → scan the QR at .qrcode.png with the 小红书 app

# Use any upstream command through the headless wrapper
.venv/bin/python scripts/xhs-headless.py search-feeds --keyword "..."
.venv/bin/python scripts/xhs-headless.py list-feeds
.venv/bin/python scripts/xhs-headless.py get-feed-detail --feed-id <id> --xsec-token <tok>
.venv/bin/python scripts/xhs-headless.py get-share-url  --feed-id <id> --xsec-token <tok>   # adopted from #66
.venv/bin/python scripts/xhs-headless.py get-notifications --num 10                       # adopted from #46

# Release the headless browser when done
.venv/bin/python scripts/xhs-headless.py --shutdown
```

---

## Upstream PR #92 status

A separate PR was opened against upstream
(`autoclaw-cc/xiaohongshu-skills#92`) proposing the headless adapter.
It is **not** a substitute for this fork branch — even if upstream
accepts the headless adapter idea in the future, this branch will
remain useful for tracking community PRs (#46, #61, #66, #89, #25)
that haven't been merged yet.

See `https://github.com/autoclaw-cc/xiaohongshu-skills/pull/92` for
review status.
