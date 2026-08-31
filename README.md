# Wildlife & Biodiversity Knowledge Wiki & FastMCP Server (Kaggle Dataset)

A non-linear Knowledge Wiki using a Wildlife & Biodiversity dataset that connects to Claude via a Model Context Protocol (MCP) server. This setup uses the [Kaggle Wildlife Dataset](https://www.kaggle.com/datasets/banuprasadb/wildlife-dataset) to build a realistic 3-Tier relational database and an Obsidian Knowledge Graph.

---

## 🏗️ Architecture Overview

The system is structured in a **3-Tier Hierarchy** within a PostgreSQL database:
1. **Tier 1 (Raw Observation & Telemetry)**: Real-time sensor readings, GPS logs, ambient temperatures, and associated image file references parsed directly from the Kaggle YOLO bounding-box annotation text files.
2. **Tier 2 (Relational Network & Ecological Interactions)**: Predator-prey webs, symbiotic dependencies, and shared migration corridors generated based on ecological niches for 54 distinct animal species.
3. **Tier 3 (High-Priority Conservation Intelligence)**: IUCN threat classifications, poaching risk indexes, protected breeding zones, and patrol schedules for the 54 species.

An exporter script connects to this database to build a beautifully structured **Obsidian Vault** consisting of Markdown notes linked through non-linear wikilinks, allowing graph-view analysis of the species, corridors, and ecological relationships.

Finally, a **FastMCP Server** exposes this data hierarchy directly to Claude Desktop over **SSE (Server-Sent Events)**, allowing AI agents to query and reason non-linearly across the entire ecosystem.

---

## 🗄️ 1. Database & Ingestion Setup

### Step A: Initialize Schema
Load the database schema to set up the tables:
```bash
# Set your DATABASE_URL in your terminal session, e.g.:
# export DATABASE_URL="postgresql://username:password@localhost:5432/biodiversity"

# Run schema script
psql $DATABASE_URL -f schema.sql
```

### Step B: Download the Kaggle Dataset (YOLO Format)
Use the Kaggle CLI (or download it manually from [Kaggle](https://www.kaggle.com/datasets/banuprasadb/wildlife-dataset)) and unzip it:
```bash
kaggle datasets download -d banuprasadb/wildlife-dataset
unzip wildlife-dataset.zip -d wildlife_dataset
```

### Step C: Run the Ingestion Pipeline
The ingestion script `ingest_kaggle_data.py` will read the YOLO coordinates and image filenames, map them to the 54 species master table, assign realistic locations based on natural ranges, define interactions, and populate Tier 1, 2, and 3:

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Ingest the downloaded dataset
python ingest_kaggle_data.py --dataset-dir ./wildlife_dataset
```

> [!NOTE]
> If you do not have the dataset downloaded yet and want to run a quick test, you can run:
> `python ingest_kaggle_data.py`
> This will automatically generate a folder named `dummy_wildlife_dataset` containing mock YOLO annotations and load them into the database to demonstrate the functionality.

---

## 🗃️ 2. Obsidian Vault Exporter

The exporter script queries the database and generates linked Markdown notes inside the `./obsidian_vault` directory:

```bash
python export_to_obsidian.py
```

### Note Structure:
- Each species file (e.g., `Panthera_leo.md`) contains YAML frontmatter, an active telemetry sighting table (including image references mapping back to the Kaggle dataset images), and links to other species they interact with (e.g., `[[Equus_quagga]]`) and corridors they share (e.g. `[[Serengeti_Migration_Corridor]]`).
- When opened in Obsidian, the vault generates a multi-cluster non-linear graph representing food webs, predator-prey dynamics, and shared regional corridors.

---

## 🚀 3. Deployment Configuration (Render)

Deploy this Python FastMCP server as a **Web Service** on [Render](https://render.com) using Server-Sent Events (SSE).

### Step-by-Step Render Deployment:
1. **Create a GitHub Repository**: Push `server.py`, `ingest_kaggle_data.py`, `export_to_obsidian.py`, `requirements.txt`, `schema.sql`, and `.gitignore` to your repository. Do **not** push `.env` — it holds your local database password and `.gitignore` now excludes it; set `DATABASE_URL` and the tier keys as Render environment variables instead (Step 4).
2. **Create a Web Service on Render**:
   - Link your GitHub repository to Render.
   - Choose **Python** as the runtime environment.
3. **Configure Service Settings**:
   - **Name**: `biodiversity-mcp-server`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python server.py`
4. **Configure Environment Variables**:
   - Add the following key-value pairs:
     * `DATABASE_URL`: Your PostgreSQL database URI (e.g., from Neon, Supabase, or Render PostgreSQL).
     * `MCP_API_KEY_TIER1`: a secret string you generate — a connection using this key can only call `get_tier1_sightings`.
     * `MCP_API_KEY_TIER2`: a second, different secret string — unlocks `get_tier1_sightings` and `get_tier2_interactions`.
     * `MCP_API_KEY_TIER3`: a third, different secret string — unlocks all three tools, including `get_tier3_risk_intelligence` (poaching risk, protected breeding zones). Keep this one the most tightly held.
   - Leave all three unset only if you deliberately want the deployed server fully open (no clearance checks at all). Set at least one and the server starts rejecting unrecognized/missing keys with 401.
   - `PORT` does not need to be set manually — Render injects it, and the server now auto-detects that and switches to SSE mode even if the Start Command below is left as-is.
5. **Deploy**: Click **Create Web Service**. Once the build succeeds, the server will expose endpoints at:
   - SSE Connection: `https://<your-app-name>.onrender.com/sse`
   - Message Channel: `https://<your-app-name>.onrender.com/messages`

---

## 🔌 4. Client Integration (Claude Desktop Configuration)

To connect Claude Desktop to your deployed Render service, add the following configuration to your `claude_desktop_config.json` file. The `x-api-key` you paste in determines the clearance level of that connection — use `MCP_API_KEY_TIER3` for your own full-access setup, and hand out `MCP_API_KEY_TIER1` (or TIER2) to anyone who should only see the lower-sensitivity tiers.

### Config Location:
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`

### JSON Snippet:
```json
{
  "mcpServers": {
    "biodiversity-wiki": {
      "type": "sse",
      "url": "https://<your-app-name>.onrender.com/sse",
      "headers": {
        "x-api-key": "<one of MCP_API_KEY_TIER1 / TIER2 / TIER3, matching the clearance you want this connection to have>"
      }
    }
  }
}
```
*Replace `<your-app-name>` with your actual Render deployment subdomain. Omit the `headers` block only if you deliberately left all three tier keys unset on Render.*

---

## 📝 5. Testing Prompts

Verify Claude's capability to traverse all three database tiers non-linearly using these test queries:

### Prompt 1: High-Risk Species Sighting Analysis (Tiers 3 ➡️ 1)
> "Identify the species in the database that has the highest poaching risk score (Tier 3). Once found, retrieve its 5 most recent telemetry observations (Tier 1), and report the coordinates and associated Kaggle image path for each sighting."
*Testing: Queries Tier 3 to find a highly protected species (like Tiger or Elephant), then shifts to Tier 1 to fetch and summarize raw telemetry data with image path links.*

### Prompt 2: Food-Web Ecological Ripple Effects (Tiers 3 ➡️ 2)
> "Check the protected breeding zone info for the Tiger (Panthera tigris) in Tier 3. Then, retrieve all of its ecological interactions from Tier 2. If the Tiger population were to collapse, what prey species in that forest network would experience population spikes, and what are their primary habitats?"
*Testing: Queries Tier 3 for base species details, then traverses Tier 2 interactions to analyze secondary food-web impacts.*

### Prompt 3: Corridor Threat Evaluation & Conservation Briefing (Tiers 2 ➡️ 3 ➡️ 1)
> "For the species *Panthera onca* (Jaguar), retrieve its shared corridors and their threat levels from Tier 2. Cross-reference this with its poaching risk score and patrol frequency from Tier 3, and check its latest GPS telemetry and image path from Tier 1. Write an emergency recommendations brief for rangers patrolling its protected breeding zone."
*Testing: Fully traverses all three tiers in a single prompt (corridors in Tier 2, security/patrol values in Tier 3, and coordinates/image path in Tier 1).*
