#!/usr/bin/env python3
"""
ingest_kaggle_data.py
Python script to ingest the Kaggle Wildlife YOLO dataset into the 3-Tier PostgreSQL database.
Supports parsing actual YOLO annotation files and generating realistic telemetry/ecological data.
"""

import os
import sys
import glob
import random
import argparse
from datetime import datetime, timedelta
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

# Database URL configuration
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("Error: DATABASE_URL environment variable is not set.", file=sys.stderr)
    print("Please set it in your environment or a .env file.", file=sys.stderr)
    sys.exit(1)

# List of 54 classes as defined in the banuprasadb/wildlife-dataset data.yaml
SPECIES_CLASSES = [
    "Zebra", "Lion", "Leopard", "Cheetah", "Tiger", "Bear", "Butterfly", "Canary", 
    "Crocodile", "Bull", "Camel", "Centipede", "Caterpillar", "Duck", "Squirrel", 
    "Spider", "Ladybug", "Elephant", "Horse", "Fox", "Tortoise", "Frog", "Kangaroo", 
    "Deer", "Eagle", "Monkey", "Snake", "Owl", "Swan", "Goat", "Rabbit", "Giraffe", 
    "Goose", "PolarBear", "Raven", "Hippopotamus", "BrownBear", "Rhinoceros", 
    "Woodpecker", "Sheep", "Magpie", "Ostrich", "Jaguar", "Hedgehog", "Turkey", 
    "Raccoon", "Worm", "Harbor", "Panda", "RedPanda", "Otter", "Lynx", "Scorpion", 
    "Koala"
]

# Taxonomic and ecological mapping for the 54 classes to populate tables
SPECIES_METADATA = {
    "Zebra": {"sci": "Equus quagga", "class": "Mammalia", "habitat": "Savannah", "iucn": "Near Threatened (NT)", "poach": 3, "zone": "Serengeti Plains Zone B"},
    "Lion": {"sci": "Panthera leo", "class": "Mammalia", "habitat": "Savannah", "iucn": "Vulnerable (VU)", "poach": 7, "zone": "Serengeti Core Area A"},
    "Leopard": {"sci": "Panthera pardus", "class": "Mammalia", "habitat": "Savannah", "iucn": "Vulnerable (VU)", "poach": 6, "zone": "Kruger South Sector"},
    "Cheetah": {"sci": "Acinonyx jubatus", "class": "Mammalia", "habitat": "Savannah", "iucn": "Vulnerable (VU)", "poach": 5, "zone": "Lolkisale Protected Range"},
    "Tiger": {"sci": "Panthera tigris", "class": "Mammalia", "habitat": "Forest", "iucn": "Endangered (EN)", "poach": 10, "zone": "Ranthambore Tiger Reserve A"},
    "Bear": {"sci": "Ursus arctos", "class": "Mammalia", "habitat": "Forest", "iucn": "Least Concern (LC)", "poach": 3, "zone": "Rocky Mountain North Buffer"},
    "Butterfly": {"sci": "Lepidoptera", "class": "Insecta", "habitat": "Grasslands", "iucn": "Least Concern (LC)", "poach": 1, "zone": "Eurasian Wildflower Corridor"},
    "Canary": {"sci": "Serinus canaria", "class": "Aves", "habitat": "Forest", "iucn": "Least Concern (LC)", "poach": 1, "zone": "Macaronesian Forest Zone"},
    "Crocodile": {"sci": "Crocodylinae", "class": "Reptilia", "habitat": "Wetlands", "iucn": "Vulnerable (VU)", "poach": 6, "zone": "Kakadu Wetlands Area B"},
    "Bull": {"sci": "Bos taurus", "class": "Mammalia", "habitat": "Grasslands", "iucn": "Least Concern (LC)", "poach": 1, "zone": "Eurasian Grazing Range"},
    "Camel": {"sci": "Camelus dromedarius", "class": "Mammalia", "habitat": "Desert", "iucn": "Least Concern (LC)", "poach": 2, "zone": "Sahara Transit Buffer"},
    "Centipede": {"sci": "Chilopoda", "class": "Chilopoda", "habitat": "Forest", "iucn": "Least Concern (LC)", "poach": 1, "zone": "Amazon Floor Reserve"},
    "Caterpillar": {"sci": "Lepidoptera larvae", "class": "Insecta", "habitat": "Forest", "iucn": "Least Concern (LC)", "poach": 1, "zone": "Eurasian Forest Core"},
    "Duck": {"sci": "Anas platyrhynchos", "class": "Aves", "habitat": "Wetlands", "iucn": "Least Concern (LC)", "poach": 1, "zone": "Great Lakes Marsh Zone"},
    "Squirrel": {"sci": "Sciuridae", "class": "Mammalia", "habitat": "Forest", "iucn": "Least Concern (LC)", "poach": 1, "zone": "Eurasian Forest Core"},
    "Spider": {"sci": "Araneae", "class": "Arachnida", "habitat": "Forest", "iucn": "Least Concern (LC)", "poach": 1, "zone": "Amazon Canopy Reserve"},
    "Ladybug": {"sci": "Coccinellidae", "class": "Insecta", "habitat": "Grasslands", "iucn": "Least Concern (LC)", "poach": 1, "zone": "Eurasian Wildflower Corridor"},
    "Elephant": {"sci": "Loxodonta africana", "class": "Mammalia", "habitat": "Savannah", "iucn": "Endangered (EN)", "poach": 10, "zone": "Tsavo East High Security Zone"},
    "Horse": {"sci": "Equus caballus", "class": "Mammalia", "habitat": "Grasslands", "iucn": "Least Concern (LC)", "poach": 1, "zone": "Eurasian Grazing Range"},
    "Fox": {"sci": "Vulpes vulpes", "class": "Mammalia", "habitat": "Forest", "iucn": "Least Concern (LC)", "poach": 2, "zone": "Yukon Protected Buffer"},
    "Tortoise": {"sci": "Testudinidae", "class": "Reptilia", "habitat": "Savannah", "iucn": "Vulnerable (VU)", "poach": 5, "zone": "Galapagos Shield Zone C"},
    "Frog": {"sci": "Anura", "class": "Amphibia", "habitat": "Wetlands", "iucn": "Least Concern (LC)", "poach": 1, "zone": "Queensland Riparian Sanctuary"},
    "Kangaroo": {"sci": "Macropodidae", "class": "Mammalia", "habitat": "Outback", "iucn": "Least Concern (LC)", "poach": 2, "zone": "New South Wales Plains"},
    "Deer": {"sci": "Cervidae", "class": "Mammalia", "habitat": "Forest", "iucn": "Least Concern (LC)", "poach": 3, "zone": "Yukon Protected Buffer"},
    "Eagle": {"sci": "Accipitridae", "class": "Aves", "habitat": "Forest", "iucn": "Least Concern (LC)", "poach": 4, "zone": "Yukon Mountain Crags"},
    "Monkey": {"sci": "Cercopithecidae", "class": "Mammalia", "habitat": "Forest", "iucn": "Least Concern (LC)", "poach": 3, "zone": "Amazon Canopy Reserve"},
    "Snake": {"sci": "Serpentes", "class": "Reptilia", "habitat": "Forest", "iucn": "Least Concern (LC)", "poach": 3, "zone": "Amazon Floor Reserve"},
    "Owl": {"sci": "Strigiformes", "class": "Aves", "habitat": "Forest", "iucn": "Least Concern (LC)", "poach": 2, "zone": "Yukon Mountain Crags"},
    "Swan": {"sci": "Cygnus cygnus", "class": "Aves", "habitat": "Wetlands", "iucn": "Least Concern (LC)", "poach": 2, "zone": "Great Lakes Marsh Zone"},
    "Goat": {"sci": "Capra hircus", "class": "Mammalia", "habitat": "Grasslands", "iucn": "Least Concern (LC)", "poach": 1, "zone": "Eurasian Grazing Range"},
    "Rabbit": {"sci": "Oryctolagus cuniculus", "class": "Mammalia", "habitat": "Grasslands", "iucn": "Least Concern (LC)", "poach": 1, "zone": "Eurasian Wildflower Corridor"},
    "Giraffe": {"sci": "Giraffa camelopardalis", "class": "Mammalia", "habitat": "Savannah", "iucn": "Vulnerable (VU)", "poach": 5, "zone": "Serengeti Plains Zone B"},
    "Goose": {"sci": "Anser anser", "class": "Aves", "habitat": "Wetlands", "iucn": "Least Concern (LC)", "poach": 1, "zone": "Great Lakes Marsh Zone"},
    "PolarBear": {"sci": "Ursus maritimus", "class": "Mammalia", "habitat": "Arctic Marine", "iucn": "Vulnerable (VU)", "poach": 4, "zone": "Svalbard Ice Reserve Alpha"},
    "Raven": {"sci": "Corvus corax", "class": "Aves", "habitat": "Forest", "iucn": "Least Concern (LC)", "poach": 1, "zone": "Yukon Mountain Crags"},
    "Hippopotamus": {"sci": "Hippopotamus amphibius", "class": "Mammalia", "habitat": "Wetlands", "iucn": "Vulnerable (VU)", "poach": 6, "zone": "Mara River Hippocamp Zone"},
    "BrownBear": {"sci": "Ursus arctos horribilis", "class": "Mammalia", "habitat": "Forest", "iucn": "Least Concern (LC)", "poach": 3, "zone": "Yukon Protected Buffer"},
    "Rhinoceros": {"sci": "Diceros bicornis", "class": "Mammalia", "habitat": "Savannah", "iucn": "Critically Endangered (CR)", "poach": 10, "zone": "Ol Pejeta Rhino Sanctuary"},
    "Woodpecker": {"sci": "Picidae", "class": "Aves", "habitat": "Forest", "iucn": "Least Concern (LC)", "poach": 1, "zone": "Eurasian Forest Core"},
    "Sheep": {"sci": "Ovis aries", "class": "Mammalia", "habitat": "Grasslands", "iucn": "Least Concern (LC)", "poach": 1, "zone": "Eurasian Grazing Range"},
    "Magpie": {"sci": "Pica pica", "class": "Aves", "habitat": "Forest", "iucn": "Least Concern (LC)", "poach": 1, "zone": "Eurasian Forest Core"},
    "Ostrich": {"sci": "Struthio camelus", "class": "Mammalia", "habitat": "Savannah", "iucn": "Least Concern (LC)", "poach": 2, "zone": "Serengeti Plains Zone B"},
    "Jaguar": {"sci": "Panthera onca", "class": "Mammalia", "habitat": "Forest", "iucn": "Near Threatened (NT)", "poach": 8, "zone": "Pantanal Reserve Sector Delta"},
    "Hedgehog": {"sci": "Erinaceinae", "class": "Mammalia", "habitat": "Forest", "iucn": "Least Concern (LC)", "poach": 1, "zone": "Eurasian Forest Core"},
    "Turkey": {"sci": "Meleagris gallopavo", "class": "Aves", "habitat": "Forest", "iucn": "Least Concern (LC)", "poach": 1, "zone": "Yukon Protected Buffer"},
    "Raccoon": {"sci": "Procyon lotor", "class": "Mammalia", "habitat": "Forest", "iucn": "Least Concern (LC)", "poach": 2, "zone": "Rocky Mountain North Buffer"},
    "Worm": {"sci": "Lumbricina", "class": "Clitellata", "habitat": "Forest", "iucn": "Least Concern (LC)", "poach": 1, "zone": "Eurasian Forest Core"},
    "Harbor": {"sci": "Phoca vitulina", "class": "Mammalia", "habitat": "Coastal Marine", "iucn": "Least Concern (LC)", "poach": 3, "zone": "Maine Coast Seal Harbor"},
    "Panda": {"sci": "Ailuropoda melanoleuca", "class": "Mammalia", "habitat": "Forest", "iucn": "Vulnerable (VU)", "poach": 5, "zone": "Sichuan Bamboo Reserves"},
    "RedPanda": {"sci": "Ailurus fulgens", "class": "Mammalia", "habitat": "Forest", "iucn": "Endangered (EN)", "poach": 7, "zone": "Himalayan Forest Sanctuary"},
    "Otter": {"sci": "Lutrinae", "class": "Mammalia", "habitat": "Wetlands", "iucn": "Least Concern (LC)", "poach": 3, "zone": "Queensland Riparian Sanctuary"},
    "Lynx": {"sci": "Lynx lynx", "class": "Mammalia", "habitat": "Forest", "iucn": "Least Concern (LC)", "poach": 4, "zone": "Yukon Protected Buffer"},
    "Scorpion": {"sci": "Scorpiones", "class": "Arachnida", "habitat": "Desert", "iucn": "Least Concern (LC)", "poach": 1, "zone": "Sahara Transit Buffer"},
    "Koala": {"sci": "Phascolarctos cinereus", "class": "Mammalia", "habitat": "Outback", "iucn": "Vulnerable (VU)", "poach": 2, "zone": "Blue Mountains Sanctuary"}
}

# Coordinate bounding boxes for generating realistic latitude/longitude based on species primary habitat
HABITAT_GEOGRAPHY = {
    "Savannah": {"lat_range": (-3.0, -1.0), "lon_range": (34.0, 36.0)},       # East Africa (Serengeti)
    "Forest": {"lat_range": (-5.0, -2.0), "lon_range": (-65.0, -60.0)},       # Amazon Rainforest
    "Grasslands": {"lat_range": (45.0, 50.0), "lon_range": (10.0, 20.0)},      # Europe
    "Wetlands": {"lat_range": (-13.0, -11.0), "lon_range": (131.0, 133.0)},   # Northern Australia (Kakadu)
    "Desert": {"lat_range": (20.0, 25.0), "lon_range": (15.0, 25.0)},         # Sahara Desert
    "Outback": {"lat_range": (-34.0, -32.0), "lon_range": (148.0, 151.0)},    # SE Australia
    "Arctic Marine": {"lat_range": (78.0, 80.0), "lon_range": (15.0, 25.0)},  # Svalbard
    "Coastal Marine": {"lat_range": (43.0, 45.0), "lon_range": (-70.0, -68.0)} # US Maine Coast
}

def generate_gps(habitat: str):
    """Generates coordinates based on habitat geographic zones."""
    geo = HABITAT_GEOGRAPHY.get(habitat, HABITAT_GEOGRAPHY["Forest"])
    lat = random.uniform(geo["lat_range"][0], geo["lat_range"][1])
    lon = random.uniform(geo["lon_range"][0], geo["lon_range"][1])
    return lat, lon

def create_dummy_dataset(base_dir: str):
    """Creates a dummy YOLO dataset with 20 label files and fake images for testing ingestion."""
    print("Kaggle dataset directory not found. Creating a dummy dataset to test ingestion...")
    images_dir = os.path.join(base_dir, "train", "images")
    labels_dir = os.path.join(base_dir, "train", "labels")
    os.makedirs(images_dir, exist_ok=True)
    os.makedirs(labels_dir, exist_ok=True)
    
    # Write data.yaml mapping
    with open(os.path.join(base_dir, "data.yaml"), "w") as f:
        f.write("train: ./train/images\n")
        f.write("val: ./valid/images\n\n")
        f.write("nc: 54\n")
        f.write(f"names: {str(SPECIES_CLASSES)}\n")

    # Write some mock text files in train/labels/ and touch image files
    for i in range(1, 21):
        filename = f"telemetry_cam_{i:03d}"
        
        # Write labels file
        # YOLO format: class_id x_center y_center width height
        class_id = random.randint(0, 53)
        with open(os.path.join(labels_dir, f"{filename}.txt"), "w") as f:
            f.write(f"{class_id} {random.random():.4f} {random.random():.4f} {random.random():.4f} {random.random():.4f}\n")
            # Occasional multi-species photo
            if random.random() > 0.7:
                other_class = random.randint(0, 53)
                f.write(f"{other_class} {random.random():.4f} {random.random():.4f} {random.random():.4f} {random.random():.4f}\n")
                
        # Create empty image file
        with open(os.path.join(images_dir, f"{filename}.jpg"), "wb") as f:
            f.write(b"") # Empty file representation

    print(f"Created dummy dataset in directory: {base_dir}")

def main():
    parser = argparse.ArgumentParser(description="Ingest Kaggle Wildlife YOLO dataset into PostgreSQL.")
    parser.add_argument("--dataset-dir", type=str, default="./dummy_wildlife_dataset",
                        help="Path to the unzipped Kaggle dataset folder. Defaults to creating a dummy directory if missing.")
    args = parser.parse_args()

    # Create dummy dataset if no real dataset is found
    if not os.path.exists(args.dataset_dir):
        create_dummy_dataset(args.dataset_dir)

    print("Connecting to PostgreSQL...")
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        # 0. Initialize schema automatically
        print("Initializing database schema from schema.sql...")
        with open("schema.sql", "r", encoding="utf-8") as f:
            schema_sql = f.read()
            cur.execute(schema_sql)

        # 1. TRUNCATE tables to reset
        print("Clearing existing tables...")
        cur.execute("TRUNCATE TABLE conservation_intelligence, species_corridors, corridors, ecological_interactions, sightings, species RESTART IDENTITY CASCADE;")

        # 2. Insert Species master entries
        print("Ingesting 54 Species classes...")
        species_map = {}  # maps Class Name -> species DB id
        
        for name in SPECIES_CLASSES:
            meta = SPECIES_METADATA.get(name, {"sci": f"Unknown {name}", "class": "Mammalia", "habitat": "Forest"})
            cur.execute("""
                INSERT INTO species (scientific_name, common_name, taxonomic_class, primary_habitat)
                VALUES (%s, %s, %s, %s) RETURNING id;
            """, (meta["sci"], name, meta["class"], meta["habitat"]))
            species_map[name] = cur.fetchone()[0]

        # 3. Insert Tier 3 Conservation Intelligence
        print("Ingesting Tier 3 Conservation Intelligence...")
        for name, spec_id in species_map.items():
            meta = SPECIES_METADATA.get(name)
            if not meta:
                continue
            cur.execute("""
                INSERT INTO conservation_intelligence (species_id, iucn_status, poaching_risk_score, protected_breeding_zone, patrol_frequency_days, last_assessment_date, conservation_measures)
                VALUES (%s, %s, %s, %s, %s, %s, %s);
            """, (
                spec_id, 
                meta["iucn"], 
                meta["poach"], 
                meta["zone"], 
                max(1, 14 - meta["poach"]), # Patrol frequency derived from poaching risk (higher risk -> more frequent patrols)
                datetime.date(datetime.now() - timedelta(days=random.randint(10, 200))),
                f"Active tracking program deployed in {meta['zone']}. Strict anti-poaching measures enforced. Periodic thermal surveillance."
            ))

        # 4. Insert Corridors (Tier 2 Networks)
        print("Ingesting Corridors...")
        corridor_data = [
            ("Serengeti Migration Corridor", "East Africa (Kenya/Tanzania)", "Migratory Corridor", 150.0, "Medium"),
            ("Amazon-Guiana Ecological Corridor", "South America (Brazil/Venezuela)", "Protected Forest Zone", 850.0, "Low"),
            ("Yukon-Rocky Mountain Wilderness Corridor", "North America (Canada/USA)", "Protected Buffer", 650.0, "Low"),
            ("Great Barrier Coast Buffer Zone", "Australia/Queensland", "Coastal Riparian Zone", 420.0, "High"),
            ("Eurasian Forest Migration Network", "Northern Europe & Asia", "Migratory Corridor", 1200.0, "Medium"),
            ("Sahara Transit Buffer Zone", "North Africa", "Arid Transit Corridor", 500.0, "High")
        ]
        
        corridor_map = {}
        for cname, reg, ctype, length, threat in corridor_data:
            cur.execute("""
                INSERT INTO corridors (name, geographic_region, corridor_type, length_km, threat_level)
                VALUES (%s, %s, %s, %s, %s) RETURNING id;
            """, (cname, reg, ctype, length, threat))
            corridor_map[cname] = cur.fetchone()[0]

        # Link species to corridors based on habitat
        print("Linking Species to Corridors...")
        for name, spec_id in species_map.items():
            meta = SPECIES_METADATA.get(name)
            if not meta:
                continue
            
            c_id = None
            if meta["habitat"] == "Savannah":
                c_id = corridor_map["Serengeti Migration Corridor"]
            elif meta["habitat"] == "Forest":
                if name in ["Panda", "RedPanda", "Caterpillar", "Woodpecker", "Squirrel", "Hedgehog", "Worm"]:
                    c_id = corridor_map["Eurasian Forest Migration Network"]
                elif name in ["Bear", "BrownBear", "Fox", "Deer", "Eagle", "Owl", "Raccoon", "Turkey", "Lynx"]:
                    c_id = corridor_map["Yukon-Rocky Mountain Wilderness Corridor"]
                else:
                    c_id = corridor_map["Amazon-Guiana Ecological Corridor"]
            elif meta["habitat"] in ["Outback", "Wetlands", "Coastal Marine"]:
                c_id = corridor_map["Great Barrier Coast Buffer Zone"]
            elif meta["habitat"] == "Desert":
                c_id = corridor_map["Sahara Transit Buffer Zone"]
            elif meta["habitat"] == "Grasslands":
                c_id = corridor_map["Eurasian Forest Migration Network"]
            
            if c_id:
                cur.execute("INSERT INTO species_corridors (species_id, corridor_id) VALUES (%s, %s) ON CONFLICT DO NOTHING;", (spec_id, c_id))

        # 5. Build Tier 2 Ecological Interactions (Predator/Prey/Mutualism)
        print("Generating Tier 2 Ecological Network interactions...")
        predators = ["Lion", "Leopard", "Cheetah", "Tiger", "Bear", "BrownBear", "Fox", "Eagle", "Owl", "Jaguar", "Lynx", "Scorpion", "Otter", "Crocodile"]
        herbivores = ["Zebra", "Bull", "Camel", "Duck", "Squirrel", "Horse", "Tortoise", "Kangaroo", "Deer", "Monkey", "Goat", "Rabbit", "Giraffe", "Goose", "Sheep", "Ostrich", "Panda", "Koala"]
        insects_worms = ["Butterfly", "Centipede", "Caterpillar", "Spider", "Ladybug", "Worm"]

        interactions_to_insert = []
        for pred in predators:
            pred_id = species_map[pred]
            # Pick 2-4 random prey based on geography or generic niches
            pred_meta = SPECIES_METADATA[pred]
            
            # Filter potential prey sharing same or compatible habitats
            potentials = [h for h in herbivores if SPECIES_METADATA[h]["habitat"] == pred_meta["habitat"]]
            if not potentials:
                potentials = herbivores # Fallback to any herbivore
                
            targets = random.sample(potentials, min(len(potentials), random.randint(2, 3)))
            for prey in targets:
                prey_id = species_map[prey]
                interactions_to_insert.append((
                    pred_id, prey_id, 'Predation', 'Herbivore to Apex Carnivore',
                    f"Natural predation observed in {pred_meta['habitat']} ecosystems where {pred} limits {prey} overgrazing."
                ))

        # Add some insectivore/smaller predator interactions
        for small_pred in ["Fox", "Owl", "Eagle", "Frog", "Raccoon"]:
            sp_id = species_map[small_pred]
            for bug in random.sample(insects_worms, 3):
                bug_id = species_map[bug]
                interactions_to_insert.append((
                    sp_id, bug_id, 'Predation', 'Invertebrate to Small Carnivore',
                    f"{small_pred} preying on {bug} as a secondary food source."
                ))
                
        # Add Mutualism examples
        interactions_to_insert.append((
            species_map["Butterfly"], species_map["Caterpillar"], 'Symbiosis', 'Life Cycle Stages',
            "Caterpillars mature into Butterflies, representing essential primary consumer and pollinator lifecycle phases."
        ))

        execute_values(cur, """
            INSERT INTO ecological_interactions (species_a_id, species_b_id, interaction_type, energy_transfer_pathway, interaction_details)
            VALUES %s;
        """, interactions_to_insert)

        # 6. Parse YOLO dataset folder to ingest Tier 1 Sightings
        print("Scanning YOLO annotation label files for Tier 1 Sightings...")
        # Search in train, valid, and test labels
        label_pattern = os.path.join(args.dataset_dir, "**", "labels", "*.txt")
        label_files = glob.glob(label_pattern, recursive=True)

        if not label_files:
            print("Warning: No YOLO text annotations (.txt) found in dataset directory.", file=sys.stderr)
            conn.rollback()
            sys.exit(1)

        print(f"Found {len(label_files)} annotation files. Processing...")
        
        # Limit processing to a reasonable number to avoid slow loads
        max_sightings = 1500
        random.shuffle(label_files)
        files_to_process = label_files[:max_sightings]

        sighting_count = 0
        for filepath in files_to_process:
            # Determine corresponding image file path
            # Replaces /labels/ with /images/ and changes file extension from .txt to .jpg (or png)
            img_path_rel = filepath.replace(os.sep + "labels" + os.sep, os.sep + "images" + os.sep)
            img_path_rel = img_path_rel.replace(".txt", ".jpg")
            
            # Check if image file exists
            if not os.path.exists(img_path_rel):
                # Check for png fallback
                img_path_rel_png = img_path_rel.replace(".jpg", ".png")
                if os.path.exists(img_path_rel_png):
                    img_path_rel = img_path_rel_png
                else:
                    # Keep path reference anyway, but warning
                    pass

            # Read annotations in the file
            # YOLO annotations: class_id x_center y_center width height
            try:
                with open(filepath, 'r') as f:
                    lines = f.readlines()
            except Exception as e:
                print(f"Error reading file {filepath}: {e}", file=sys.stderr)
                continue

            for line in lines:
                parts = line.strip().split()
                if not parts:
                    continue
                try:
                    class_id = int(parts[0])
                except ValueError:
                    continue
                
                # Check bounds
                if class_id < 0 or class_id >= len(SPECIES_CLASSES):
                    continue

                class_name = SPECIES_CLASSES[class_id]
                spec_id = species_map[class_name]
                meta = SPECIES_METADATA[class_name]

                # Generate simulated telemetry fields
                lat, lon = generate_gps(meta["habitat"])
                sighting_time = datetime.now() - timedelta(
                    days=random.randint(0, 30), 
                    hours=random.randint(0, 23), 
                    minutes=random.randint(0, 59)
                )
                sensor_id = f"YOLO-CAM-{random.randint(100, 999)}"
                battery = round(random.uniform(15.0, 100.0), 1)
                temp = round(random.uniform(15.0, 38.0), 1)
                
                # Calculate coordinates string for notes
                notes = f"YOLO Object Detection event. Bounding box coordinates center ({parts[1]}, {parts[2]}) dimensions {parts[3]}x{parts[4]} in source image frame."

                cur.execute("""
                    INSERT INTO sightings (species_id, sighting_time, latitude, longitude, sensor_id, battery_level_pct, ambient_temp_c, image_path, notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
                """, (
                    spec_id, 
                    sighting_time, 
                    lat, 
                    lon, 
                    sensor_id, 
                    battery, 
                    temp, 
                    os.path.relpath(img_path_rel, os.path.dirname(args.dataset_dir)) if os.path.exists(img_path_rel) else img_path_rel, 
                    notes
                ))
                sighting_count += 1

        conn.commit()
        print(f"Ingestion Completed successfully!")
        print(f"Total Species inserted: {len(species_map)}")
        print(f"Total Corridors inserted: {len(corridor_map)}")
        print(f"Total Ecological Interactions inserted: {len(interactions_to_insert)}")
        print(f"Total Telemetry Sightings (parsed from YOLO): {sighting_count}")

    except Exception as e:
        conn.rollback()
        print(f"Ingestion failed due to database transaction error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
