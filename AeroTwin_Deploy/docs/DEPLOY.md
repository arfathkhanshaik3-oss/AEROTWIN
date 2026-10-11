# Put AeroTwin online (shareable link)

AeroTwin needs a Python server, so it cannot run on Netlify/GitHub Pages (static-only hosts).
Use any Docker-capable host. Easiest, free, no GitHub needed: **Hugging Face Spaces**.

## Hugging Face Spaces
1. Make a free account at huggingface.co -> **New Space**.
2. Name `aerotwin`, SDK **Docker -> Blank**, hardware **CPU basic (free)**, **Public** -> Create.
3. **Files -> Add file -> Upload files**: drag in EVERYTHING from the extracted deploy folder (backend, frontend, models, docs, Dockerfile, README.md, requirements-deploy.txt, run.py ...). Commit.
4. Wait 3-5 min (Building -> Running). Your link: `https://<your-username>-aerotwin.hf.space`

## Render.com (needs the folder in a GitHub repo)
New -> Blueprint (reads render.yaml) -> pick the repo -> deploy. Link: `https://aerotwin.onrender.com`.

## Notes
* Free hosts sleep when idle: first open after a break can take 30-60 s.
* The public link runs in **Simulator** mode (no drone attached). Anyone with the link can use the test buttons.
* To receive real drone data on a hosted server, set the secret `AEROTWIN_API_KEY` (Space -> Settings -> Variables and secrets) and send it as `X-API-Key`.
* Data written to the SQLite log is wiped when a free host restarts.
