# Wildlife & Biodiversity Priority Wiki

This project creates a PostgreSQL-backed biodiversity wiki, exports linked Markdown notes for Obsidian Graph View, and provides three independently scoped MCP endpoints for Claude.

## Priority model

Each species is a wiki record with a `curation_score` from 1–100 and a `priority_tier`:

| Priority tier | Score | Meaning | Endpoint access |
| --- | --- | --- | --- |
| Tier 1 | 70–100 | Highest-priority curated records | All-data endpoint only |
| Tier 2 | 40–69 | Medium-priority curated records | All-data and Tier 2 + Tier 3 endpoints |
| Tier 3 | 1–39 | Lowest-priority curated records | All three endpoints |

Private curator notes are always assigned to Tier 3. The initial curation score is deterministically derived from the source conservation-risk score so the sample data has a reproducible assignment; edit `species.curation_score` and `species.priority_tier` to apply editorial judgement later.

## MCP endpoints

Render deploys the same server three times, each with its own API key and hard-coded scope:

| Endpoint | Render service | Visible records |
| --- | --- | --- |
| MCP 1 | `biodiversity-mcp-all` | Tier 1 + Tier 2 + Tier 3 |
| MCP 2 | `biodiversity-mcp-tier23` | Tier 2 + Tier 3 |
| MCP 3 | `biodiversity-mcp-tier3` | Tier 3 only |

The MCP tools are:

- `search_wiki` — searches only records permitted by the endpoint.
- `get_species_wiki` — returns a complete species profile only when the record is in scope; linked interaction partners are filtered too.

## Local database and Obsidian

Set a PostgreSQL connection string in `.env`:

```env
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/biodiversity
```

Then initialize/reset the database, ingest data, and recreate the Obsidian graph:

```powershell
python ingest_kaggle_data.py --dataset-dir ./wildlife_dataset
python export_to_obsidian.py
```

If the Kaggle download is not available, omit `--dataset-dir` and the ingestion script generates a small dummy dataset. Open `obsidian_vault` in Obsidian and select Graph View.

## Deploy on Render

1. Push this repository, including `render.yaml`, to GitHub. Never commit `.env`.
2. In Render select **New → Blueprint** and choose the repository.
3. The Blueprint creates one PostgreSQL database and three web services. Render generates a distinct `MCP_API_KEY` for each one.
4. Populate the Render database by running `python ingest_kaggle_data.py --dataset-dir ./wildlife_dataset` with `DATABASE_URL` set to the Render database’s external connection string. A newly created production database is empty until this step.
5. Verify each `https://SERVICE.onrender.com/healthz` URL, then use `https://SERVICE.onrender.com/sse` as that service’s MCP URL.

The service rejects all MCP requests when its API key is absent or invalid. `/healthz` is intentionally public but returns no wiki data.

## Claude connector configuration

Replace every placeholder with the exact Render service URL and that service’s own generated `MCP_API_KEY`.

```json
{
  "mcpServers": {
    "biodiversity-all": {
      "type": "sse",
      "url": "https://biodiversity-mcp-all.onrender.com/sse",
      "headers": { "x-api-key": "MCP1" }
    },
    "biodiversity-tier23": {
      "type": "sse",
      "url": "https://biodiversity-mcp-tier23.onrender.com/sse",
      "headers": { "x-api-key": "MCP2" }
    },
    "biodiversity-tier3": {
      "type": "sse",
      "url": "https://biodiversity-mcp-tier3.onrender.com/sse",
      "headers": { "x-api-key": "MCP3" }
    }
  }
}
```

Add only the connectors appropriate for the person using Claude. Do not share the all-data key with a Tier-3-only user.

## Verification

```powershell
python -m py_compile server.py ingest_kaggle_data.py export_to_obsidian.py
```

Test the required isolation after deployment:

- MCP 1 can search a Tier 1, Tier 2, and Tier 3 record.
- MCP 2 cannot find a Tier 1 record but can find Tier 2 and Tier 3 records.
- MCP 3 can find only Tier 3 records and its Tier-3 private notes.
