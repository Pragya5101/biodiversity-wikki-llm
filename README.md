# Wildlife & Biodiversity Priority Wiki

This project creates a PostgreSQL-backed biodiversity wiki, exports linked Markdown notes for Obsidian Graph View, and provides three independently scoped MCP endpoints for Claude.

## Priority model

Each species is a wiki record with a `curation_score` from 1–100 and a `priority_tier`:

| Priority tier | Score | Meaning | Endpoint access |
| --- | --- | --- | --- |
| Tier 1 | 70–100 | Highest-priority curated records | All-data endpoint only |
| Tier 2 | 40–69 | Medium-priority curated records | All-data and Tier 2 + Tier 3 endpoints |
| Tier 3 | 1–39 | Lowest-priority curated records | All three endpoints |

Private curator notes are always assigned to Tier 3. The curation score is deterministically derived from each species' real IUCN-style conservation status (Extinct/Extinct-in-the-wild/Critically-endangered → Tier 1; Endangered/Vulnerable/Near-threatened → Tier 2; Least-concern/Data-deficient/unassessed → Tier 3), so the sample data has a reproducible, meaningful assignment; edit `species.curation_score` and `species.priority_tier` to apply editorial judgement later.

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
python ingest_animals_data.py --csv-path "C:\path\to\animals_info.csv"
python export_to_obsidian.py
```

`ingest_animals_data.py` expects a species reference CSV shaped like the public "Animals" datasets found on Kaggle (columns: `Name, Kingdom, Phylum, ..., Class, ..., Weight, ..., Diet, ..., Population`, where `Population` is a stringified dict containing a `Population status` IUCN category). It filters out family/genus-level placeholder rows, then samples roughly 350 species: every Extinct-in-the-wild record, a capped sample of Extinct and Critically Endangered records, and a smaller breadth sample across the remaining statuses — pass `--seed` to change the sample deterministically. The CSV has no GPS/timestamp occurrence data, so Tier 1 "sightings" are lightly synthesized from each species' real continent of distribution; ecological interactions are inferred from each species' real `Diet` text, constrained to the same taxonomic class, continent, and a rough weight-based size check so links stay plausible.

Open `obsidian_vault` in Obsidian and select Graph View. `export_to_obsidian.py` clears previously generated notes before writing, so it always reflects only what's currently in the database.

## Deploy on Render

1. Push this repository, including `render.yaml`, to GitHub. Never commit `.env`.
2. In Render select **New → Blueprint** and choose the repository.
3. The Blueprint creates one PostgreSQL database and three web services. Render generates a distinct `MCP_API_KEY` for each one.
4. Populate the Render database by running `python ingest_animals_data.py --csv-path "C:\path\to\animals_info.csv"` with `DATABASE_URL` set to the Render database’s external connection string. A newly created production database is empty until this step.
5. Verify each `https://SERVICE.onrender.com/healthz` URL, then use `https://SERVICE.onrender.com/mcp` as that service’s MCP URL.

The service rejects all MCP requests when its API key is absent or invalid. `/healthz` is intentionally public but returns no wiki data.

The server uses the Streamable HTTP transport, mounted at `/mcp` (not `/sse`). This matters for auth: every request goes to that one fixed URL directly, with no server-generated redirect in between, so a query-string API key stays attached to every call — a header-only, session-redirecting transport would silently drop it after the first request.

## Claude connector configuration

**If your Claude client's "Add connector" UI only asks for a Name and a URL** (no custom headers field — this is the case for the Connectors screen in the Claude app's Settings), pass the API key as a query parameter directly in the URL:

| Name | Remote MCP server URL |
| --- | --- |
| biodiversity-all | `https://biodiversity-mcp-all.onrender.com/mcp?api_key=MCP1` |
| biodiversity-tier23 | `https://biodiversity-mcp-tier23.onrender.com/mcp?api_key=MCP2` |
| biodiversity-tier3 | `https://biodiversity-mcp-tier3.onrender.com/mcp?api_key=MCP3` |

Replace `MCP1`/`MCP2`/`MCP3` with each service's own generated `MCP_API_KEY` value from its Render Environment tab.

**If your Claude client instead reads a local `mcpServers` config file** (classic Claude Desktop's `claude_desktop_config.json`), you can use the header form instead:

```json
{
  "mcpServers": {
    "biodiversity-all": {
      "url": "https://biodiversity-mcp-all.onrender.com/mcp",
      "headers": { "x-api-key": "MCP1" }
    },
    "biodiversity-tier23": {
      "url": "https://biodiversity-mcp-tier23.onrender.com/mcp",
      "headers": { "x-api-key": "MCP2" }
    },
    "biodiversity-tier3": {
      "url": "https://biodiversity-mcp-tier3.onrender.com/mcp",
      "headers": { "x-api-key": "MCP3" }
    }
  }
}
```

Add only the connectors appropriate for the person using Claude. Do not share the all-data key with a Tier-3-only user.

## OAuth pilot (biodiversity-mcp-tier3 only)

`biodiversity-mcp-tier3` uses real per-user login instead of a shared `MCP_API_KEY` -- `biodiversity-mcp-all` and `biodiversity-mcp-tier23` are unaffected and keep using the API-key setup above. This is controlled by two env vars only set on the tier3 service: `AUTH_MODE=oauth` and `PUBLIC_BASE_URL` (its own `https://...onrender.com` URL). See `oauth_provider.py` for the implementation -- a minimal OAuth 2.1 authorization server (Dynamic Client Registration, PKCE, a first-party `/login` form) built on the MCP SDK's `OAuthAuthorizationServerProvider` interface, backed by Postgres.

Setup, after deploying: add the new OAuth tables without touching existing species data: `DATABASE_URL="..." python apply_schema.py`. That's the only one-time step against the database -- accounts themselves are self-service (see below).

In Claude's "Add custom connector" dialog, use `https://biodiversity-mcp-tier3.onrender.com/mcp` as the URL, and leave Authentication on its default "Detected" setting (or pick OAuth explicitly) rather than the "None" + header override used for the other two connectors. Claude will register itself automatically (Dynamic Client Registration) and redirect to this server's own login page.

**Whoever you share this URL with creates their own account.** The `/login` page has a "Create an account" link to `/signup`, where anyone can pick their own username and password (8+ characters) and is signed in immediately -- no DB or terminal access required, and nobody needs you to run a command on their behalf. `create_oauth_user.py` still exists as an admin-side fallback (e.g. to reset someone's password from the terminal), but it is no longer the primary way accounts get created.

Anyone with the connector URL can create an account this way, so treat sharing the URL itself as the actual access-control decision for this tier -- the same way you'd think about who gets the `-tier23` or `-all` API keys.

## Verification

```powershell
python -m py_compile server.py ingest_animals_data.py export_to_obsidian.py
```

Test the required isolation after deployment:

- MCP 1 can search a Tier 1, Tier 2, and Tier 3 record.
- MCP 2 cannot find a Tier 1 record but can find Tier 2 and Tier 3 records.
- MCP 3 can find only Tier 3 records and its Tier-3 private notes.
