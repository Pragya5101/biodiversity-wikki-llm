"""MCP server for the priority-scoped Wildlife & Biodiversity Wiki.

Deploy this same service three times with MCP_SCOPE set to ``all``,
``tier2plus``, or ``tier3only``.  Each deployment uses its own MCP_API_KEY.
"""

import os
import sys
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from psycopg2.pool import SimpleConnectionPool

try:
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError:
    from mcp.server.mcpserver import MCPServer as FastMCP


load_dotenv()
mcp = FastMCP("Wildlife Biodiversity Priority Wiki")

SCOPES = {
    "all": (1, 2, 3),
    "tier2plus": (2, 3),
    "tier3only": (3,),
}
MCP_SCOPE = os.environ.get("MCP_SCOPE", "all").lower()
if MCP_SCOPE not in SCOPES:
    raise RuntimeError("MCP_SCOPE must be one of: all, tier2plus, tier3only.")
ALLOWED_TIERS = SCOPES[MCP_SCOPE]
MCP_API_KEY = os.environ.get("MCP_API_KEY")

IS_WEB_TRANSPORT = (
    "--sse" in sys.argv
    or os.environ.get("TRANSPORT") == "sse"
    or os.environ.get("RENDER") is not None
    or os.environ.get("PORT") is not None
)

DATABASE_URL = os.environ.get("DATABASE_URL")
db_pool = None


def init_db_pool():
    global db_pool
    if not DATABASE_URL:
        return
    try:
        db_pool = SimpleConnectionPool(minconn=1, maxconn=10, dsn=DATABASE_URL)
    except Exception as error:
        print(f"Database connection pool unavailable: {error}", file=sys.stderr)
        db_pool = None


init_db_pool()


@contextmanager
def get_db_cursor():
    global db_pool
    if not db_pool:
        init_db_pool()
    if not db_pool:
        raise RuntimeError("Database pool not initialized. Set DATABASE_URL.")
    connection = db_pool.getconn()
    try:
        yield connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        db_pool.putconn(connection)


def scope_clause(column: str = "priority_tier") -> tuple[str, tuple[int, ...]]:
    placeholders = ", ".join(["%s"] * len(ALLOWED_TIERS))
    return f"{column} IN ({placeholders})", ALLOWED_TIERS


def resolve_species(cursor, species_name: str):
    scope_sql, scope_params = scope_clause("priority_tier")
    cursor.execute(
        f"""
        SELECT id, scientific_name, common_name, taxonomic_class, primary_habitat,
               curation_score, priority_tier
        FROM species
        WHERE (LOWER(scientific_name) = LOWER(%s) OR LOWER(common_name) = LOWER(%s))
          AND {scope_sql}
        LIMIT 1
        """,
        (species_name.strip(), species_name.strip(), *scope_params),
    )
    return cursor.fetchone()


def unavailable_message(species_name: str) -> str:
    return (
        f"'{species_name}' is not available through this endpoint. "
        f"This server exposes priority tiers {', '.join(map(str, ALLOWED_TIERS))}."
    )


@mcp.tool()
def search_wiki(query: str, limit: int = 20) -> str:
    """Search the species records that this MCP endpoint is permitted to expose."""
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        return "Invalid limit: provide a whole number from 1 to 50."
    try:
        scope_sql, scope_params = scope_clause("priority_tier")
        with get_db_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT common_name, scientific_name, primary_habitat, curation_score, priority_tier
                FROM species
                WHERE (common_name ILIKE %s OR scientific_name ILIKE %s)
                  AND {scope_sql}
                ORDER BY priority_tier, curation_score DESC, common_name
                LIMIT %s
                """,
                (f"%{query.strip()}%", f"%{query.strip()}%", *scope_params, limit),
            )
            rows = cursor.fetchall()
        if not rows:
            return "No matching records are available through this endpoint."
        output = [f"### Wiki search ({MCP_SCOPE})", "| Species | Habitat | Score | Priority tier |", "| --- | --- | --- | --- |"]
        output.extend(
            f"| {row['common_name']} (*{row['scientific_name']}*) | {row['primary_habitat']} | {row['curation_score']} | {row['priority_tier']} |"
            for row in rows
        )
        return "\n".join(output)
    except Exception as error:
        return f"Database error: {error}"


@mcp.tool()
def list_at_risk_species(limit: int = 20) -> str:
    """List species in this endpoint's scope that are at risk of extinction (IUCN status Vulnerable,
    Endangered, or Critically Endangered), ordered by poaching risk. Use this for open-ended questions
    like "which animals are endangered" or "what species are at risk", as opposed to search_wiki, which
    only matches species by name."""
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        return "Invalid limit: provide a whole number from 1 to 50."
    try:
        scope_sql, scope_params = scope_clause("s.priority_tier")
        with get_db_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT s.common_name, s.scientific_name, s.priority_tier, ci.iucn_status, ci.poaching_risk_score
                FROM species s
                JOIN conservation_intelligence ci ON ci.species_id = s.id
                WHERE {scope_sql} AND ci.iucn_status ~* '\\(CR\\)|\\(EN\\)|\\(VU\\)'
                ORDER BY ci.poaching_risk_score DESC, s.common_name
                LIMIT %s
                """,
                (*scope_params, limit),
            )
            rows = cursor.fetchall()
        if not rows:
            return "No at-risk species are available through this endpoint."
        output = [
            f"### At-risk species ({MCP_SCOPE})",
            "| Species | IUCN status | Poaching risk | Priority tier |",
            "| --- | --- | --- | --- |",
        ]
        output.extend(
            f"| {row['common_name']} (*{row['scientific_name']}*) | {row['iucn_status']} | {row['poaching_risk_score']}/10 | {row['priority_tier']} |"
            for row in rows
        )
        return "\n".join(output)
    except Exception as error:
        return f"Database error: {error}"


@mcp.tool()
def get_species_wiki(species_name: str, sightings_limit: int = 10) -> str:
    """Return the complete wiki profile for one species, if its priority tier is in this endpoint's scope."""
    if not isinstance(sightings_limit, int) or isinstance(sightings_limit, bool) or not 1 <= sightings_limit <= 100:
        return "Invalid sightings_limit: provide a whole number from 1 to 100."
    try:
        with get_db_cursor() as cursor:
            species = resolve_species(cursor, species_name)
            if not species:
                return unavailable_message(species_name)

            cursor.execute(
                """SELECT sighting_time, latitude, longitude, sensor_id, ambient_temp_c, image_path, notes
                   FROM sightings WHERE species_id = %s ORDER BY sighting_time DESC LIMIT %s""",
                (species["id"], sightings_limit),
            )
            sightings = cursor.fetchall()
            cursor.execute(
                """SELECT iucn_status, poaching_risk_score, protected_breeding_zone, patrol_frequency_days,
                          security_clearance_level, conservation_measures, last_assessment_date
                   FROM conservation_intelligence WHERE species_id = %s""",
                (species["id"],),
            )
            intelligence = cursor.fetchone()
            scope_sql, scope_params = scope_clause("partner.priority_tier")
            cursor.execute(
                f"""
                SELECT partner.common_name, partner.scientific_name, ei.interaction_type, ei.interaction_details
                FROM ecological_interactions ei
                JOIN species partner ON partner.id = CASE
                    WHEN ei.species_a_id = %s THEN ei.species_b_id ELSE ei.species_a_id END
                WHERE (ei.species_a_id = %s OR ei.species_b_id = %s) AND {scope_sql}
                """,
                (species["id"], species["id"], species["id"], *scope_params),
            )
            interactions = cursor.fetchall()
            cursor.execute(
                """SELECT c.name, c.geographic_region, c.corridor_type, c.threat_level
                   FROM corridors c JOIN species_corridors sc ON sc.corridor_id = c.id
                   WHERE sc.species_id = %s""",
                (species["id"],),
            )
            corridors = cursor.fetchall()
            cursor.execute(
                "SELECT note FROM private_notes WHERE species_id = %s AND priority_tier = 3 ORDER BY created_at DESC",
                (species["id"],),
            )
            private_notes = cursor.fetchall()

        output = [
            f"# {species['common_name']} (*{species['scientific_name']}*)",
            f"Scope: `{MCP_SCOPE}` | Curation score: **{species['curation_score']}** | Priority tier: **{species['priority_tier']}**",
            f"Habitat: {species['primary_habitat']} | Taxonomy: {species['taxonomic_class']}",
        ]
        if intelligence:
            output += ["\n## Conservation", f"- IUCN: {intelligence['iucn_status']}", f"- Poaching risk: {intelligence['poaching_risk_score']}/10", f"- Breeding zone: {intelligence['protected_breeding_zone']}"]
        output.append("\n## Ecological links")
        output.extend(f"- {row['interaction_type']}: {row['common_name']} (*{row['scientific_name']}*) — {row['interaction_details']}" for row in interactions) or output.append("- None in this endpoint scope.")
        output.append("\n## Corridors")
        output.extend(f"- {row['name']} ({row['corridor_type']}, {row['threat_level']} threat)" for row in corridors) or output.append("- None recorded.")
        output.append("\n## Recent sightings")
        output.extend(f"- {row['sighting_time']:%Y-%m-%d %H:%M UTC}: {float(row['latitude']):.4f}, {float(row['longitude']):.4f}; image `{row['image_path'] or '-'}`" for row in sightings) or output.append("- None recorded.")
        if private_notes:
            output.append("\n## Private curator notes (Tier 3)")
            output.extend(f"- {row['note']}" for row in private_notes)
        return "\n".join(output)
    except Exception as error:
        return f"Database error: {error}"


async def healthcheck(request):
    from starlette.responses import JSONResponse
    return JSONResponse({"status": "ok", "scope": MCP_SCOPE, "allowed_tiers": ALLOWED_TIERS})


try:
    from mcp.server.transport_security import TransportSecuritySettings
    mcp.settings.transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
except ImportError:
    pass
app = mcp.streamable_http_app()

from starlette.routing import Route
app.routes.append(Route("/healthz", healthcheck))

if IS_WEB_TRANSPORT:
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    class ApiKeyMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if request.url.path == "/healthz":
                return await call_next(request)
            if not MCP_API_KEY:
                return JSONResponse({"error": "MCP_API_KEY is not configured"}, status_code=503)
            supplied = request.headers.get("x-api-key") or request.query_params.get("api_key")
            if supplied != MCP_API_KEY:
                return JSONResponse({"error": "Unauthorized: missing or invalid API key"}, status_code=401)
            return await call_next(request)

    app.add_middleware(ApiKeyMiddleware)


if __name__ == "__main__":
    if IS_WEB_TRANSPORT:
        import uvicorn
        uvicorn.run("server:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), log_level="info")
    else:
        try:
            mcp.run(transport="stdio")
        except TypeError:
            mcp.run()
