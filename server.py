# server.py
"""
Model Context Protocol (MCP) Server for Wildlife & Biodiversity Wiki
Exposes Tier 1, Tier 2, and Tier 3 data endpoints to LLMs via FastMCP with SSE.
"""

import os
import sys
from contextlib import contextmanager
from contextvars import ContextVar
import psycopg2
import psycopg2.extras
from psycopg2.pool import SimpleConnectionPool
from dotenv import load_dotenv

from mcp.server.fastmcp import FastMCP

# Load environment variables
load_dotenv()

# Define the MCP server name
mcp = FastMCP("Wildlife Biodiversity Wiki")

# ==============================================================================
# Tiered credential model
# ==============================================================================
# Three separate API keys, one per clearance level. A key only unlocks its own
# tier and every tier below it:
#   MCP_API_KEY_TIER1 -> Tier 1 (general telemetry) only
#   MCP_API_KEY_TIER2 -> Tier 1 + Tier 2 (ecological network)
#   MCP_API_KEY_TIER3 -> Tier 1 + Tier 2 + Tier 3 (poaching risk / breeding zones)
# Whichever key a Claude connector is configured with determines what that
# connection can see - paste the Tier 3 key only into your own trusted config,
# hand out the Tier 1 key for anything general-purpose.
TIER_KEYS = {
    os.environ.get("MCP_API_KEY_TIER1"): 1,
    os.environ.get("MCP_API_KEY_TIER2"): 2,
    os.environ.get("MCP_API_KEY_TIER3"): 3,
}
TIER_KEYS.pop(None, None)  # drop unset env vars
ANY_TIER_KEY_CONFIGURED = len(TIER_KEYS) > 0

TIER_NAMES = {1: "Tier 1 (General)", 2: "Tier 2 (High Priority)", 3: "Tier 3 (Highest Priority)"}

# Per-request clearance level. Set by ApiKeyMiddleware on every HTTP request
# (both /sse and /messages), read by each tool at call time. Defaults to 3
# (no restriction) when no tier keys are configured at all, so local stdio
# use with Claude Desktop keeps working unauthenticated.
current_clearance: ContextVar[int] = ContextVar("current_clearance", default=3 if not ANY_TIER_KEY_CONFIGURED else 0)


def require_clearance(min_level: int, label: str):
    """Returns an error string if the caller's clearance is below min_level, else None."""
    level = current_clearance.get()
    if level < min_level:
        return (
            f"Access denied: {label} requires {TIER_NAMES[min_level]} credentials. "
            f"This connection is authenticated at clearance level {level or 'none'}."
        )
    return None

# Fetch database configuration
DATABASE_URL = os.environ.get("DATABASE_URL")

# Setup PostgreSQL Database connection pool
db_pool = None

def init_db_pool():
    global db_pool
    if not DATABASE_URL:
        if "--sse" in sys.argv:
            print("Warning: DATABASE_URL is not set. Database tools will fail when queried.", file=sys.stderr)
        return None
    try:
        db_pool = SimpleConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=DATABASE_URL
        )
        if "--sse" in sys.argv:
            print("PostgreSQL Database Connection Pool initialized successfully.", file=sys.stderr)
    except Exception as e:
        if "--sse" in sys.argv:
            print(f"Error initializing connection pool: {e}", file=sys.stderr)
        db_pool = None

# Initialize pool on startup
init_db_pool()

@contextmanager
def get_db_cursor():
    """Acquires a database connection from the pool and yields a dict-based cursor."""
    global db_pool
    # Re-initialize pool if it went down or wasn't set (e.g. env loaded late)
    if not db_pool:
        init_db_pool()
        if not db_pool:
            raise RuntimeError("Database pool not initialized. Please set DATABASE_URL environment variable.")
    
    conn = db_pool.getconn()
    try:
        yield conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        db_pool.putconn(conn)

def resolve_species(cur, species_name: str):
    """
    Helper function to resolve common or scientific species name to database ID.
    Returns species record dictionary or None.
    """
    query = """
        SELECT id, scientific_name, common_name, taxonomic_class, primary_habitat 
        FROM species 
        WHERE LOWER(scientific_name) = LOWER(%s) OR LOWER(common_name) = LOWER(%s)
        LIMIT 1
    """
    cur.execute(query, (species_name.strip(), species_name.strip()))
    return cur.fetchone()

# ==============================================================================
# MCP Tools
# ==============================================================================

@mcp.tool()
def get_tier1_sightings(species_name: str, limit: int = 10) -> str:
    """
    Retrieve raw observation & telemetry data (sighting times, coordinates, ambient temp, sensor notes)
    for a given species by common or scientific name.
    """
    denial = require_clearance(1, "Tier 1 telemetry data")
    if denial:
        return denial
    try:
        with get_db_cursor() as cur:
            species = resolve_species(cur, species_name)
            if not species:
                return f"Species '{species_name}' not found in the database. Double-check scientific or common spelling."
            
            query = """
                SELECT sighting_time, latitude, longitude, sensor_id, battery_level_pct, ambient_temp_c, image_path, notes
                FROM sightings
                WHERE species_id = %s
                ORDER BY sighting_time DESC
                LIMIT %s
            """
            cur.execute(query, (species['id'], limit))
            sightings = cur.fetchall()
            
            if not sightings:
                return f"No telemetry observations recorded for {species['common_name']} ({species['scientific_name']})."
            
            # Format as Markdown table
            output = [
                f"### Tier 1 Telemetry Observations for {species['common_name']} (*{species['scientific_name']}*)",
                f"Primary Habitat: {species['primary_habitat']} | Taxonomy: {species['taxonomic_class']}\n",
                "| Timestamp | Location (Lat, Long) | Sensor ID | Temp (°C) | Battery | Image Path Reference | Observation Notes |",
                "| --- | --- | --- | --- | --- | --- | --- |"
            ]
            for row in sightings:
                time_str = row['sighting_time'].strftime("%Y-%m-%d %H:%M UTC")
                battery = f"{row['battery_level_pct']}%" if row['battery_level_pct'] is not None else "N/A"
                temp = f"{row['ambient_temp_c']}°C" if row['ambient_temp_c'] is not None else "N/A"
                img_path = f"`{row['image_path']}`" if row['image_path'] else "-"
                notes = row['notes'] if row['notes'] else "-"
                output.append(f"| {time_str} | {float(row['latitude']):.4f}, {float(row['longitude']):.4f} | `{row['sensor_id']}` | {temp} | {battery} | {img_path} | {notes} |")
                
            return "\n".join(output)
            
    except Exception as e:
        return f"Database error occurred: {str(e)}"


@mcp.tool()
def get_tier2_interactions(species_name: str) -> str:
    """
    Retrieve ecological relationships (predator-prey, mutualism, food-web links)
    and shared environmental corridors for a given species.
    """
    denial = require_clearance(2, "Tier 2 ecological network data")
    if denial:
        return denial
    try:
        with get_db_cursor() as cur:
            species = resolve_species(cur, species_name)
            if not species:
                return f"Species '{species_name}' not found in the database."

            # Query Interactions
            query_interactions = """
                SELECT 
                    s_a.common_name as sa_common, s_a.scientific_name as sa_sci,
                    s_b.common_name as sb_common, s_b.scientific_name as sb_sci,
                    ei.interaction_type, ei.energy_transfer_pathway, ei.interaction_details
                FROM ecological_interactions ei
                JOIN species s_a ON ei.species_a_id = s_a.id
                JOIN species s_b ON ei.species_b_id = s_b.id
                WHERE ei.species_a_id = %s OR ei.species_b_id = %s
            """
            cur.execute(query_interactions, (species['id'], species['id']))
            interactions = cur.fetchall()

            # Query Shared Corridors
            query_corridors = """
                SELECT c.name, c.geographic_region, c.corridor_type, c.threat_level
                FROM corridors c
                JOIN species_corridors sc ON c.id = sc.corridor_id
                WHERE sc.species_id = %s
            """
            cur.execute(query_corridors, (species['id'],))
            corridors = cur.fetchall()

            output = [f"### Tier 2 Ecological & Network Profile: {species['common_name']} (*{species['scientific_name']}*)"]

            # Format Interactions
            output.append("\n#### 🕸️ Food Web & Ecological Interactions")
            if not interactions:
                output.append("*No documented interactions for this species in the network database.*")
            else:
                for row in interactions:
                    # Determine directional relationship
                    if row['sa_sci'].lower() == species['scientific_name'].lower():
                        role = "Source"
                        partner = f"{row['sb_common']} (*{row['sb_sci']}*)"
                    else:
                        role = "Target"
                        partner = f"{row['sa_common']} (*{row['sa_sci']}*)"
                    
                    output.append(
                        f"- **Type:** {row['interaction_type']} relationship with {partner}\n"
                        f"  - *Pathway:* {row['energy_transfer_pathway']}\n"
                        f"  - *Details:* {row['interaction_details']}"
                    )

            # Format Corridors
            output.append("\n#### 🗺️ Shared Corridors & Habitat Connections")
            if not corridors:
                output.append("*No migratory or geographical corridors registered for this species.*")
            else:
                for row in corridors:
                    output.append(
                        f"- **Corridor Name:** {row['name']}\n"
                        f"  - *Region:* {row['geographic_region']}\n"
                        f"  - *Type:* {row['corridor_type']} | *Threat Level:* {row['threat_level']}"
                    )

            return "\n".join(output)

    except Exception as e:
        return f"Database error occurred: {str(e)}"


@mcp.tool()
def get_tier3_risk_intelligence(species_name: str) -> str:
    """
    Retrieve high-priority conservation intelligence data (IUCN status,
    poaching risk scores, protected breeding zones, security clearance) for a given species.
    """
    denial = require_clearance(3, "Tier 3 conservation intelligence")
    if denial:
        return denial
    try:
        with get_db_cursor() as cur:
            species = resolve_species(cur, species_name)
            if not species:
                return f"Species '{species_name}' not found in the database."

            query = """
                SELECT iucn_status, poaching_risk_score, protected_breeding_zone, 
                       patrol_frequency_days, security_clearance_level, conservation_measures, 
                       last_assessment_date
                FROM conservation_intelligence
                WHERE species_id = %s
            """
            cur.execute(query, (species['id'],))
            intel = cur.fetchone()

            if not intel:
                return f"No Tier 3 Conservation Intelligence recorded for {species['common_name']} (*{species['scientific_name']}*)."

            # Format Response
            output = [
                f"### 🛡️ Tier 3 Conservation Intelligence: {species['common_name']} (*{species['scientific_name']}*)",
                f"**[SECURITY LEVEL: {intel['security_clearance_level']}]**\n",
                f"- **IUCN Red List Status:** {intel['iucn_status']}",
                f"- **Poaching Threat Risk Score:** {intel['poaching_risk_score']}/10",
                f"- **Protected Breeding Zone:** {intel['protected_breeding_zone']}",
                f"- **Ranger Patrol Interval:** Every {intel['patrol_frequency_days']} days",
                f"- **Last Security Assessment:** {intel['last_assessment_date']}\n",
                "#### Active Conservation Protocols",
                intel['conservation_measures'] if intel['conservation_measures'] else "No protocols active."
            ]
            return "\n".join(output)

    except Exception as e:
        return f"Database error occurred: {str(e)}"


# ==============================================================================
# FastAPI / ASGI Application Setup (for SSE Deployment)
# ==============================================================================

# Expose Starlette app containing SSE routes (/sse and /messages)
app = mcp.sse_app()

# --- Tiered access control ----------------------------------------------------
# Reads the `x-api-key` header (or ?api_key=) on every request - both the
# long-lived /sse connection and each /messages POST - looks it up in
# TIER_KEYS, and sets current_clearance for the tool functions above to check.
# If no MCP_API_KEY_TIER* env vars are set at all, the server stays open at
# full clearance (dev/local mode). Once any tier key is configured, an
# unrecognized or missing key is rejected outright (401) rather than silently
# defaulting to level 0, so a misconfigured client fails loudly instead of
# just seeing empty/denied tool results.
if ANY_TIER_KEY_CONFIGURED:
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    class TieredApiKeyMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            supplied = request.headers.get("x-api-key") or request.query_params.get("api_key")
            level = TIER_KEYS.get(supplied)
            if level is None:
                return JSONResponse({"error": "Unauthorized: missing or invalid API key"}, status_code=401)
            current_clearance.set(level)
            return await call_next(request)

    app.add_middleware(TieredApiKeyMiddleware)

# Render (and most PaaS) inject PORT automatically for any deployed web
# service, so use its presence as a second signal to switch into SSE mode
# even if the Start Command forgets the --sse flag.
_should_run_sse = (
    "--sse" in sys.argv
    or os.environ.get("TRANSPORT") == "sse"
    or os.environ.get("RENDER") is not None
    or os.environ.get("PORT") is not None
)

if __name__ == "__main__":
    if _should_run_sse:
        import uvicorn
        port = int(os.environ.get("PORT", 8000))
        if not ANY_TIER_KEY_CONFIGURED:
            print("Warning: no MCP_API_KEY_TIER1/2/3 set. This SSE endpoint will be publicly queryable at full clearance by anyone with the URL.", file=sys.stderr)
        print(f"Starting MCP SSE Server on port {port}...", file=sys.stderr)
        uvicorn.run("server:app", host="0.0.0.0", port=port, log_level="info")
    else:
        # Default: Run stdio mode (directly managed by Claude Desktop)
        try:
            mcp.run(transport="stdio")
        except TypeError:
            mcp.run()
