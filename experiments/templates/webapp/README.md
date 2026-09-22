# Webapp experiment template

FastAPI + HTML page: upload a file, call the gateway through the
`wotan.gateway_client` SDK, show the result.

## Run

```bash
pip install -r requirements.txt
cp .env.example .env      # then fill in / or rely on ~/.wotan/config.yaml
python webapp.py --port 8899
```

The IDE Experiments panel can start it too (preview proxied at
`/preview/<name>/`).

## Files

- `webapp.py` - server (upload endpoint + static page)
- `static/index.html` - UI
- `requirements.txt`, `.env.example` - deps and environment template
- Logging: stdout + errors surfaced in the API response (never swallowed)
