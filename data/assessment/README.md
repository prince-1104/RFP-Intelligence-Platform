# Assessment bid inputs (local only, not committed)

Copy the supplied assignment ZIP here and extract it so each bid is one folder:

```
data/assessment/
  Bid1/   original PDF/HTML files of the first bid (subfolders allowed)
  Bid2/   original PDF/HTML files of the second bid
```

Never replace missing files with synthetic or downloaded documents. Everything in this folder
except this README is git-ignored. Compose mounts it read-only at `/data/assessment` in the API
container for later, explicitly approved production ingestion.

## Extract the ZIP (PowerShell, repository root)

```powershell
$zip = "C:\path\to\assignment.zip"                 # the ZIP you were given
Expand-Archive -LiteralPath $zip -DestinationPath data\assessment\_extracted -Force
Get-ChildItem data\assessment\_extracted -Recurse -Depth 2 | Select-Object FullName
# Move each bid's folder contents into Bid1 / Bid2 according to the listing above, e.g.:
Move-Item data\assessment\_extracted\<first-bid-folder>\* data\assessment\Bid1\
Move-Item data\assessment\_extracted\<second-bid-folder>\* data\assessment\Bid2\
Remove-Item data\assessment\_extracted -Recurse      # only after both moves are verified
Get-ChildItem data\assessment\Bid1, data\assessment\Bid2 -Recurse -File |
  Select-Object FullName, Length, @{n="SHA256"; e={(Get-FileHash $_.FullName).Hash}}
```

## Parse and chunk locally (no R2, PostgreSQL, Qdrant Cloud or network)

The backend image contains Tesseract for scanned pages. `--network none` makes cloud access
impossible; the input is mounted read-only and outputs go to the git-ignored `artifacts/`.

```powershell
docker compose build api                          # once, if the image is outdated
New-Item -ItemType Directory -Force artifacts\assessment\Bid1 | Out-Null
docker run --rm --network none `
  -v "${PWD}\data\assessment:/data/assessment:ro" `
  -v "${PWD}\artifacts\assessment:/out" `
  rfp-backend:local python -m rfp.cli parse-folder --path /data/assessment/Bid1 --output /out/Bid1/parsed
docker run --rm --network none `
  -v "${PWD}\artifacts\assessment:/out" `
  rfp-backend:local python -m rfp.cli chunk-folder --path /out/Bid1/parsed --output /out/Bid1/chunks
Get-Content artifacts\assessment\Bid1\parsed\summary.json
Get-Content artifacts\assessment\Bid1\chunks\summary.json
```

Host alternative (OCR pages need Tesseract installed on the host; otherwise they are reported
as partial): `.\.venv\Scripts\python -m rfp.cli parse-folder --path data\assessment\Bid1
--output artifacts\assessment\Bid1\parsed` followed by `chunk-folder` with the same paths as above.

Optional local search validation against the local Docker Qdrant only (never Qdrant Cloud):

```powershell
docker compose up -d qdrant
.\.venv\Scripts\python -m rfp.cli index-folder --path artifacts\assessment\Bid1\chunks
.\.venv\Scripts\python -m rfp.cli search --local --query "your question" --top-k 5
```

The bid ID is derived from the folder name (`Bid1`) unless `--bid-id` is given. Do not run
`ingest-folder`, `chunk-bid`, `index-bid` or `POST /api/v1/index` until the production canary is
explicitly approved; those write to R2, PostgreSQL and Qdrant Cloud.
