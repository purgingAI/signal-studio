# Signal Studio — a LinkedIn agent that turns today's tech news into *your* story

Every morning it reads Hacker News and the AI/tech press, pairs the stories with things **you actually built**,
pitches post ideas, and drafts posts in your voice. **Nothing is posted until you approve it.** It also drafts your
LinkedIn **About** section and headline.

```
News feeds + HN ──► Claude (ideas: news × your projects) ──► Drafts ──► You: edit / rewrite with feedback / approve
                                                                       └─► Copy + open LinkedIn composer
```

- **Studio**: news radar, idea cards (project, format, editorial fit, hook, why now), and an editor with a live
  LinkedIn preview including the "…see more" fold
- **Queue**: pending → approved → posted (or rejected)
- **About section**: three variants (builder story, recruiter-ready, short) plus headlines
- **Profile**: `app/profile.json`, the agent's only source of truth about you. It never invents results; unknown details
  come back as `[ADD: …]` placeholders.
- **Daily run** at 09:00 local time auto-drafts the 2 best ideas (`STUDIO_DAILY_HOUR`, `STUDIO_AUTO_DRAFTS`)

## Run
```bash
# needs the Claude Code CLI logged in (`claude`), no API key
./run.sh                # http://localhost:8099
```
Built with FastAPI, SQLite and vanilla JS. Claude runs through the Claude Code CLI (`claude -p --json-schema`), so it uses your Claude subscription instead of an API key.

## LinkedIn connection
LinkedIn doesn't let personal apps read your About/experience through its API, so Signal Studio uses the two
sanctioned routes:
- **Profile**: drop your LinkedIn **data export** zip (Settings → Data privacy → Get a copy of your data) on the
  About tab. It reads your headline, About, positions, skills, education and recent posts (to match your voice).
- **Posting**: *Connect LinkedIn* (OAuth, `openid profile email w_member_social`) publishes **approved** drafts and
  adds the first comment. Create an app at linkedin.com/developers, add the products *Sign In with LinkedIn using
  OpenID Connect* and *Share on LinkedIn*, register `PUBLIC_URL/auth/linkedin/callback`, then set
  `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET` and `PUBLIC_URL`.

## Deploy
`./deploy.sh` builds a Docker image (Python + Claude Code CLI) and runs it on a server behind Caddy. Set
`CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`) in the server's env file. Put the dashboard behind auth:
set `STUDIO_PASSWORD` (login page) because it can spend your Claude usage and post to your LinkedIn.
