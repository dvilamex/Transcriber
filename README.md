# Transcriber

Transcriber watches a short list of VC, tech, and AI YouTube channels, summarizes new English videos, and writes a daily digest Diego can read in a few minutes.

The digest is organized by theme, not by episode. When two shows make the same point, it is stated once and both shows are credited. A point already covered in the last 7 days is omitted unless a number or conclusion actually changed. After the themes, each video gets a single line: title, link, channel, guests, and only what was unique to that episode.

Per-video notes (TL;DR, takeaways, claims with timestamps, people and companies, why it matters) are stored on the video record in `state/seen.json`. The transcript text is held in memory only long enough to summarize, then discarded. The digest is the thing to read.

## Channels

Feeds were checked against `https://www.youtube.com/feeds/videos.xml?channel_id=<id>`. `@Sequoia` and `@tbpn` are different channels; the ids below are the official ones.

| Channel | Handle | Channel ID | On |
| --- | --- | --- | --- |
| 20VC | `@20VC` | `UCf0PBRjhf0rF8fWBIxTuoWA` | yes |
| Sequoia Capital | `@sequoiacapital` | `UCWrF0oN6unbXrWsTN7RctTw` | yes |
| TBPN | `@TBPNLive` | `UC-DRzaGnL_vtBUpCFH5M0tg` | yes |
| All-In Podcast | `@allin` | `UCESLZhusAkFfsNsApnjF_Cg` | yes |
| AI Engineer | `@aiDotEngineer` | `UCLKPca3kwwd-B59HNr-_lvA` | yes |
| Dwarkesh Patel | `@DwarkeshPatel` | `UCXl4i9dYBrFOabk0xGmbkRA` | yes |
| Lenny's Podcast | `@LennysPodcast` | `UC6t1O76G0jYXOAoYCm153dA` | no |
| Latent Space | `@LatentSpacePod` | `UCxBcwypKK-W3GHd_RZ9FZrQ` | no |
| No Priors | `@NoPriorsPodcast` | `UCSI7h9hydQ40K5MJHnCrQvw` | no |
| Acquired | `@acquiredfm` | `UCyFqFYfTW2VoIQKylJ04Rtw` | no |
| a16z | `@a16z` | `UC9cn0TuPq4dnbTY-CBsm8XA` | no |
| Invest Like the Best | `@ILTB_Podcast` | `UCpQBb0fToph3jrDulwz1iUQ` | no |
| Bg2 Pod | `@Bg2Pod` | `UC-yRDvpR99LUc5l7i7jLzew` | no |

Edit `config.yaml` to enable a suggestion or add another channel. Each entry needs `name`, `channel_id`, and `enabled`.

## Setup

Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

`pip install -r requirements.txt` installs the same runtime packages plus pytest.

Copy nothing for a first look. API keys are optional and are read from the environment, not from the config file.

```bash
export OPENAI_API_KEY=sk-...          # or ANTHROPIC_API_KEY
export LLM_PROVIDER=openai            # openai or anthropic; inferred from whichever key is set
export LLM_MODEL=gpt-4o-mini          # optional; defaults are gpt-4o-mini and claude-3-5-haiku-latest
```

Without a key, `transcriber run` still fetches each transcript, records the video in state, and writes an episode index. It does not save the transcript text, and it does not invent a theme digest.

## Commands

```bash
transcriber init --hours 48
transcriber run --since 72 --limit 10
transcriber run --no-summarize
transcriber video "https://www.youtube.com/watch?v=VIDEO_ID"
```

`init` reads each enabled feed and marks current videos as seen, except those published in the last N hours (default `init_lookback_hours`, 48). The next `run` then picks up only that recent window instead of the whole feed. Shorts are marked skipped. Videos that were already transcribed or summarized are left alone.

`run` is idempotent. A video that was summarized is not fetched again. A video that was fetched but not summarized is fetched again on a later run, because the transcript text is not kept. A failed fetch is retried on later runs, up to 8 attempts or 7 days from the first failure, whichever comes first. `--since` limits the feed to a publish window. `--limit` caps how many videos are processed, newest first, with retries after new uploads. A lock file at `state/transcriber.lock` stops two runs from writing state at the same time.

`video` does one URL (`watch`, `youtu.be`, `shorts`, `embed`, or a raw 11-character id) and appends it to today's digest.

Add `--config path/to/config.yaml` if the file is not in the working directory. Paths in the config are relative to the config file.

## What a run writes

- `digests/YYYY-MM-DD.md` — the theme digest for that America/Mexico_City date. A later run on the same day merges into the file instead of duplicating episodes.
- `state/seen.json` — video id, status, errors, retry counts, and the per-video summary when summarization succeeds. This is what makes reruns safe. The summary is the TL;DR, takeaways, claims, people, and why it matters. The transcript text is not stored.
- `state/covered_topics.json` — topics actually printed in a digest (`topic`, `key_points`, `date`, `sources`, `numbers`). Matching uses the last 7 days (`dedupe_window_days`). Records older than 30 days are dropped.

Transcript text is not written to disk. `transcripts/` is gitignored so a stray local file cannot be committed.

Both JSON files are committed on purpose so GitHub Actions remembers what it has already covered.

### Digest shape

```markdown
# Digest — 2026-11-18

## Inference costs
- Inference prices are falling — 20VC, TBPN
- Update: one lab quoted a 10× drop this quarter (previously 3×) — Dwarkesh Patel

## AGI timelines
- AGI arrives within two years — 20VC
- A government-level system is plausible within twelve months — Dwarkesh Patel

## Episodes
<!-- video:abcdefghijk -->
- **[Cost curve](https://www.youtube.com/watch?v=abcdefghijk)** — 20VC — Jane Doe — Only this guest toured the new cluster.
```

Shared wording becomes one bullet. Different numbers on the same claim stay on one line, credited per show. Genuinely different views stay as separate bullets under the same theme. A repeat of a point from the last 7 days disappears. A new figure is kept as an `Update:` line and the previous figure is named so the delta is obvious.

Theme clustering is lexical (token overlap on the short topic labels and atomic claims the model extracts). The summarizer is told to use reusable labels such as "inference costs" rather than episode titles. Long episodes, including TBPN shows on the order of 35k words, are chunked (about 12k characters, with overlap) and summarized map-reduce so the model sees the whole transcript before those claims are extracted.

## Transcripts

For each video, in order:

1. `youtube-transcript-api`, English tracks only (`en-orig`, then `en`, then other `en-*`). Manual captions are preferred to auto-captions. The library retries blocked requests when a Webshare proxy is configured; otherwise Transcriber retries three times. It never asks YouTube to translate a non-English track.
2. `yt-dlp` English captions, including auto-captions, using the same proxy and an optional cookies file. `en-orig` wins over a dubbed English track.
3. [Supadata](https://supadata.ai/) when `SUPADATA_API_KEY` is set. Default mode is `native` (existing captions only). Set `SUPADATA_MODE=auto` or `generate` if you explicitly want their paid AI transcript.

Any result whose language code is not English, or whose text does not read as English, is rejected. That is what drops TBPN auto-dubs that come back as another language. A failure on one video is stored and the run continues. The next run retries it until the cap above.

Proxy, first match wins:

- `WEBSHARE_PROXY_USERNAME` and `WEBSHARE_PROXY_PASSWORD` — rotating residential proxy via `youtube-transcript-api`'s `WebshareProxyConfig` (`http://user-rotate:pass@p.webshare.io:80`). Optional `WEBSHARE_FILTER_LOCATIONS=us` limits the IP pool. Buy Webshare's **Residential** package, not "Proxy Server" or "Static Residential".
- `YOUTUBE_PROXY` — any HTTP(S) proxy URL, used when Webshare is not set. Example: `http://user:pass@host:port`.

`YOUTUBE_COOKIES_FILE` is a Netscape cookies file passed to yt-dlp and to the transcript API session. On GitHub Actions the same material can be provided as the `YOUTUBE_COOKIES` secret; the workflow writes it to a temp file. Cookies help for a while and can get the Google account banned. Prefer the residential proxy.

Audio download plus a local Whisper model is not wired up. From a datacenter address the download itself is usually bot-checked, and the model is a large extra dependency. Webshare, then Supadata, are the supported ways through that block.

## GitHub Actions

`.github/workflows/transcriber.yml` runs every day, including Saturday and Sunday, at about 07:00 America/Mexico_City. Mexico City is UTC−6 year-round, so the cron is `0 13 * * *` (GitHub cron is UTC and has no timezone field; the day-of-week `*` is what includes weekends). You can also run it by hand with `workflow_dispatch` (`run` or `init`, optional since/hours, limit, and skip-summary).

Scheduled runs process at most 20 videos so one noisy day cannot fill the job. Anything still on the feed and not yet seen is picked up next time. The job commits `digests/` and `state/` back to the branch even when some videos fail, then marks the job red if the run itself failed.

GitHub-hosted runner IPs are datacenter addresses. YouTube often answers those with `RequestBlocked` or a "Sign in to confirm you're not a bot" page, so a run with no proxy can commit nothing but failures. Set the Webshare secrets (or `YOUTUBE_PROXY`) before relying on the schedule.

### Secrets

Repository secrets (Settings → Secrets and variables → Actions → Secrets):

| Secret | Required | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | one of the two LLM keys | OpenAI summaries |
| `ANTHROPIC_API_KEY` | one of the two LLM keys | Anthropic summaries |
| `WEBSHARE_PROXY_USERNAME` | recommended | Residential proxy username |
| `WEBSHARE_PROXY_PASSWORD` | recommended | Residential proxy password |
| `YOUTUBE_PROXY` | optional | Generic proxy URL if you are not using Webshare |
| `YOUTUBE_COOKIES` | optional | Netscape cookie file contents for yt-dlp |
| `SUPADATA_API_KEY` | optional | Paid transcript fallback |

Repository variables (not secrets):

| Variable | Purpose |
| --- | --- |
| `LLM_PROVIDER` | `openai` or `anthropic`. If empty, the key that is set wins. |
| `LLM_MODEL` | Model name. Defaults: `gpt-4o-mini`, `claude-3-5-haiku-latest`. |
| `WEBSHARE_FILTER_LOCATIONS` | Optional comma-separated country codes, for example `us`. |
| `SUPADATA_MODE` | `native` (default), `auto`, or `generate`. |

## Tests

```bash
pytest
```

Covers RSS parsing (including Shorts), seen-state and the retry cap, chunking of a long transcript, English rejection, and theme dedupe against the 7-day covered-topics store. The tests do not call YouTube or an LLM.

## What was verified from this environment

This VM is a datacenter IP.

- All 13 RSS feeds returned HTTP 200 and the channel title in the table above, including Bg2 Pod and Invest Like the Best.
- `youtube-transcript-api` 1.2.5 raised `RequestBlocked` for `9RKrSF9d2SE` (AI Engineer, "2026 in LLMs — Simon Willison") and `Cyt3LU8TyNc`, three attempts each. No English transcript came back.
- `yt-dlp` on `9RKrSF9d2SE` stopped with "Sign in to confirm you're not a bot" before any caption URL was issued. This was a bot check, not an HTTP 429.
- Webshare and Supadata were not called; no credentials were available.
- `transcriber init --hours 72` against the live feeds (temporary state directory) marked 28 videos seen, skipped 51 Shorts, and left 11 recent videos for the next run.
- `transcriber run --limit 1 --no-summarize` selected All-In's `1bR0gtecoHU` ("Databricks CEO: AGI Is Already Here…"). `youtube-transcript-api` returned `RequestBlocked` and yt-dlp returned the bot-check page. The failure was stored with `attempts: 1`, nothing was written under `digests/`, and the process exited 1.
- Offline tests cover RSS parsing, retry state, chunking, English rejection, and theme dedupe. The CLI help text loads.

## What Diego needs to do

1. Merge this pull request. The scheduled workflow only runs from the default branch.
2. Confirm GitHub Actions is enabled for the repository (Settings → Actions).
3. Add an LLM secret if you want summaries. Add Webshare (recommended) or `YOUTUBE_PROXY` or the schedule will keep hitting YouTube's datacenter block.
4. Run the workflow once with command `init` (or `transcriber init` locally) so the first daily run does not treat the whole current feed as new.
5. Optionally set `LLM_PROVIDER` and `LLM_MODEL`, then enable any of the suggested channels in `config.yaml`.
