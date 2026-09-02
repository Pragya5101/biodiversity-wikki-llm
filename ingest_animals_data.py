#!/usr/bin/env python3
"""
ingest_animals_data.py
Ingest a real-world species reference CSV (Name, Kingdom, ..., Distribution,
Habits, Diet, Mating_Habits, Population as stringified Python dict/list
literals) into the 3-tier PostgreSQL biodiversity wiki.

The CSV has no GPS/timestamp occurrence records, so Tier 1 "sightings" are
still lightly synthesized from each species' real continent of distribution.
Tiers are derived from each species' real IUCN-style conservation status
(the 'Population status' key inside the Population column), not from a
fabricated poaching score.
"""

import os
import re
import sys
import csv
import ast
import random
import argparse
from datetime import datetime, timedelta

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("Error: DATABASE_URL environment variable is not set.", file=sys.stderr)
    print("Please set it in your environment or a .env file.", file=sys.stderr)
    sys.exit(1)

# Maps a real IUCN-style status to (curation_score, priority_tier), using the
# same tier boundaries documented in schema.sql / README.md (1: 70-100,
# 2: 40-69, 3: 1-39). Extinct/Extinct-in-the-wild/Critically-endangered
# species are the highest-priority curated records; Least-concern/
# Data-deficient/unassessed species are the lowest-priority, Tier 3 records.
STATUS_TIER = {
    "Extinct in the wild (EW)": (95, 1),
    "Extinct (EX)": (90, 1),
    "Critically endangered (CR)": (85, 1),
    "Endangered (EN)": (65, 2),
    "Vulnerable (VU)": (55, 2),
    "Near Threatened (NT)": (45, 2),
    "Data deficient (DD)": (30, 3),
    "Least concern (LC)": (20, 3),
    "Not evaluated (NE)": (15, 3),
    None: (15, 3),
}

# A 1-10 severity used only to derive patrol frequency and note tone; this
# dataset has no real poaching data, so it is a heuristic scaled off the
# real conservation status, not a measured quantity.
STATUS_SEVERITY = {
    "Extinct in the wild (EW)": 8,
    "Extinct (EX)": 10,
    "Critically endangered (CR)": 9,
    "Endangered (EN)": 7,
    "Vulnerable (VU)": 5,
    "Near Threatened (NT)": 4,
    "Data deficient (DD)": 2,
    "Least concern (LC)": 2,
    "Not evaluated (NE)": 2,
    None: 2,
}

# How many rows to keep from each status bucket when narrowing the ~28,500
# usable rows down to a demo-sized wiki. "Extinct in the wild" is rare and
# significant enough to keep in full; the rest are capped/sampled.
SELECTION_TARGETS = {
    "Extinct in the wild (EW)": None,  # None = keep all
    "Extinct (EX)": 60,
    "Critically endangered (CR)": 150,
    "Endangered (EN)": 30,
    "Vulnerable (VU)": 30,
    "Near Threatened (NT)": 25,
    "Least concern (LC)": 30,
    "Data deficient (DD)": 10,
    "Not evaluated (NE)": 5,
    None: 5,
}

CONTINENT_COORDS = {
    "Africa": ((-30.0, 20.0), (10.0, 40.0)),
    "Asia": ((5.0, 55.0), (60.0, 140.0)),
    "Europe": ((40.0, 65.0), (-10.0, 40.0)),
    "North America": ((15.0, 60.0), (-130.0, -60.0)),
    "South America": ((-40.0, 10.0), (-80.0, -35.0)),
    "Oceania": ((-45.0, -10.0), (115.0, 180.0)),
    "Antarctica": ((-80.0, -65.0), (-60.0, 60.0)),
}

CORRIDOR_BY_CONTINENT = {
    "Africa": "Serengeti Migration Corridor",
    "South America": "Amazon-Guiana Ecological Corridor",
    "North America": "Yukon-Rocky Mountain Wilderness Corridor",
    "Oceania": "Great Barrier Coast Buffer Zone",
    "Europe": "Eurasian Forest Migration Network",
    "Asia": "Eurasian Forest Migration Network",
}

CORRIDOR_DATA = [
    ("Serengeti Migration Corridor", "East Africa (Kenya/Tanzania)", "Migratory Corridor", 150.0, "Medium"),
    ("Amazon-Guiana Ecological Corridor", "South America (Brazil/Venezuela)", "Protected Forest Zone", 850.0, "Low"),
    ("Yukon-Rocky Mountain Wilderness Corridor", "North America (Canada/USA)", "Protected Buffer", 650.0, "Low"),
    ("Great Barrier Coast Buffer Zone", "Australia/Queensland", "Coastal Riparian Zone", 420.0, "High"),
    ("Eurasian Forest Migration Network", "Northern Europe & Asia", "Migratory Corridor", 1200.0, "Medium"),
    ("Sahara Transit Buffer Zone", "North Africa", "Arid Transit Corridor", 500.0, "High"),
]


def parse_dict_field(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        return ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return {}


def load_rows(csv_path: str) -> list[dict]:
    with open(csv_path, encoding="utf-8", errors="replace", newline="") as f:
        rows = list(csv.DictReader(f))
    # Family/genus-level placeholder rows (e.g. Species="Equidae") have no
    # space in the Species column; real binomial names always do.
    return [r for r in rows if " " in r["Species"].strip()]


def status_of(row: dict) -> str | None:
    return parse_dict_field(row["Population"]).get("Population status")


def select_species(rows: list[dict], seed: int = 42) -> list[dict]:
    rnd = random.Random(seed)
    by_status: dict[str | None, list[dict]] = {}
    for row in rows:
        by_status.setdefault(status_of(row), []).append(row)

    selected: list[dict] = []
    seen_scientific_names: set[str] = set()
    for status, cap in SELECTION_TARGETS.items():
        pool = by_status.get(status, [])
        chosen = pool if cap is None else rnd.sample(pool, min(cap, len(pool)))
        for row in chosen:
            sci_name = row["Species"].strip()
            if sci_name in seen_scientific_names:
                continue
            seen_scientific_names.add(sci_name)
            selected.append(row)
    return selected


def primary_continent(row: dict) -> str | None:
    continents = parse_dict_field(row["Distribution"]).get("Geography", {}).get("Continents", "")
    first = continents.split(",")[0].strip()
    return first or None


def primary_habitat(row: dict) -> str:
    biomes = parse_dict_field(row["Distribution"]).get("Biome")
    if biomes:
        return str(biomes[0]).strip()
    lifestyle = parse_dict_field(row["Habits"]).get("Lifestyle", "")
    if lifestyle:
        return lifestyle.split(",")[0].strip()
    return "Unknown habitat"


def protected_zone(row: dict) -> str:
    geo = parse_dict_field(row["Distribution"]).get("Geography", {})
    countries = geo.get("Countries", "")
    first_country = countries.split(",")[0].strip()
    return f"{first_country} range" if first_country else "Range not specified"


def conservation_measures(row: dict) -> str:
    pop = parse_dict_field(row["Population"])
    trend = pop.get("Population trend", "Unknown")
    diet = (row["Diet"] or "Unknown").strip().strip(",")
    lifestyle = parse_dict_field(row["Habits"]).get("Lifestyle", "Unknown")
    return f"Population trend: {trend}. Primary diet: {diet}. Typical lifestyle: {lifestyle}."


def generate_gps(continent: str | None):
    lat_range, lon_range = CONTINENT_COORDS.get(continent, CONTINENT_COORDS["Africa"])
    lat = random.uniform(*lat_range)
    lon = random.uniform(*lon_range)
    return lat, lon


_WEIGHT_UNIT_TO_KG = {"g": 0.001, "kg": 1.0, "t": 1000.0}


def parse_weight_kg(weight_str: str) -> float | None:
    """Rough kg estimate from strings like '65-306 kg', '100-160 t', '16-60 kg'.
    Averages a range when present; returns None when nothing parseable."""
    weight_str = (weight_str or "").lower()
    numbers = [float(n) for n in re.findall(r"[\d.]+", weight_str)]
    if not numbers:
        return None
    unit = next((u for u in ("kg", "g", "t") if re.search(rf"\b{u}\b", weight_str)), "kg")
    avg = sum(numbers) / len(numbers)
    return avg * _WEIGHT_UNIT_TO_KG[unit]


def is_predator_diet(diet: str) -> bool:
    diet = (diet or "").lower()
    return any(term in diet for term in ("carnivore", "piscivor", "insectivor"))


def is_prey_diet(diet: str) -> bool:
    diet = (diet or "").lower()
    return any(term in diet for term in ("herbivore", "folivor", "frugivor", "granivor", "omnivore"))


def main():
    parser = argparse.ArgumentParser(description="Ingest a real species-info CSV into the PostgreSQL biodiversity wiki.")
    parser.add_argument("--csv-path", required=True, help="Path to the animals_info.csv (or similarly shaped) file.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for species sampling (default 42, for reproducible selection).")
    args = parser.parse_args()

    if not os.path.exists(args.csv_path):
        print(f"Error: CSV file not found at {args.csv_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Loading {args.csv_path}...")
    rows = load_rows(args.csv_path)
    print(f"Loaded {len(rows)} rows with a real binomial species name.")

    selected = select_species(rows, seed=args.seed)
    print(f"Selected {len(selected)} species for the wiki.")

    print("Connecting to PostgreSQL...")
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        print("Initializing database schema from schema.sql...")
        with open("schema.sql", "r", encoding="utf-8") as f:
            cur.execute(f.read())

        print("Clearing existing tables...")
        cur.execute(
            "TRUNCATE TABLE conservation_intelligence, private_notes, species_corridors, "
            "corridors, ecological_interactions, sightings, species RESTART IDENTITY CASCADE;"
        )

        print("Ingesting corridors...")
        corridor_map = {}
        for name, region, ctype, length, threat in CORRIDOR_DATA:
            cur.execute(
                "INSERT INTO corridors (name, geographic_region, corridor_type, length_km, threat_level) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id;",
                (name, region, ctype, length, threat),
            )
            corridor_map[name] = cur.fetchone()[0]

        print(f"Ingesting {len(selected)} species...")
        species_map = {}  # scientific_name -> (id, diet, continent)
        for row in selected:
            sci_name = row["Species"].strip()
            common_name = row["Name"].strip()
            taxonomic_class = row["Class"].strip() or "Unknown"
            habitat = primary_habitat(row)
            status = status_of(row)
            curation_score, priority_tier = STATUS_TIER[status]

            cur.execute(
                "INSERT INTO species (scientific_name, common_name, taxonomic_class, primary_habitat, "
                "curation_score, priority_tier) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id;",
                (sci_name, common_name, taxonomic_class, habitat, curation_score, priority_tier),
            )
            species_id = cur.fetchone()[0]
            species_map[sci_name] = {
                "id": species_id,
                "common_name": common_name,
                "diet": row["Diet"],
                "continent": primary_continent(row),
                "taxonomic_class": taxonomic_class,
                "priority_tier": priority_tier,
                "weight_kg": parse_weight_kg(row["Weight"]),
            }

            severity = STATUS_SEVERITY[status]
            cur.execute(
                "INSERT INTO conservation_intelligence (species_id, iucn_status, poaching_risk_score, "
                "protected_breeding_zone, patrol_frequency_days, last_assessment_date, conservation_measures) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s);",
                (
                    species_id,
                    status or "Not evaluated (NE)",
                    severity,
                    protected_zone(row),
                    max(1, 14 - severity),
                    datetime.date(datetime.now() - timedelta(days=random.randint(10, 365))),
                    conservation_measures(row),
                ),
            )

            if priority_tier == 3:
                pop = parse_dict_field(row["Population"])
                trend = pop.get("Population trend", "unknown")
                cur.execute(
                    "INSERT INTO private_notes (species_id, note) VALUES (%s, %s);",
                    (
                        species_id,
                        f"Internal curator note: {common_name} population trend recorded as '{trend}'. "
                        f"Field observations pending independent verification. Do not cite in public-facing materials.",
                    ),
                )

            continent = primary_continent(row)
            corridor_name = CORRIDOR_BY_CONTINENT.get(continent)
            if corridor_name:
                cur.execute(
                    "INSERT INTO species_corridors (species_id, corridor_id) VALUES (%s, %s) ON CONFLICT DO NOTHING;",
                    (species_id, corridor_map[corridor_name]),
                )

        print("Generating ecological interactions from real diet data...")
        predators = [s for s in species_map.values() if is_predator_diet(s["diet"])]
        prey_pool = [s for s in species_map.values() if is_prey_diet(s["diet"])]
        interactions_to_insert = []
        def size_plausible(predator, prey) -> bool:
            # When both weights are known, a predator shouldn't be wildly
            # smaller than its prey (no tarsiers preying on gaur). Missing
            # weight data doesn't block a match, since it's common in this CSV.
            pw, qw = predator["weight_kg"], prey["weight_kg"]
            if pw is None or qw is None:
                return True
            return qw <= pw * 15

        for predator in predators:
            # Restrict candidates to the same taxonomic class and continent so
            # links stay biologically plausible (no snakes preying on gorillas),
            # plus a rough size sanity check.
            candidates = [
                p for p in prey_pool
                if p["id"] != predator["id"]
                and p["taxonomic_class"] == predator["taxonomic_class"]
                and p["continent"] == predator["continent"]
                and size_plausible(predator, p)
            ]
            if not candidates:
                continue
            targets = random.sample(candidates, min(len(candidates), random.randint(1, 3)))
            for prey in targets:
                interactions_to_insert.append((
                    predator["id"], prey["id"], "Predation", "Diet-inferred trophic link",
                    f"{predator['common_name']} ({predator['diet'].strip(', ')}) preys on species matching its dietary profile, "
                    f"including {prey['common_name']}.",
                ))
        if interactions_to_insert:
            execute_values(
                cur,
                "INSERT INTO ecological_interactions (species_a_id, species_b_id, interaction_type, "
                "energy_transfer_pathway, interaction_details) VALUES %s;",
                interactions_to_insert,
            )

        print("Generating lightweight synthetic sightings (no real occurrence data in this dataset)...")
        sighting_count = 0
        for sci_name, info in species_map.items():
            for _ in range(random.randint(1, 2)):
                lat, lon = generate_gps(info["continent"])
                sighting_time = datetime.now() - timedelta(days=random.randint(0, 60), hours=random.randint(0, 23))
                cur.execute(
                    "INSERT INTO sightings (species_id, sighting_time, latitude, longitude, sensor_id, "
                    "battery_level_pct, ambient_temp_c, image_path, notes) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);",
                    (
                        info["id"],
                        sighting_time,
                        lat,
                        lon,
                        f"FIELD-CAM-{random.randint(100, 999)}",
                        round(random.uniform(15.0, 100.0), 1),
                        round(random.uniform(-10.0, 40.0), 1),
                        None,
                        f"Simulated field observation within {info['continent'] or 'unspecified'} range (no real telemetry source for this dataset).",
                    ),
                )
                sighting_count += 1

        conn.commit()
        tier_counts = {1: 0, 2: 0, 3: 0}
        for info in species_map.values():
            tier_counts[info["priority_tier"]] += 1
        print("Ingestion completed successfully!")
        print(f"Total species inserted: {len(species_map)}")
        print(f"  Tier 1: {tier_counts[1]}  Tier 2: {tier_counts[2]}  Tier 3: {tier_counts[3]}")
        print(f"Total corridors inserted: {len(corridor_map)}")
        print(f"Total ecological interactions inserted: {len(interactions_to_insert)}")
        print(f"Total synthetic sightings inserted: {sighting_count}")

    except Exception as e:
        conn.rollback()
        print(f"Ingestion failed due to database transaction error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
