# Batch experiment template

Process a folder of files (text/PDF) through the configured gateway and write
`output/results.csv` (open in Excel directly).

```bash
pip install -r requirements.txt
python run.py --input input --output output/results.csv
```

- Errors per file are recorded in the CSV and in `output/run.log`, never lost.
- Structured output goes through `extract_json` (schema validation + repair).
