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

from mcp.server.fastmcp import FastMCP


load_dotenv()

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

# AUTH_MODE is a per-deployment pilot switch. "apikey" (default) is the
# original shared-secret x-api-key/api_key check, unchanged, used by
# biodiversity-mcp-all and biodiversity-mcp-tier23. "oauth" -- currently only
# set on biodiversity-mcp-tier3 -- replaces it with a real per-user OAuth 2.1
# login (see oauth_provider.py) so access isn't one shared secret string.
AUTH_MODE = os.environ.get("AUTH_MODE", "apikey").lower()
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL")

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


oauth_provider = None
if AUTH_MODE == "oauth":
    if not PUBLIC_BASE_URL:
        raise RuntimeError("PUBLIC_BASE_URL must be set when AUTH_MODE=oauth (e.g. https://biodiversity-mcp-tier3.onrender.com).")
    from oauth_provider import WikiOAuthProvider
    from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions

    oauth_provider = WikiOAuthProvider(get_db_cursor)
    mcp = FastMCP(
        "Wildlife Biodiversity Priority Wiki",
        auth_server_provider=oauth_provider,
        auth=AuthSettings(
            issuer_url=PUBLIC_BASE_URL,
            resource_server_url=PUBLIC_BASE_URL,
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=["mcp"], default_scopes=["mcp"]
            ),
            revocation_options=RevocationOptions(enabled=True),
        ),
    )
elif AUTH_MODE != "apikey":
    raise RuntimeError("AUTH_MODE must be one of: apikey, oauth.")
else:
    mcp = FastMCP("Wildlife Biodiversity Priority Wiki")


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
def list_extinct_species(limit: int = 20) -> str:
    """List species in this endpoint's scope that are already extinct (IUCN status Extinct or
    Extinct in the Wild). Use this for open-ended questions like "give me the names of extinct
    animals" or "which species have gone extinct" -- as opposed to list_at_risk_species, which
    covers species that are still alive but threatened."""
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        return "Invalid limit: provide a whole number from 1 to 50."
    try:
        scope_sql, scope_params = scope_clause("s.priority_tier")
        with get_db_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT s.common_name, s.scientific_name, s.priority_tier, ci.iucn_status
                FROM species s
                JOIN conservation_intelligence ci ON ci.species_id = s.id
                WHERE {scope_sql} AND ci.iucn_status ~* '\\(EX\\)|\\(EW\\)'
                ORDER BY s.common_name
                LIMIT %s
                """,
                (*scope_params, limit),
            )
            rows = cursor.fetchall()
        if not rows:
            return "No extinct species are available through this endpoint."
        output = [
            f"### Extinct species ({MCP_SCOPE})",
            "| Species | IUCN status | Priority tier |",
            "| --- | --- | --- |",
        ]
        output.extend(
            f"| {row['common_name']} (*{row['scientific_name']}*) | {row['iucn_status']} | {row['priority_tier']} |"
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
        if interactions:
            output.extend(f"- {row['interaction_type']}: {row['common_name']} (*{row['scientific_name']}*) — {row['interaction_details']}" for row in interactions)
        else:
            output.append("- None in this endpoint scope.")
        output.append("\n## Corridors")
        if corridors:
            output.extend(f"- {row['name']} ({row['corridor_type']}, {row['threat_level']} threat)" for row in corridors)
        else:
            output.append("- None recorded.")
        output.append("\n## Recent sightings")
        if sightings:
            output.extend(f"- {row['sighting_time']:%Y-%m-%d %H:%M UTC}: {float(row['latitude']):.4f}, {float(row['longitude']):.4f}; image `{row['image_path'] or '-'}`" for row in sightings)
        else:
            output.append("- None recorded.")
        if private_notes:
            output.append("\n## Private curator notes (Tier 3)")
            output.extend(f"- {row['note']}" for row in private_notes)
        return "\n".join(output)
    except Exception as error:
        return f"Database error: {error}"


def _validate_limit(limit: int, max_limit: int = 50) -> str | None:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= max_limit:
        return f"Invalid limit: provide a whole number from 1 to {max_limit}."
    return None


def _species_table(rows) -> str:
    output = ["| Species | Habitat | Score | Priority tier |", "| --- | --- | --- | --- |"]
    output.extend(
        f"| {row['common_name']} (*{row['scientific_name']}*) | {row['primary_habitat']} | {row['curation_score']} | {row['priority_tier']} |"
        for row in rows
    )
    return "\n".join(output)


# --- Taxonomy / habitat dimension (species table) ---------------------------

@mcp.tool()
def list_species_by_class(taxonomic_class: str, limit: int = 20) -> str:
    """List species in this endpoint's scope belonging to a given taxonomic class (e.g. "Mammalia",
    "Aves", "Reptilia", "Amphibia"). Use this for questions like "what mammals are in the wiki",
    as opposed to search_wiki, which matches by species name, not taxonomy."""
    if err := _validate_limit(limit):
        return err
    try:
        scope_sql, scope_params = scope_clause("priority_tier")
        with get_db_cursor() as cursor:
            cursor.execute(
                f"""SELECT common_name, scientific_name, primary_habitat, curation_score, priority_tier
                    FROM species WHERE taxonomic_class ILIKE %s AND {scope_sql}
                    ORDER BY priority_tier, curation_score DESC, common_name LIMIT %s""",
                (f"%{taxonomic_class.strip()}%", *scope_params, limit),
            )
            rows = cursor.fetchall()
        if not rows:
            return f"No species of class matching '{taxonomic_class}' are available through this endpoint."
        return f"### Species in class '{taxonomic_class}' ({MCP_SCOPE})\n" + _species_table(rows)
    except Exception as error:
        return f"Database error: {error}"


@mcp.tool()
def list_species_by_habitat(habitat: str, limit: int = 20) -> str:
    """List species in this endpoint's scope whose primary habitat matches the given text (e.g.
    "Forest", "Wetlands", "Savannah"). Use this for questions like "what species live in wetlands"."""
    if err := _validate_limit(limit):
        return err
    try:
        scope_sql, scope_params = scope_clause("priority_tier")
        with get_db_cursor() as cursor:
            cursor.execute(
                f"""SELECT common_name, scientific_name, primary_habitat, curation_score, priority_tier
                    FROM species WHERE primary_habitat ILIKE %s AND {scope_sql}
                    ORDER BY priority_tier, curation_score DESC, common_name LIMIT %s""",
                (f"%{habitat.strip()}%", *scope_params, limit),
            )
            rows = cursor.fetchall()
        if not rows:
            return f"No species with habitat matching '{habitat}' are available through this endpoint."
        return f"### Species in habitat '{habitat}' ({MCP_SCOPE})\n" + _species_table(rows)
    except Exception as error:
        return f"Database error: {error}"


@mcp.tool()
def list_species_by_tier(tier: int, limit: int = 20) -> str:
    """List species assigned to one specific priority tier (1, 2, or 3), if that tier is in this
    endpoint's scope. Use this to browse a whole tier directly, as opposed to search_wiki, which
    requires a name."""
    if err := _validate_limit(limit):
        return err
    if not isinstance(tier, int) or isinstance(tier, bool) or tier not in (1, 2, 3):
        return "Invalid tier: provide 1, 2, or 3."
    if tier not in ALLOWED_TIERS:
        return f"Tier {tier} is not available through this endpoint. This server exposes priority tiers {', '.join(map(str, ALLOWED_TIERS))}."
    try:
        with get_db_cursor() as cursor:
            cursor.execute(
                """SELECT common_name, scientific_name, primary_habitat, curation_score, priority_tier
                   FROM species WHERE priority_tier = %s
                   ORDER BY curation_score DESC, common_name LIMIT %s""",
                (tier, limit),
            )
            rows = cursor.fetchall()
        if not rows:
            return f"No species found in tier {tier}."
        return f"### Tier {tier} species ({MCP_SCOPE})\n" + _species_table(rows)
    except Exception as error:
        return f"Database error: {error}"


# --- Conservation dimension (conservation_intelligence table) ---------------

@mcp.tool()
def get_conservation_status(species_name: str) -> str:
    """Return just the conservation-status fields (IUCN status, poaching risk, breeding zone, patrol
    frequency, last assessment date) for one species -- a lighter-weight alternative to
    get_species_wiki when you don't need ecological links, corridors, or sightings too."""
    try:
        with get_db_cursor() as cursor:
            species = resolve_species(cursor, species_name)
            if not species:
                return unavailable_message(species_name)
            cursor.execute(
                """SELECT iucn_status, poaching_risk_score, protected_breeding_zone,
                          patrol_frequency_days, conservation_measures, last_assessment_date
                   FROM conservation_intelligence WHERE species_id = %s""",
                (species["id"],),
            )
            intelligence = cursor.fetchone()
        if not intelligence:
            return f"No conservation intelligence recorded for '{species_name}'."
        return "\n".join([
            f"### Conservation status: {species['common_name']} (*{species['scientific_name']}*)",
            f"- IUCN status: {intelligence['iucn_status']}",
            f"- Poaching risk: {intelligence['poaching_risk_score']}/10",
            f"- Protected breeding zone: {intelligence['protected_breeding_zone']}",
            f"- Patrol frequency: every {intelligence['patrol_frequency_days']} days",
            f"- Last assessment: {intelligence['last_assessment_date']}",
            f"- Measures: {intelligence['conservation_measures']}",
        ])
    except Exception as error:
        return f"Database error: {error}"


@mcp.tool()
def list_species_by_iucn_status(status_keyword: str, limit: int = 20) -> str:
    """List species in this endpoint's scope whose IUCN status matches the given text (e.g.
    "Least concern", "Data deficient", "Near Threatened", "Vulnerable"). Use this for a specific
    single status; use list_at_risk_species or list_extinct_species for the broader CR/EN/VU or
    EX/EW groupings instead."""
    if err := _validate_limit(limit):
        return err
    try:
        scope_sql, scope_params = scope_clause("s.priority_tier")
        with get_db_cursor() as cursor:
            cursor.execute(
                f"""SELECT s.common_name, s.scientific_name, s.priority_tier, ci.iucn_status
                    FROM species s JOIN conservation_intelligence ci ON ci.species_id = s.id
                    WHERE ci.iucn_status ILIKE %s AND {scope_sql}
                    ORDER BY s.common_name LIMIT %s""",
                (f"%{status_keyword.strip()}%", *scope_params, limit),
            )
            rows = cursor.fetchall()
        if not rows:
            return f"No species with IUCN status matching '{status_keyword}' are available through this endpoint."
        output = [f"### Species with IUCN status '{status_keyword}' ({MCP_SCOPE})", "| Species | IUCN status | Priority tier |", "| --- | --- | --- |"]
        output.extend(f"| {row['common_name']} (*{row['scientific_name']}*) | {row['iucn_status']} | {row['priority_tier']} |" for row in rows)
        return "\n".join(output)
    except Exception as error:
        return f"Database error: {error}"


@mcp.tool()
def list_high_poaching_risk(min_score: int = 7, limit: int = 20) -> str:
    """List species in this endpoint's scope with a poaching-risk score at or above the given
    threshold (1-10 scale). Use this for questions like "which species have the highest poaching
    risk", as opposed to list_at_risk_species, which filters by IUCN status rather than the
    numeric risk score."""
    if err := _validate_limit(limit):
        return err
    if not isinstance(min_score, int) or isinstance(min_score, bool) or not 1 <= min_score <= 10:
        return "Invalid min_score: provide a whole number from 1 to 10."
    try:
        scope_sql, scope_params = scope_clause("s.priority_tier")
        with get_db_cursor() as cursor:
            cursor.execute(
                f"""SELECT s.common_name, s.scientific_name, s.priority_tier, ci.poaching_risk_score
                    FROM species s JOIN conservation_intelligence ci ON ci.species_id = s.id
                    WHERE ci.poaching_risk_score >= %s AND {scope_sql}
                    ORDER BY ci.poaching_risk_score DESC, s.common_name LIMIT %s""",
                (min_score, *scope_params, limit),
            )
            rows = cursor.fetchall()
        if not rows:
            return f"No species with poaching risk >= {min_score} are available through this endpoint."
        output = [f"### Poaching risk >= {min_score} ({MCP_SCOPE})", "| Species | Poaching risk | Priority tier |", "| --- | --- | --- |"]
        output.extend(f"| {row['common_name']} (*{row['scientific_name']}*) | {row['poaching_risk_score']}/10 | {row['priority_tier']} |" for row in rows)
        return "\n".join(output)
    except Exception as error:
        return f"Database error: {error}"


@mcp.tool()
def list_species_by_population_trend(trend: str, limit: int = 20) -> str:
    """List species in this endpoint's scope with a recorded population trend matching the given
    text (typically "Increasing", "Decreasing", or "Stable"). Use this for questions like "which
    species have a declining population"."""
    if err := _validate_limit(limit):
        return err
    try:
        scope_sql, scope_params = scope_clause("s.priority_tier")
        with get_db_cursor() as cursor:
            cursor.execute(
                f"""SELECT s.common_name, s.scientific_name, s.priority_tier, ci.conservation_measures
                    FROM species s JOIN conservation_intelligence ci ON ci.species_id = s.id
                    WHERE ci.conservation_measures ILIKE %s AND {scope_sql}
                    ORDER BY s.common_name LIMIT %s""",
                (f"%Population trend: {trend.strip()}%", *scope_params, limit),
            )
            rows = cursor.fetchall()
        if not rows:
            return f"No species with population trend matching '{trend}' are available through this endpoint."
        output = [f"### Population trend '{trend}' ({MCP_SCOPE})", "| Species | Priority tier |", "| --- | --- |"]
        output.extend(f"| {row['common_name']} (*{row['scientific_name']}*) | {row['priority_tier']} |" for row in rows)
        return "\n".join(output)
    except Exception as error:
        return f"Database error: {error}"


@mcp.tool()
def list_species_needing_reassessment(older_than_days: int = 180, limit: int = 20) -> str:
    """List species in this endpoint's scope whose conservation status was last assessed more than
    the given number of days ago. Use this for data-quality questions like "which records are
    stale" or "what needs a fresh conservation assessment"."""
    if err := _validate_limit(limit):
        return err
    if not isinstance(older_than_days, int) or isinstance(older_than_days, bool) or older_than_days < 1:
        return "Invalid older_than_days: provide a positive whole number."
    try:
        scope_sql, scope_params = scope_clause("s.priority_tier")
        with get_db_cursor() as cursor:
            cursor.execute(
                f"""SELECT s.common_name, s.scientific_name, s.priority_tier, ci.last_assessment_date
                    FROM species s JOIN conservation_intelligence ci ON ci.species_id = s.id
                    WHERE ci.last_assessment_date < CURRENT_DATE - (%s * INTERVAL '1 day') AND {scope_sql}
                    ORDER BY ci.last_assessment_date ASC LIMIT %s""",
                (older_than_days, *scope_params, limit),
            )
            rows = cursor.fetchall()
        if not rows:
            return f"No species need reassessment (older than {older_than_days} days) through this endpoint."
        output = [f"### Assessment older than {older_than_days} days ({MCP_SCOPE})", "| Species | Last assessment | Priority tier |", "| --- | --- | --- |"]
        output.extend(f"| {row['common_name']} (*{row['scientific_name']}*) | {row['last_assessment_date']} | {row['priority_tier']} |" for row in rows)
        return "\n".join(output)
    except Exception as error:
        return f"Database error: {error}"


# --- Ecological network dimension (ecological_interactions table) -----------

def _directional_interactions(species_name: str, predator_side: bool) -> str:
    """predator_side=True returns species that prey ON species_name; False returns species_name's prey."""
    try:
        with get_db_cursor() as cursor:
            species = resolve_species(cursor, species_name)
            if not species:
                return unavailable_message(species_name)
            scope_sql, scope_params = scope_clause("partner.priority_tier")
            if predator_side:
                where_id, select_id = "ei.species_b_id", "ei.species_a_id"
            else:
                where_id, select_id = "ei.species_a_id", "ei.species_b_id"
            cursor.execute(
                f"""SELECT partner.common_name, partner.scientific_name, ei.interaction_details
                    FROM ecological_interactions ei
                    JOIN species partner ON partner.id = {select_id}
                    WHERE {where_id} = %s AND ei.interaction_type = 'Predation' AND {scope_sql}""",
                (species["id"], *scope_params),
            )
            rows = cursor.fetchall()
        label = "Predators of" if predator_side else "Prey of"
        if not rows:
            return f"{label} {species['common_name']}: none recorded in this endpoint's scope."
        output = [f"### {label} {species['common_name']} (*{species['scientific_name']}*) ({MCP_SCOPE})"]
        output.extend(f"- {row['common_name']} (*{row['scientific_name']}*) — {row['interaction_details']}" for row in rows)
        return "\n".join(output)
    except Exception as error:
        return f"Database error: {error}"


@mcp.tool()
def get_predators_of(species_name: str) -> str:
    """List the species recorded as predators of the given species, within this endpoint's scope.
    Use this for questions like "what preys on the zebra"."""
    return _directional_interactions(species_name, predator_side=True)


@mcp.tool()
def get_prey_of(species_name: str) -> str:
    """List the species recorded as prey of the given species, within this endpoint's scope. Use
    this for questions like "what does the tiger eat"."""
    return _directional_interactions(species_name, predator_side=False)


@mcp.tool()
def list_interactions_by_type(interaction_type: str, limit: int = 20) -> str:
    """Browse ecological interactions of a given type (e.g. "Predation", "Symbiosis") across all
    species pairs in this endpoint's scope, rather than for one named species. Use this for
    questions like "show me examples of symbiosis in the wiki"."""
    if err := _validate_limit(limit):
        return err
    try:
        scope_a, params_a = scope_clause("a.priority_tier")
        scope_b, params_b = scope_clause("b.priority_tier")
        with get_db_cursor() as cursor:
            cursor.execute(
                f"""SELECT a.common_name AS a_name, a.scientific_name AS a_sci,
                           b.common_name AS b_name, b.scientific_name AS b_sci, ei.interaction_details
                    FROM ecological_interactions ei
                    JOIN species a ON a.id = ei.species_a_id
                    JOIN species b ON b.id = ei.species_b_id
                    WHERE ei.interaction_type ILIKE %s AND {scope_a} AND {scope_b}
                    LIMIT %s""",
                (f"%{interaction_type.strip()}%", *params_a, *params_b, limit),
            )
            rows = cursor.fetchall()
        if not rows:
            return f"No '{interaction_type}' interactions are available through this endpoint."
        output = [f"### Interactions of type '{interaction_type}' ({MCP_SCOPE})"]
        output.extend(
            f"- {row['a_name']} (*{row['a_sci']}*) ↔ {row['b_name']} (*{row['b_sci']}*): {row['interaction_details']}"
            for row in rows
        )
        return "\n".join(output)
    except Exception as error:
        return f"Database error: {error}"


# --- Corridor dimension (corridors / species_corridors tables) --------------

@mcp.tool()
def list_corridors(limit: int = 20) -> str:
    """List all named geographic corridors in the wiki, with their region, type, and threat level.
    Corridor metadata is not tier-restricted; use get_species_in_corridor to see which species
    (filtered to this endpoint's scope) use a given corridor."""
    if err := _validate_limit(limit):
        return err
    try:
        with get_db_cursor() as cursor:
            cursor.execute(
                "SELECT name, geographic_region, corridor_type, threat_level FROM corridors ORDER BY name LIMIT %s",
                (limit,),
            )
            rows = cursor.fetchall()
        if not rows:
            return "No corridors recorded."
        output = ["### Corridors", "| Name | Region | Type | Threat level |", "| --- | --- | --- | --- |"]
        output.extend(f"| {row['name']} | {row['geographic_region']} | {row['corridor_type']} | {row['threat_level']} |" for row in rows)
        return "\n".join(output)
    except Exception as error:
        return f"Database error: {error}"


@mcp.tool()
def get_species_in_corridor(corridor_name: str, limit: int = 20) -> str:
    """List the species (filtered to this endpoint's scope) that use a given named corridor. Use
    this for questions like "what species use the Serengeti Migration Corridor"."""
    if err := _validate_limit(limit):
        return err
    try:
        scope_sql, scope_params = scope_clause("s.priority_tier")
        with get_db_cursor() as cursor:
            cursor.execute(
                f"""SELECT s.common_name, s.scientific_name, s.priority_tier
                    FROM species_corridors sc
                    JOIN species s ON s.id = sc.species_id
                    JOIN corridors c ON c.id = sc.corridor_id
                    WHERE c.name ILIKE %s AND {scope_sql}
                    ORDER BY s.common_name LIMIT %s""",
                (f"%{corridor_name.strip()}%", *scope_params, limit),
            )
            rows = cursor.fetchall()
        if not rows:
            return f"No species using a corridor matching '{corridor_name}' are available through this endpoint."
        output = [f"### Species using corridor '{corridor_name}' ({MCP_SCOPE})", "| Species | Priority tier |", "| --- | --- |"]
        output.extend(f"| {row['common_name']} (*{row['scientific_name']}*) | {row['priority_tier']} |" for row in rows)
        return "\n".join(output)
    except Exception as error:
        return f"Database error: {error}"


@mcp.tool()
def list_corridors_by_threat(threat_level: str, limit: int = 20) -> str:
    """List corridors matching a given threat level (e.g. "Low", "Medium", "High", "Critical").
    Use this for questions like "which corridors are under critical threat"."""
    if err := _validate_limit(limit):
        return err
    try:
        with get_db_cursor() as cursor:
            cursor.execute(
                """SELECT name, geographic_region, corridor_type, threat_level FROM corridors
                   WHERE threat_level ILIKE %s ORDER BY name LIMIT %s""",
                (f"%{threat_level.strip()}%", limit),
            )
            rows = cursor.fetchall()
        if not rows:
            return f"No corridors with threat level matching '{threat_level}'."
        output = [f"### Corridors with threat level '{threat_level}'", "| Name | Region | Type |", "| --- | --- | --- |"]
        output.extend(f"| {row['name']} | {row['geographic_region']} | {row['corridor_type']} |" for row in rows)
        return "\n".join(output)
    except Exception as error:
        return f"Database error: {error}"


# --- Telemetry dimension (sightings table) -----------------------------------

@mcp.tool()
def get_recent_sightings(species_name: str, limit: int = 10) -> str:
    """Return only the recent-sightings telemetry (timestamp, location, sensor) for one species --
    a lighter-weight alternative to get_species_wiki when you don't need conservation, ecological,
    or corridor data too."""
    if err := _validate_limit(limit, max_limit=100):
        return err
    try:
        with get_db_cursor() as cursor:
            species = resolve_species(cursor, species_name)
            if not species:
                return unavailable_message(species_name)
            cursor.execute(
                """SELECT sighting_time, latitude, longitude, sensor_id, notes
                   FROM sightings WHERE species_id = %s ORDER BY sighting_time DESC LIMIT %s""",
                (species["id"], limit),
            )
            rows = cursor.fetchall()
        if not rows:
            return f"No sightings recorded for '{species_name}'."
        output = [f"### Recent sightings: {species['common_name']} (*{species['scientific_name']}*)"]
        output.extend(
            f"- {row['sighting_time']:%Y-%m-%d %H:%M UTC}: {float(row['latitude']):.4f}, {float(row['longitude']):.4f} (sensor `{row['sensor_id']}`)"
            for row in rows
        )
        return "\n".join(output)
    except Exception as error:
        return f"Database error: {error}"


@mcp.tool()
def list_recent_global_sightings(limit: int = 20) -> str:
    """List the most recent sightings across all species in this endpoint's scope, newest first --
    a live telemetry feed, as opposed to get_recent_sightings, which is scoped to one named species."""
    if err := _validate_limit(limit, max_limit=100):
        return err
    try:
        scope_sql, scope_params = scope_clause("s.priority_tier")
        with get_db_cursor() as cursor:
            cursor.execute(
                f"""SELECT s.common_name, s.scientific_name, sg.sighting_time, sg.latitude, sg.longitude, sg.sensor_id
                    FROM sightings sg JOIN species s ON s.id = sg.species_id
                    WHERE {scope_sql}
                    ORDER BY sg.sighting_time DESC LIMIT %s""",
                (*scope_params, limit),
            )
            rows = cursor.fetchall()
        if not rows:
            return "No sightings are available through this endpoint."
        output = [f"### Recent sightings feed ({MCP_SCOPE})"]
        output.extend(
            f"- {row['sighting_time']:%Y-%m-%d %H:%M UTC}: {row['common_name']} (*{row['scientific_name']}*) at "
            f"{float(row['latitude']):.4f}, {float(row['longitude']):.4f} (sensor `{row['sensor_id']}`)"
            for row in rows
        )
        return "\n".join(output)
    except Exception as error:
        return f"Database error: {error}"


# --- Per-status tools, registered per deployment -----------------------------
# Unlike the generic tools above (which take a status/tier as an argument and
# filter at query time), these are single-status tools that only get
# registered on a deployment at all if that status's tier is inside its
# ALLOWED_TIERS. tier3only's server therefore never even offers a
# "list_critically_endangered_species" tool -- not just an empty result for
# one -- since Tier 1 is invisible to that endpoint. Each real IUCN status in
# the dataset belongs to exactly one priority tier (see STATUS_TIER in
# ingest_animals_data.py), so this partitions cleanly.
STATUS_TOOL_DEFS = [
    ("Extinct in the wild (EW)", 1, "list_extinct_in_the_wild_species", "Extinct in the Wild"),
    ("Extinct (EX)", 1, "list_extinct_status_species", "Extinct"),
    ("Critically endangered (CR)", 1, "list_critically_endangered_species", "Critically Endangered"),
    ("Endangered (EN)", 2, "list_endangered_species", "Endangered"),
    ("Vulnerable (VU)", 2, "list_vulnerable_species", "Vulnerable"),
    ("Near Threatened (NT)", 2, "list_near_threatened_species", "Near Threatened"),
    ("Data deficient (DD)", 3, "list_data_deficient_species", "Data Deficient"),
    ("Least concern (LC)", 3, "list_least_concern_species", "Least Concern"),
    ("Not evaluated (NE)", 3, "list_not_evaluated_species", "Not Evaluated"),
]


def _make_status_lister(status_label: str, human_label: str):
    def _list_status_species(limit: int = 20) -> str:
        if err := _validate_limit(limit):
            return err
        try:
            scope_sql, scope_params = scope_clause("s.priority_tier")
            with get_db_cursor() as cursor:
                cursor.execute(
                    f"""SELECT s.common_name, s.scientific_name, s.priority_tier
                        FROM species s JOIN conservation_intelligence ci ON ci.species_id = s.id
                        WHERE ci.iucn_status = %s AND {scope_sql}
                        ORDER BY s.common_name LIMIT %s""",
                    (status_label, *scope_params, limit),
                )
                rows = cursor.fetchall()
            if not rows:
                return f"No {human_label} species are available through this endpoint."
            output = [f"### {human_label} species ({MCP_SCOPE})", "| Species | Priority tier |", "| --- | --- |"]
            output.extend(f"| {row['common_name']} (*{row['scientific_name']}*) | {row['priority_tier']} |" for row in rows)
            return "\n".join(output)
        except Exception as error:
            return f"Database error: {error}"
    return _list_status_species


for _status_label, _required_tier, _tool_name, _human_label in STATUS_TOOL_DEFS:
    if _required_tier in ALLOWED_TIERS:
        _fn = _make_status_lister(_status_label, _human_label)
        _fn.__name__ = _tool_name
        mcp.tool(
            name=_tool_name,
            description=(
                f'List every species in this endpoint\'s scope with the exact IUCN status "{_human_label}". '
                f'Use this for a direct question like "give me the {_human_label.lower()} animals", as opposed to '
                f"the broader list_at_risk_species/list_extinct_species or the general-purpose list_species_by_iucn_status."
            ),
        )(_fn)


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

if AUTH_MODE == "oauth":
    from starlette.responses import HTMLResponse, RedirectResponse

    def _login_page(login_id: str, error: bool = False) -> str:
        error_html = "<p style='color:#b00020'>Invalid username or password.</p>" if error else ""
        return f"""<!doctype html><html><body style="font-family: system-ui; max-width: 360px; margin: 80px auto;">
<h2>Biodiversity Wiki -- Sign in</h2>
{error_html}
<form method="post" action="/login">
  <input type="hidden" name="login_id" value="{login_id}">
  <div style="margin-bottom: 10px;"><label>Username<br><input name="username" autofocus style="width: 100%; padding: 6px;"></label></div>
  <div style="margin-bottom: 10px;"><label>Password<br><input name="password" type="password" style="width: 100%; padding: 6px;"></label></div>
  <button type="submit" style="padding: 8px 16px;">Sign in</button>
</form>
</body></html>"""

    async def login_form(request):
        login_id = request.query_params.get("login_id", "")
        error = request.query_params.get("error") == "1"
        if not login_id:
            return HTMLResponse("Missing login_id. Please reconnect the connector.", status_code=400)
        return HTMLResponse(_login_page(login_id, error=error))

    async def login_submit(request):
        form = await request.form()
        login_id = form.get("login_id", "")
        username = form.get("username", "")
        password = form.get("password", "")
        if not oauth_provider.verify_user(username, password):
            return RedirectResponse(f"/login?login_id={login_id}&error=1", status_code=302)
        try:
            redirect_url = await oauth_provider.complete_login(login_id, username.strip())
        except ValueError as error:
            return HTMLResponse(str(error), status_code=400)
        return RedirectResponse(redirect_url, status_code=302)

    app.routes.append(Route("/login", login_form, methods=["GET"]))
    app.routes.append(Route("/login", login_submit, methods=["POST"]))

elif IS_WEB_TRANSPORT:
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
