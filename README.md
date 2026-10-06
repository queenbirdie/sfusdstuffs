# SF school tours

A public, auto-refreshing list of every SFUSD elementary and TK-8 tour time.

- `scripts/refresh_tours.py` reads the [SFUSD tours page](https://www.sfusd.edu/schools/enroll/discover/sfusd-school-tours), opens each school's Google Form, and writes `site/data/tours.json` and `tours.csv`.
- Tours that disappear from a form stay in the data, marked **full** with the date they dropped off.
- `.github/workflows/refresh.yml` runs twice a day, commits the data, and publishes `site/` to GitHub Pages.

## Setup (one time)
1. Create a new **public** GitHub repo and upload everything in this folder (keep the `.github` folder).
2. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
3. **Actions tab →** enable workflows if prompted → *Refresh tours and publish site* → **Run workflow**.
4. When it finishes (~2–3 min), your site is live at `https://<username>.github.io/<repo>/`.

## Day to day
- Refreshes run automatically about 6am and 6pm Pacific. Use **Run workflow** for an on-demand refresh.
- If a run fails, the site keeps showing the last good data. Check the run log: forms the script couldn't read are listed at the bottom.
- To change the page, edit `site/index.html`; pushing it redeploys the site.

## Notes
- GitHub pauses scheduled workflows after 60 days with no repo activity. The data commits count as activity, so this shouldn't trigger, but if refreshes stop, re-enable the workflow in the Actions tab.
- Run locally: `pip install -r requirements.txt && python scripts/refresh_tours.py`, then `python -m http.server -d site` and open http://localhost:8000.
