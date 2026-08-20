# claude-ipixel

Your remaining Claude quota, live on an iPixel Color LED matrix.

![the panel in place](doc/banner.jpg)

Two rows: how much of the rolling **5-hour session** window you have left, and how much of the rolling **7-day** window. When either runs out, that row switches to a countdown until it refills.

---

## Contents

- [What you're looking at](#what-youre-looking-at)
- [The views](#the-views)
- [Today's git activity](#todays-git-activity)
  - [Where the repository list lives](#where-the-repository-list-lives)
- [Your gitranks standing](#your-gitranks-standing)
  - [Where the GitHub login lives](#where-the-github-login-lives)
- [Google Scholar citations](#google-scholar-citations)
- [Requirements](#requirements)
- [Install](#install)
- [Usage](#usage)
- [Running it as a service](#running-it-as-a-service)
- [How authorization works](#how-authorization-works)
- [How it works](#how-it-works)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)

---

## What you're looking at

![panel states](doc/states.png)

Every view the panel can draw, at 64×20. The quota views are the default; the rest are described under [Today's git activity](#todays-git-activity), [Your gitranks standing](#your-gitranks-standing) and [Google Scholar citations](#google-scholar-citations).

Each row is `label · bar · number`. Both the bar and the number show what is **remaining**, so the bar drains as you work.

A red arrow sits above each bar, marking where that bar would stand if the window's quota were spent evenly — the share of the rolling window still to run, so the arrow walks left as the window burns down and snaps back to the right when it resets. Bar filled past the arrow means you are ahead of an even spend; bar short of it means you are burning faster than the clock. The top arrow paces the 5-hour session against 5 hours, the bottom one the week against 7 equal days.

The 7-day row carries a second, yellow arrow that paces the week against *working* hours only — Mon-Fri, 09:00-17:00 local, 40 hours a week rather than 168. It marks where the week bar would stand if the quota were spent evenly across the working time still left in the window, so it sits still overnight and over the weekend and only walks left while you are at the desk. Edit `WORK_DAYS` and `WORK_HOURS` in `display.py` if your week looks different.

The arrows shrink to whatever fits above their bar: a full arrow where there is room, a stub where there is less, a 3-pixel tick on the 16-pixel-high panels. Panels too narrow for a bar have no arrows at all.

| Remaining | Colour |
| --- | --- |
| 50–100% | green |
| 20–50% | amber |
| 0–20% | orange |
| spent | red, bar empty |

When a window is spent, the number becomes the time until it resets:

| Wait | Shown as |
| --- | --- |
| under 24 hours | `2:22` |
| 24 hours or more | `66H` |

The weekly window can be up to 168 hours out. Long waits use an hours suffix rather than days because a 3-pixel-wide `D` is nearly indistinguishable from `0` — `2D18` reads as `2018`.

Two full-panel messages replace the bars on failure:

| Message | Meaning |
| --- | --- |
| `AUTH` | Stored credentials are missing, malformed, or rejected |
| `ERR` | Three consecutive failures reaching the usage endpoint |

A single network blip holds the last good frame rather than blanking the panel; it takes three in a row to show `ERR`.

On Ctrl+C or `systemctl stop`, the panel is switched off in software before the process exits, so it never keeps showing figures from a service that is no longer running. If the panel has stopped answering, the attempt is given five seconds and then abandoned.

## The views

Eight views, from three sources. Most are `label · bar · number` rows; `net`, `tier` and `cites` give one figure the whole panel. Pick one with `--view`, or rotate through several:

| `--view` | Source | Shows |
| --- | --- | --- |
| `quota` | usage endpoint | The two quota bars. The default, and what the panel has always drawn |
| `net` | [git](#todays-git-activity) | Today's net line count across every repository |
| `churn` | [git](#todays-git-activity) | Lines added over lines removed, splitting one total |
| `repos` | [git](#todays-git-activity) | A row per repository — initials, share of the day, net lines |
| `commits` | [git](#todays-git-activity) | The same rows, counted in commits |
| `rank` | [gitranks](#your-gitranks-standing) | Stars, contributions and followers — each bar how far up the board you are |
| `tier` | [gitranks](#your-gitranks-standing) | The tier gitranks leads your profile with |
| `cites` | [Scholar](#google-scholar-citations) | Your total citation count |
| `all` | | Rotate through every view above |

`--view` also takes a comma-separated selection, so you can rotate through just the ones you care about:

```bash
./venv/bin/python service.py --view quota,net,cites --rotate 20
```

Each source refreshes on **its own schedule**, not once per frame, so rotating costs nothing extra — see [What it costs](#what-it-costs). A view whose source is not configured, or has not answered yet, simply holds the previous frame rather than blanking the panel.

## Today's git activity

Alongside the quota there are four views of how much code you have actually written today, counted straight out of a list of git repositories you keep in your config directory. See [Where the repository list lives](#where-the-repository-list-lives) for how to create it.

Each repository is asked for its commits since **local midnight** on the current `HEAD`, merges excluded:

| `--view` | Shows |
| --- | --- |
| `net` | Today's net line count across every repository, as large as the panel will take it — green when you are adding, orange when you are deleting |
| `churn` | Two rows, `+` added over `-` removed. The bars split one total, so a day spent deleting reads at a glance |
| `repos` | A row per repository — initials, share of the day's churn, and its net lines |
| `commits` | A row per repository — initials, share of the day's commits, and the count |

Repositories are sorted busiest-first and only as many rows as the panel can hold are drawn, so on a 16-pixel panel you see the top two. Each bar is that repository's share of the *leader* rather than of the total, which stops a lone active repository from drawing a full bar beside three empty ones. Labels are the initials of an underscore- or dash-separated name — `magician_vision_classifier` becomes `MVC` — or the first three letters of a single-word name.

Line counts are capped at four characters: `9999`, then `10K`, then `1M`. Binary files report no line counts to git and are skipped rather than counted as zero.

### Where the repository list lives

The list is **machine-specific** — absolute paths that mean nothing on another host — so the checkout deliberately does not carry one. Your list goes in your config directory instead:

```bash
mkdir -p ~/.config/claude-ipixel
cp github-repos.txt.example ~/.config/claude-ipixel/repos.txt
$EDITOR ~/.config/claude-ipixel/repos.txt
```

One absolute path per line. Blank lines and anything after a `#` are ignored, `~` is expanded, and anything that is not a git repository is skipped with a warning:

```
# things I am actually working on
~/Programming/project-one
~/Programming/project-two   # comments can trail a path too
```

Three locations are consulted, first hit wins:

| Order | Location | For |
| --- | --- | --- |
| 1 | `--repos PATH` | A one-off, or a second list for a second panel |
| 2 | `$XDG_CONFIG_HOME/claude-ipixel/repos.txt`, i.e. `~/.config/claude-ipixel/repos.txt` | **The normal place.** Outside the checkout, so `git pull`, moving the clone, or reinstalling never touches it |
| 3 | `github-repos.txt` beside the code | Fallback for running straight out of a clone. `.gitignore`d, so a machine-specific list can never follow you into a commit |

Whichever it picks is logged at startup, so there is never any doubt:

```
INFO watching 18 repositories from /home/you/.config/claude-ipixel/repos.txt
```

If none of them exists, the git views draw `NONE` and the log tells you exactly what to run:

```
WARNING no repository list at /home/you/.config/claude-ipixel/repos.txt (No such file or directory)
WARNING create one: mkdir -p /home/you/.config/claude-ipixel && cp .../github-repos.txt.example /home/you/.config/claude-ipixel/repos.txt
```

**Moving an existing list into place** — if you already have a `github-repos.txt` in your checkout:

```bash
mkdir -p ~/.config/claude-ipixel
mv github-repos.txt ~/.config/claude-ipixel/repos.txt
```

Nothing else changes: the paths inside it are already absolute, and the service picks the new location up on its next start.

### What it costs

Rotating every 15 seconds does **not** mean scanning every 15 seconds. Both sources are polled on their own schedule and cached, so a tick that finds them warm draws entirely from memory:

- **The usage endpoint** is called at most once per `--interval`, no matter how often the frame is redrawn — and not at all if no chosen view is `quota`.
- **Each repository** gets at most one `git log` per `--git-interval`, and even that is skipped while the repository has not moved. A commit rewrites `.git/logs/HEAD`, so its mtime plus the current date is enough to serve the previous answer from cache. An idle set of repositories costs a `stat` per repository, not a process.
- **gitranks and Google Scholar** are somebody else's servers, so each is asked **once a day at most** and the answer is kept in `~/.cache/claude-ipixel/`. Neither is touched at all unless one of its views is on screen and its config file exists. A failure backs off for an hour rather than retrying on the next tick, and both refresh on a background thread — a page load that takes a minute never holds up a redraw, the panel simply keeps yesterday's numbers until the new ones land.

In practice a full hour of rotating through every view across three repositories spawns **three** `git` processes — one per repository, on the first tick — and none after that until you commit something.

## Your gitranks standing

[gitranks.com](https://gitranks.com/) ranks GitHub profiles by stars, by stars on repositories you have merged PRs into, and by followers. Two views put that on the panel:

| `--view` | Shows |
| --- | --- |
| `rank` | Three rows — `STARS`, `CONTR`, `FOLLW`. The value is the "top N%" the site gives you; the bar is the share of ranked profiles you are above, so a full bar is the top of the board rather than the bottom. The panel font has no lower case, hence the capitals |
| `tier` | The tier the profile page leads with — `ELITE 1`, `MASTER 5` — as large as the panel will take it |

Bars are green in the top 10%, amber to the halfway mark, orange below it. Everything the page states is parsed, not just what fits on the panel — position, how many are ranked, the percentile, the month's movement, the score, the tier and how far the next one is — and the whole lot goes to the status line and the journal:

```
gitranks Elite 1  S #47,939 top 3%  C #1,357,286 top 50%  F #63,602 top 3%
```

Run the module on its own to see all of it:

```bash
./venv/bin/python gitranks.py
```

```
AmmarkoV: Global Rank Elite 1  (Influencer)
Stars Rank        Master 5   #   47,939 / 1,600,000  top 3%  month +1,940  score 843  (2 to Elite 1)
Contributor Rank  Adept 2    #1,357,286 / 3,200,000  top 50%  month -12,435  score 1,705  (472 to Adept 3)
Followers Rank    Elite 1    #   63,602 / 2,400,000  top 3%  month +584  score 189  (2 to Elite 2)
```

**Why this one needs a browser.** gitranks sits behind a Cloudflare managed challenge: every HTML and `/api/` path answers `403` to any plain HTTP client, whatever headers it sends. So the page is rendered in a **headless Firefox**, driven through `geckodriver` over its plain HTTP interface — no Selenium, no extra Python dependency. Firefox keeps its own profile in `~/.cache/claude-ipixel/gitranks-profile` (or under `~/snap/firefox/common/` for a snap build, which cannot write anywhere else), so the clearance cookie survives between runs and one page load a day is genuinely all it takes. Their `robots.txt` allows `/profile/` at a ten-second crawl delay; once every 86,400 seconds is well inside that.

If `geckodriver` or Firefox is missing the views stay empty and the log says so — nothing else is affected.

### Where the GitHub login lives

One line, in your config directory:

```bash
mkdir -p ~/.config/claude-ipixel
echo AmmarkoV > ~/.config/claude-ipixel/github-user.txt
```

Three locations are consulted, first hit wins — the same order as the repository list:

| Order | Location | For |
| --- | --- | --- |
| 1 | `--github-user PATH` | A one-off, or a second profile for a second panel |
| 2 | `~/.config/claude-ipixel/github-user.txt` | **The normal place** |
| 3 | `github-user.txt` beside the code | Fallback for running out of a clone. `.gitignore`d |

**With no such file the feature is simply off**: no browser is started, no request is made, and the `rank` and `tier` views draw nothing while the log tells you what is missing. `github-user.txt.example` has the same note in it.

## Google Scholar citations

If you publish, one more view:

| `--view` | Shows |
| --- | --- |
| `cites` | Total citations, as large as the space beside the quote mark allows |

A closing quote mark sits in the top-left corner, dimmed towards the number's own colour — seven pixels in a corner read as a stuck pixel unless they clearly belong to something. It is there because a bare figure on a panel could be anything; the mark says the number is a citation count. The mark is sized off the panel rather than off the number, so it grows on a larger display without ever squeezing the figure.

A second view, `hindex` — h-index over i10-index, each bar the share of that index earned inside Scholar's recent window — is written but **off the rotation**, since it did not earn a slot. Put `"hindex"` back into `SCHOLAR_VIEWS` in `display.py` to bring it back; the parse always collects the numbers either way.

Configuration is one line, and takes either the profile id or the URL you have in the address bar:

```bash
mkdir -p ~/.config/claude-ipixel
echo 'https://scholar.google.gr/citations?user=sDOdhtwAAAAJ&hl=en' > ~/.config/claude-ipixel/google-scholar.txt
```

`--scholar PATH` overrides it, `google-scholar.txt` beside the code is the fallback, and with none of them the views are off and nothing is fetched. Unlike gitranks this needs no browser — Scholar serves the page to a plain HTTPS request. The module runs on its own too:

```bash
./venv/bin/python scholar.py
```

```
Ammar Qammaz (sDOdhtwAAAAJ)
                 All  Since 2021
citations        704         433
h-index           11           9
i10-index         13           9
```

## Requirements

- **Firefox and `geckodriver`** — *optional*, and only for the [gitranks](#your-gitranks-standing) views. Everything else, Google Scholar included, runs without them.
- **An iPixel Color LED matrix.** Developed against a 64×20 panel (device type 5). All twenty sizes that `pypixelcolor` knows about are supported — from 32×16 up to 448×32, plus the square 64×64 — and the layout adapts to whichever one you have. I personally use [this one](https://share.temu.com/8lxqc2v6BUB)
- **Bluetooth LE** on the host.
- **Python 3.10+** (the code uses `X | None` annotations).
- **A Claude subscription logged in through [Claude Code](https://claude.com/claude-code).** See [How authorization works](#how-authorization-works) — an `ANTHROPIC_API_KEY` will *not* work.

## Install

```bash
git clone https://github.com/<you>/claude-ipixel
cd claude-ipixel
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

`pypixelcolor` is the only direct dependency; it pulls in `bleak`, `pillow`, `crccheck` and `websockets`.

If you want the [git activity views](#todays-git-activity), create your repository list too — this is the one piece of per-machine setup, and it lives outside the checkout so it survives everything you do to the clone:

```bash
mkdir -p ~/.config/claude-ipixel
cp github-repos.txt.example ~/.config/claude-ipixel/repos.txt
$EDITOR ~/.config/claude-ipixel/repos.txt
```

Skip it if you only want the quota bars; nothing else depends on it.

Check it renders before you involve any hardware:

```bash
./venv/bin/python service.py --preview /tmp/frame.png
```

That draws your real current usage to a PNG. If it produces a frame with two bars, both the credentials and the endpoint are working, and anything that goes wrong from here is Bluetooth.

## Usage

```bash
./venv/bin/python service.py
```

With no arguments it scans for your panel, connects, and redraws every 60 seconds.

`claude-ipixel.sh` is a thin wrapper that saves typing the virtualenv path. It forwards everything you give it straight through to `service.py`, so the two are interchangeable:

```bash
./claude-ipixel.sh --view all --interval 420
```

| Flag | Default | What |
| --- | --- | --- |
| `--address AA:BB:..` | scan | Panel's BLE address; skips the scan |
| `--interval N` | `60` | Seconds between polls |
| `--once` | | Draw a single frame and exit |
| `--preview PATH` | | Render to a PNG instead of the panel — no Bluetooth needed |
| `--size WxH` | `64x20` | Panel size for `--preview` only; read from the device otherwise |
| `--view NAME` | `quota` | What to draw — see [Today's git activity](#todays-git-activity), [gitranks](#your-gitranks-standing) and [Scholar](#google-scholar-citations). Takes a comma-separated list, or `all` |
| `--rotate N` | `15` | Seconds per view, when more than one is selected |
| `--repos PATH` | `~/.config/claude-ipixel/repos.txt` | Repository list to count today's work in; see [above](#where-the-repository-list-lives) for the full search order |
| `--github-user PATH` | `~/.config/claude-ipixel/github-user.txt` | GitHub login to rank on gitranks; see [above](#where-the-github-login-lives) |
| `--scholar PATH` | `~/.config/claude-ipixel/google-scholar.txt` | Google Scholar profile id or URL; see [above](#google-scholar-citations) |
| `--git-interval N` | `300` | Seconds between repository rescans |

### Finding your panel

You do not normally need to. Panels advertise as `LED_BLE_<last 4 bytes of the MAC>`, so the service scans for 8 seconds, picks the `LED_BLE_*` device with the strongest signal, and logs what it chose:

```
INFO scanning 8s for LED_BLE_* panels
INFO found LED_BLE_0C7E39FB at 5B:18:0C:7E:39:FB (-63 dBm)
INFO connected to 5B:18:0C:7E:39:FB (64x20)
```

If several panels are in range it uses the closest and says so. Pass `--address` to pin a specific one — but note these panels use a **random** BLE address that changes when the device power-cycles, so a pinned address can go stale. The scan is rerun on every reconnect precisely so that recovers on its own.

To see what is advertising nearby:

```bash
./venv/bin/python -m pypixelcolor --scan
```

### Previewing other panel sizes

`--size` only affects `--preview`; on real hardware the dimensions are read from the device itself.

```bash
./venv/bin/python service.py --preview /tmp/big.png --size 128x32
./venv/bin/python service.py --preview /tmp/small.png --size 32x16
```

The layout picks the largest whole-pixel font scale the panel's height and width allow, then trades scale back down if that is what it takes to keep a readable bar. Panels too narrow for a bar at any scale (32 pixels wide) show the numbers alone.

## Running it as a service

`claude-ipixel.service` is a systemd **user** unit — user, not system, because it needs to read *your* `~/.claude/.credentials.json`.

Edit the two absolute paths in it to match your checkout, then:

```bash
ln -s "$PWD/claude-ipixel.service" ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now claude-ipixel
```

Watch it:

```bash
systemctl --user status claude-ipixel
journalctl --user -u claude-ipixel -f
```

The unit needs no repository configuration of its own: the list is found at `~/.config/claude-ipixel/repos.txt` for the user the unit runs as, which is the same user whose credentials it already reads. Add `--view` to the unit's `ExecStart` if you want something other than the quota bars.

To keep the panel running while you are logged out:

```bash
loginctl enable-linger $USER
```

It restarts automatically 15 seconds after any crash, and recovers from Bluetooth dropouts on its own without needing a restart.

## How authorization works

This is the part worth understanding before you run it.

### What it reads

Claude Code stores an OAuth credential at `~/.claude/.credentials.json` when you log in with your Claude subscription. This service reads the `claudeAiOauth.accessToken` field out of that file and sends it as a bearer token. It needs no login of its own, no config, and no secrets in the repo — if `claude` works on this machine, this works.

### Why an API key won't do

The usage endpoint is OAuth-only. It reports quota against your *subscription* (Pro, Max, Team), and API keys bill per token rather than drawing on those windows, so there is nothing for it to report. Setting `ANTHROPIC_API_KEY` has no effect here.

### What it deliberately does not do

**The credentials file is read fresh on every poll and never written to.**

That access token expires roughly every 8 hours. Claude Code refreshes it itself, using a lock-and-compare-and-swap protocol (`~/.claude/.oauth_refresh.lock`) so that its own concurrent sessions don't clobber each other. This service could join that protocol — but a bug in a hobby project that races the refresh could rotate the token out from under Claude Code and log you out of your editor.

So it doesn't. It takes whatever is on disk and reports honestly when that is stale.

**The practical consequence:** if you don't run `claude` for longer than the token's lifetime, it expires, the endpoint returns 401, and the panel shows `AUTH` until you next use Claude Code — which refreshes the token as a side effect and brings the panel back within one poll. In day-to-day use, where the panel exists precisely because you *are* using Claude, this rarely comes up.

That is the deliberate trade: a visible, self-healing error over any chance of breaking your login.

## How it works

`usage.py` reads the token and calls:

```http
GET https://api.anthropic.com/api/oauth/usage
Authorization: Bearer <token>
anthropic-beta: oauth-2025-04-20
```

The response carries `five_hour` and `seven_day` objects, each with a `utilization` percentage and a `resets_at` timestamp. That is the entire data source — two numbers and two timestamps.

`repos.py` reads the repository list and shells out to `git log --since=midnight --no-merges --numstat` once per repository, behind the mtime cache described above. It is runnable on its own if you just want the numbers:

```bash
./venv/bin/python repos.py
```

```
MVC    5c  +5872   -99      magician_vision_classifier
MGA    1c  +124    -37      magician_grabber_annotator
MMB    0c  +0      -0       magician_main_board
ALL    6c  +5996   -136     net +5860
```

`gitranks.py` and `scholar.py` are the two daily scrapes. `gitranks.py` starts `geckodriver`, opens a headless Firefox on your profile page, polls the DOM until the real page replaces the Cloudflare challenge, and reads the three rank cards out of the rendered text. `scholar.py` needs none of that — one `urllib` request, and the summary table is parsed straight out of the HTML. Both hand their scheduling to `daily.py`, which owns the once-a-day rule, the JSON cache under `~/.cache/claude-ipixel/`, the hour-long backoff after a failure, and the background thread that keeps a slow page load off the redraw path.

`display.py` renders an RGB image at the panel's exact dimensions using a hand-rolled 3×5 bitmap font. PIL's bundled fonts start around 11 pixels tall, which does not fit a 16-pixel panel split into two rows, so the glyphs are defined as bitmaps and scaled by whole pixels.

`service.py` polls, renders, and pushes the frame as a PNG over BLE via `pypixelcolor`. **The panel is only redrawn when the rendered frame actually changes**, so an idle display generates no Bluetooth traffic at all.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| Panel shows `AUTH` | Token expired or missing. Run any `claude` command to refresh it, or `claude auth` if you were never logged in. |
| Panel shows `ERR` | Three consecutive failures reaching the endpoint. Check connectivity; it clears itself on the next success. |
| `no LED_BLE_* panel in range -- pass --address` | Panel is off, out of range, or still connected to another client — the phone app holds an exclusive connection, so close it. |
| `--preview` works but the panel never updates | The problem is Bluetooth, not credentials. Confirm with `./venv/bin/python -m pypixelcolor --scan`. |
| Repeated `device error, retrying` | Usually BLE congestion. It reconnects every 15s by itself; `systemctl --user restart bluetooth` if it persists. |
| Service dies immediately under systemd | Almost always the two absolute paths in the unit file still pointing at the wrong directory. |

## Limitations

- The usage endpoint is undocumented and unversioned. It may change or disappear without warning.
- Only the 5-hour and 7-day windows are drawn. The response also carries per-model and spend fields, which are ignored.
- Git counts are **committed** work only — nothing uncommitted in the working tree is counted, so the figures step up when you commit rather than while you type.
- Every commit on `HEAD` since midnight is counted, whoever wrote it. In a repository you share, a colleague's merge-free commits land in your totals.
- Lines are a famously poor proxy for work. A generated file or a vendored dependency will dwarf a day of real thinking.
- The gitranks and Scholar figures are scraped from pages meant for human eyes. A redesign at either end breaks the parse — the views go empty and the log says why, but nothing else stops.
- gitranks needs a real browser on the machine. Without Firefox and `geckodriver` those two views never fill in.
- Bluetooth LE only. Panels with WiFi firmware are not addressed over the network.
- Not affiliated with, endorsed by, or supported by Anthropic.

## License

GPL-3.0 — see [LICENSE](LICENSE).

Built on [pypixelcolor](https://github.com/lucagoc/pypixelcolor) by Lucas Balmès, which does all the actual talking to the hardware.
