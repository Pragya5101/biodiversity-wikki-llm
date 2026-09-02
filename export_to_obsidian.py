#!/usr/bin/env python3
"""
export_to_obsidian.py
Python script to query the Wildlife & Biodiversity database and generate
linked Markdown files for an Obsidian Vault, creating a non-linear graph.
"""

import os
import re
import sys
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Database Connection URL
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("Error: DATABASE_URL environment variable is not set.", file=sys.stderr)
    print("Please set it in your environment or a .env file.", file=sys.stderr)
    sys.exit(1)

OUTPUT_DIR = os.environ.get("OBSIDIAN_VAULT_DIR", "./obsidian_vault")

def clean_link(name: str) -> str:
    """Converts a name (e.g., 'Panthera leo' or 'Mara-Serengeti Route') into an Obsidian-friendly filename."""
    # Replace spaces with underscores and remove any special characters not allowed in filenames
    cleaned = re.sub(r'[\/:*?"<>|]', '', name)
    return cleaned.strip().replace(" ", "_")

def get_db_connection():
    """Establishes connection to the PostgreSQL database."""
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def export_vault():
    """Queries database and exports Markdown files."""
    print(f"Connecting to database...")
    try:
        conn = get_db_connection()
    except Exception as e:
        print(f"Database connection failed: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Creating output directory: {OUTPUT_DIR}")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("Clearing stale notes from a previous export...")
    for filename in os.listdir(OUTPUT_DIR):
        if filename.endswith(".md"):
            os.remove(os.path.join(OUTPUT_DIR, filename))

    try:
        with conn.cursor() as cur:
            # 1. Fetch all species and their Tier 3 conservation intelligence
            cur.execute("""
                SELECT 
                    s.id as species_id,
                    s.scientific_name,
                    s.common_name,
                    s.taxonomic_class,
                    s.primary_habitat,
                    s.curation_score,
                    s.priority_tier,
                    ci.iucn_status,
                    ci.poaching_risk_score,
                    ci.protected_breeding_zone,
                    ci.patrol_frequency_days,
                    ci.security_clearance_level,
                    ci.conservation_measures,
                    ci.last_assessment_date
                FROM species s
                LEFT JOIN conservation_intelligence ci ON s.id = ci.species_id
            """)
            species_list = cur.fetchall()

            # 2. Fetch all ecological interactions (Tier 2)
            cur.execute("""
                SELECT 
                    ei.id as interaction_id,
                    s_a.scientific_name as species_a_scientific,
                    s_a.common_name as species_a_common,
                    s_b.scientific_name as species_b_scientific,
                    s_b.common_name as species_b_common,
                    ei.interaction_type,
                    ei.energy_transfer_pathway,
                    ei.interaction_details
                FROM ecological_interactions ei
                JOIN species s_a ON ei.species_a_id = s_a.id
                JOIN species s_b ON ei.species_b_id = s_b.id
            """)
            interactions = cur.fetchall()

            # 3. Fetch all corridors (Tier 2 Network)
            cur.execute("""
                SELECT id, name, geographic_region, corridor_type, length_km, threat_level
                FROM corridors
            """)
            corridors = cur.fetchall()

            # 4. Fetch species-to-corridor links (Tier 2 Network)
            cur.execute("""
                SELECT 
                    sc.species_id,
                    s.scientific_name,
                    sc.corridor_id,
                    c.name as corridor_name
                FROM species_corridors sc
                JOIN species s ON sc.species_id = s.id
                JOIN corridors c ON sc.corridor_id = c.id
            """)
            corridor_links = cur.fetchall()

            # 5. Fetch sightings (Tier 1 Raw Telemetry)
            cur.execute("""
                SELECT 
                    id as sighting_id,
                    species_id,
                    sighting_time,
                    latitude,
                    longitude,
                    sensor_id,
                    battery_level_pct,
                    ambient_temp_c,
                    image_path,
                    notes
                FROM sightings
                ORDER BY sighting_time DESC
            """)
            sightings = cur.fetchall()

    except Exception as e:
        print(f"Error querying database: {e}", file=sys.stderr)
        conn.close()
        sys.exit(1)
    finally:
        conn.close()

    # Organize sightings by species_id
    sightings_by_species = {}
    for s in sightings:
        s_id = s['species_id']
        if s_id not in sightings_by_species:
            sightings_by_species[s_id] = []
        sightings_by_species[s_id].append(s)

    # Organize corridors used by species_id
    corridors_by_species = {}
    species_by_corridor = {}
    for link in corridor_links:
        s_id = link['species_id']
        c_id = link['corridor_id']
        c_name = link['corridor_name']
        s_sci = link['scientific_name']

        if s_id not in corridors_by_species:
            corridors_by_species[s_id] = []
        corridors_by_species[s_id].append(c_name)

        if c_id not in species_by_corridor:
            species_by_corridor[c_id] = []
        species_by_corridor[c_id].append(s_sci)

    # Track interaction connections for species notes
    interactions_by_species = {}
    for inter in interactions:
        sa_sci = inter['species_a_scientific']
        sb_sci = inter['species_b_scientific']
        
        if sa_sci not in interactions_by_species:
            interactions_by_species[sa_sci] = []
        interactions_by_species[sa_sci].append({
            "target": sb_sci,
            "target_common": inter['species_b_common'],
            "role": "Source",
            "type": inter['interaction_type'],
            "pathway": inter['energy_transfer_pathway'],
            "details": inter['interaction_details']
        })

        if sb_sci not in interactions_by_species:
            interactions_by_species[sb_sci] = []
        interactions_by_species[sb_sci].append({
            "target": sa_sci,
            "target_common": inter['species_a_common'],
            "role": "Target",
            "type": inter['interaction_type'],
            "pathway": inter['energy_transfer_pathway'],
            "details": inter['interaction_details']
        })

    # --- Write Corridor Notes ---
    print(f"Exporting corridor notes...")
    for corridor in corridors:
        c_id = corridor['id']
        c_name = corridor['name']
        filename = f"{clean_link(c_name)}.md"
        filepath = os.path.join(OUTPUT_DIR, filename)

        with open(filepath, 'w', encoding='utf-8') as f:
            # YAML Frontmatter
            f.write("---\n")
            f.write(f"type: corridor\n")
            f.write(f"name: \"{c_name}\"\n")
            f.write(f"geographic_region: \"{corridor['geographic_region']}\"\n")
            f.write(f"corridor_type: \"{corridor['corridor_type']}\"\n")
            f.write(f"length_km: {float(corridor['length_km']) if corridor['length_km'] else 'null'}\n")
            f.write(f"threat_level: \"{corridor['threat_level']}\"\n")
            f.write("---\n\n")

            # Content
            f.write(f"# Corridor: {c_name}\n\n")
            f.write(f"**Geographic Region:** {corridor['geographic_region']}  \n")
            f.write(f"**Corridor Type:** {corridor['corridor_type']}  \n")
            f.write(f"**Length:** {corridor['length_km']} km  \n")
            f.write(f"**Threat Level:** {corridor['threat_level']}  \n\n")

            f.write("## Associated Species utilizing this Corridor\n")
            associated = species_by_corridor.get(c_id, [])
            if associated:
                for s_sci in associated:
                    # Find common name
                    common = next((sp['common_name'] for sp in species_list if sp['scientific_name'] == s_sci), s_sci)
                    f.write(f"- [[{clean_link(s_sci)}]] ({common})\n")
            else:
                f.write("No recorded species associated with this corridor in database.\n")

    # --- Write Species Notes ---
    print(f"Exporting species notes...")
    for species in species_list:
        s_id = species['species_id']
        sci_name = species['scientific_name']
        common_name = species['common_name']
        filename = f"{clean_link(sci_name)}.md"
        filepath = os.path.join(OUTPUT_DIR, filename)

        with open(filepath, 'w', encoding='utf-8') as f:
            # YAML Frontmatter
            f.write("---\n")
            f.write(f"type: species\n")
            f.write(f"scientific_name: \"{sci_name}\"\n")
            f.write(f"common_name: \"{common_name}\"\n")
            f.write(f"taxonomic_class: \"{species['taxonomic_class']}\"\n")
            f.write(f"primary_habitat: \"{species['primary_habitat']}\"\n")
            f.write(f"curation_score: {species['curation_score']}\n")
            f.write(f"priority_tier: {species['priority_tier']}\n")
            if species['iucn_status']:
                f.write(f"iucn_status: \"{species['iucn_status']}\"\n")
                f.write(f"poaching_risk_score: {species['poaching_risk_score']}\n")
                f.write(f"protected_breeding_zone: \"{species['protected_breeding_zone']}\"\n")
                f.write(f"patrol_frequency_days: {species['patrol_frequency_days']}\n")
                f.write(f"security_clearance: \"{species['security_clearance_level']}\"\n")
                f.write(f"last_assessment_date: \"{species['last_assessment_date']}\"\n")
            f.write("---\n\n")

            # Note Heading
            f.write(f"# {common_name} (*{sci_name}*)\n\n")
            f.write(f"**Taxonomic Class:** {species['taxonomic_class']}  \n")
            f.write(f"**Primary Habitat:** {species['primary_habitat']}  \n\n")
            f.write(f"**Curation Score:** {species['curation_score']}/100\n")
            f.write(f"**Priority Tier:** {species['priority_tier']}\n\n")

            # Tier 3 - Conservation Intelligence
            f.write("## 🛡️ Tier 3: Conservation Intelligence\n")
            if species['iucn_status']:
                f.write(f"- **IUCN Status:** {species['iucn_status']}\n")
                f.write(f"- **Poaching Risk Score:** {species['poaching_risk_score']}/10\n")
                f.write(f"- **Protected Breeding Zone:** {species['protected_breeding_zone']}\n")
                f.write(f"- **Ranger Patrol Frequency:** Every {species['patrol_frequency_days']} days\n")
                f.write(f"- **Security Clearance Requirement:** `{species['security_clearance_level']}`\n")
                if species['conservation_measures']:
                    f.write(f"\n### Conservation Measures & Protocols\n")
                    f.write(f"{species['conservation_measures']}\n")
            else:
                f.write("*No conservation intelligence data registered for this species.*\n")
            f.write("\n")

            # Tier 2 - Relational Network & Ecological Interactions
            f.write("## 🕸️ Tier 2: Relational Network & Ecological Interactions\n")
            
            # Sub-section: Interactions
            f.write("### Species Interactions\n")
            s_inters = interactions_by_species.get(sci_name, [])
            if s_inters:
                for inter in s_inters:
                    target_note = clean_link(inter['target'])
                    direction_str = "acts on" if inter['role'] == "Source" else "interacts with"
                    f.write(f"- **{inter['type']}** relationship with [[{target_note}]] ({inter['target_common']})\n")
                    f.write(f"  - *Energy Pathway:* {inter['pathway']}\n")
                    f.write(f"  - *Details:* {inter['details']}\n")
            else:
                f.write("*No interactions documented.*\n")
            f.write("\n")

            # Sub-section: Corridors Used
            f.write("### Connected Corridors\n")
            s_corridors = corridors_by_species.get(s_id, [])
            if s_corridors:
                for corridor_name in s_corridors:
                    f.write(f"- [[{clean_link(corridor_name)}]]\n")
            else:
                f.write("*No migration or buffer corridors linked to this species.*\n")
            f.write("\n")

            # Tier 1 - Raw Observation & Telemetry
            f.write("## 📡 Tier 1: Raw Observation & Telemetry Data\n")
            s_sightings = sightings_by_species.get(s_id, [])
            if s_sightings:
                f.write("| Date/Time | Location (Lat, Long) | Sensor ID | Temp (°C) | Battery | Image Reference | Observations / Notes |\n")
                f.write("| --- | --- | --- | --- | --- | --- | --- |\n")
                for sght in s_sightings:
                    time_str = sght['sighting_time'].strftime("%Y-%m-%d %H:%M UTC")
                    battery = f"{sght['battery_level_pct']}%" if sght['battery_level_pct'] is not None else "N/A"
                    temp = f"{sght['ambient_temp_c']}°C" if sght['ambient_temp_c'] is not None else "N/A"
                    img_ref = f"[[{sght['image_path']}]]" if sght['image_path'] else "N/A"
                    f.write(f"| {time_str} | {float(sght['latitude']):.4f}, {float(sght['longitude']):.4f} | `{sght['sensor_id']}` | {temp} | {battery} | {img_ref} | {sght['notes']} |\n")
            else:
                f.write("*No telemetry observations logged.*\n")

    print(f"Export completed successfully. Generated {len(species_list) + len(corridors)} markdown notes in '{OUTPUT_DIR}'.")

if __name__ == "__main__":
    export_vault()
