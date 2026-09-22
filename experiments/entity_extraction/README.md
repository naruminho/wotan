# Entity extraction (working example)

Extracts entities (people, organizations, locations, money) from text/PDF
files into `output/results.csv` using the **gateway_client SDK** and
`extract_json` (JSON-schema validation with automatic repair).

Verified against the bundled mock gateway (`wotan mock`); point it at the real
gateway through `~/.wotan/config.yaml` without any code change.

## Run

```bash
pip install -r requirements.txt
wotan mock &                       # optional fictional gateway on :8787
export GATEWAY_URL=http://127.0.0.1:8787
python run.py --input input        # -> output/results.csv
python webapp.py --port 8899       # optional web UI (upload + result)
```

## Files

- `run.py` - batch extraction to CSV
- `webapp.py` + `static/index.html` - upload UI variant (auto-ports from the
  Experiments panel)
- `input/sample.txt` - example document
- `requirements.txt`, `.env.example`

## How the agent verified this

1. Started the mock gateway and ran `python run.py --input input`.
2. Checked `output/results.csv` contains the expected entities from the sample.
3. Started `webapp.py` and confirmed the page loads and the endpoint returns
   results for an uploaded file.
