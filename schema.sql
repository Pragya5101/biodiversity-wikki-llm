-- schema.sql
-- Wildlife & Biodiversity 3-Tier Hierarchy database schema

-- Master Table: Species
CREATE TABLE IF NOT EXISTS species (
    id SERIAL PRIMARY KEY,
    scientific_name VARCHAR(100) UNIQUE NOT NULL,
    common_name VARCHAR(100) NOT NULL,
    taxonomic_class VARCHAR(50) NOT NULL,
    primary_habitat VARCHAR(100) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Indexing for fast search on names
CREATE INDEX IF NOT EXISTS idx_species_scientific_name ON species(scientific_name);
CREATE INDEX IF NOT EXISTS idx_species_common_name ON species(common_name);

-- Tier 1: Raw Observation & Telemetry Data
-- Sightings, GPS coordinates, timestamps, sensors, and environmental readings
CREATE TABLE IF NOT EXISTS sightings (
    id SERIAL PRIMARY KEY,
    species_id INTEGER REFERENCES species(id) ON DELETE CASCADE,
    sighting_time TIMESTAMP WITH TIME ZONE NOT NULL,
    latitude DECIMAL(9, 6) NOT NULL,
    longitude DECIMAL(9, 6) NOT NULL,
    sensor_id VARCHAR(50) NOT NULL,
    battery_level_pct DECIMAL(5, 2),
    ambient_temp_c DECIMAL(4, 1),
    image_path VARCHAR(255),
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sightings_species_id ON sightings(species_id);
CREATE INDEX IF NOT EXISTS idx_sightings_sighting_time ON sightings(sighting_time);

-- Tier 2: Relational Network & Ecological Interactions
-- Food-web links, predator-prey dynamics, symbiosis, etc.
CREATE TABLE IF NOT EXISTS ecological_interactions (
    id SERIAL PRIMARY KEY,
    species_a_id INTEGER REFERENCES species(id) ON DELETE CASCADE,
    species_b_id INTEGER REFERENCES species(id) ON DELETE CASCADE,
    interaction_type VARCHAR(50) NOT NULL, -- Predation, Mutualism, Competition, Parasitism, Commensalism
    energy_transfer_pathway VARCHAR(100),   -- e.g., 'Primary Consumer to Predator', 'Nectar Producer to Pollinator'
    interaction_details TEXT,
    CONSTRAINT check_self_interaction CHECK (species_a_id <> species_b_id)
);

CREATE INDEX IF NOT EXISTS idx_interactions_species_a ON ecological_interactions(species_a_id);
CREATE INDEX IF NOT EXISTS idx_interactions_species_b ON ecological_interactions(species_b_id);

-- Shared Corridors & Habitat Connections
CREATE TABLE IF NOT EXISTS corridors (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    geographic_region VARCHAR(100) NOT NULL,
    corridor_type VARCHAR(50) NOT NULL, -- Migratory, Riparian Zone, Protected Buffer
    length_km DECIMAL(6, 2),
    threat_level VARCHAR(20) NOT NULL -- Low, Medium, High, Critical
);

-- Junction Table for Species using Corridors
CREATE TABLE IF NOT EXISTS species_corridors (
    species_id INTEGER REFERENCES species(id) ON DELETE CASCADE,
    corridor_id INTEGER REFERENCES corridors(id) ON DELETE CASCADE,
    PRIMARY KEY (species_id, corridor_id)
);

-- Tier 3: High-Priority Conservation Intelligence
-- IUCN status, poaching risk scores, protected breeding zones, high-security data
CREATE TABLE IF NOT EXISTS conservation_intelligence (
    id SERIAL PRIMARY KEY,
    species_id INTEGER UNIQUE REFERENCES species(id) ON DELETE CASCADE,
    iucn_status VARCHAR(50) NOT NULL, -- e.g., Critically Endangered (CR), Endangered (EN), Vulnerable (VU), Least Concern (LC)
    poaching_risk_score INTEGER NOT NULL CHECK (poaching_risk_score BETWEEN 1 AND 10), -- 1 (Low) to 10 (Critical)
    protected_breeding_zone VARCHAR(150) NOT NULL,
    patrol_frequency_days INTEGER,
    security_clearance_level VARCHAR(20) DEFAULT 'RESTRICTED', -- RESTRICTED, CONFIDENTIAL, SECRET
    conservation_measures TEXT,
    last_assessment_date DATE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
