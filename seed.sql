-- seed.sql
-- Wildlife & Biodiversity 3-Tier Hierarchy Seed Data

-- Clear existing data
TRUNCATE TABLE conservation_intelligence, species_corridors, corridors, ecological_interactions, sightings, species RESTART IDENTITY CASCADE;

-- Insert Species (Master Table)
INSERT INTO species (scientific_name, common_name, taxonomic_class, primary_habitat) VALUES
-- Cluster 1: Savannah Ecosystem (Africa)
('Panthera leo', 'Lion', 'Mammalia', 'Savannah'),
('Equus quagga', 'Plains Zebra', 'Mammalia', 'Savannah'),
('Acinonyx jubatus', 'Cheetah', 'Mammalia', 'Savannah'),
('Loxodonta africana', 'African Bush Elephant', 'Mammalia', 'Savannah'),
('Acacia tortilis', 'Umbrella Thorn Acacia', 'Magnoliopsida', 'Savannah'),

-- Cluster 2: Coral Reef Ecosystem (Oceanic)
('Galeocerdo cuvier', 'Tiger Shark', 'Elasmobranchii', 'Coral Reef'),
('Chelonia mydas', 'Green Sea Turtle', 'Reptilia', 'Coral Reef'),
('Acropora cervicornis', 'Staghorn Coral', 'Anthozoa', 'Coral Reef'),
('Scarus guacamaia', 'Rainbow Parrotfish', 'Actinopterygii', 'Coral Reef'),

-- Cluster 3: Amazon Rainforest Ecosystem (South America)
('Panthera onca', 'Jaguar', 'Mammalia', 'Tropical Rainforest'),
('Hydrochoerus hydrochaeris', 'Capybara', 'Mammalia', 'Tropical Rainforest'),
('Harpia harpyja', 'Harpy Eagle', 'Aves', 'Tropical Rainforest'),
('Bertholletia excelsa', 'Brazil Nut Tree', 'Magnoliopsida', 'Tropical Rainforest');

-- Tier 1: Raw Observation & Telemetry Data (Sightings)
-- Note: GPS coordinates align with realistic habitats:
-- Savannah: ~ -1.5 Lat, 34.8 Long (Serengeti)
-- Coral Reef: ~ 20.0 Lat, -87.0 Long (Mesoamerican Reef)
-- Amazon Rainforest: ~ -3.4 Lat, -62.2 Long (Amazon Basin)

INSERT INTO sightings (species_id, sighting_time, latitude, longitude, sensor_id, battery_level_pct, ambient_temp_c, notes) VALUES
-- Savannah Sightings
(1, NOW() - INTERVAL '2 hours', -1.4833, 34.9022, 'SAV-GPS-001', 88.5, 28.4, 'Adult male resting with pride under acacia tree.'),
(1, NOW() - INTERVAL '1 day', -1.5023, 34.8911, 'SAV-GPS-001', 88.2, 24.1, 'Pride moving south-west towards watering hole.'),
(2, NOW() - INTERVAL '3 hours', -1.4912, 34.9150, 'SAV-COLLAR-102', 91.0, 29.0, 'Large herd grazing. Showing signs of vigilance.'),
(2, NOW() - INTERVAL '5 hours', -1.4755, 34.9299, 'SAV-COLLAR-102', 91.5, 31.2, 'Moving fast, predator alarm calls heard nearby.'),
(3, NOW() - INTERVAL '1 hour', -1.4622, 34.9388, 'SAV-GPS-005', 76.8, 30.5, 'Female spotted stalk-hunting zebra herd.'),
(4, NOW() - INTERVAL '4 hours', -1.6110, 34.7890, 'SAV-ELE-401', 94.0, 27.5, 'Family unit drinking and bathing at river corridor.'),
(4, NOW() - INTERVAL '2 days', -1.6420, 34.7210, 'SAV-ELE-401', 93.1, 22.8, 'Crop-raiding threat mitigated near community buffer boundary.'),
(5, NOW() - INTERVAL '10 days', -1.4500, 34.9000, 'SAV-STATIC-90', 100.0, 32.0, 'Satellite canopy analysis shows healthy foliage growth.'),

-- Coral Reef Sightings
(6, NOW() - INTERVAL '30 minutes', 20.2104, -87.2015, 'MAR-TAG-01', 65.2, 26.2, 'Cruising along outer reef wall at depth of 15m.'),
(6, NOW() - INTERVAL '8 hours', 20.1988, -87.2154, 'MAR-TAG-01', 65.8, 25.9, 'Detected near sea turtle feeding grounds.'),
(7, NOW() - INTERVAL '1 hour', 20.2055, -87.1990, 'MAR-TAG-09', 82.0, 26.8, 'Juvenile foraging on seagrass bed near shallow lagoon.'),
(7, NOW() - INTERVAL '1 day', 20.2312, -87.1521, 'MAR-TAG-09', 81.1, 25.5, 'Female nesting activity observed on beach sector C.'),
(8, NOW() - INTERVAL '15 days', 20.2010, -87.2030, 'MAR-BUOY-50', 98.4, 27.1, 'Temperature logger indicates warning threshold of 29.5C reached.'),
(9, NOW() - INTERVAL '4 hours', 20.2025, -87.2045, 'MAR-CAMERA-12', 100.0, 26.5, 'Adult feeding on macroalgae, helping keep coral skeleton clean.');

-- Amazon Rainforest Sightings
INSERT INTO sightings (species_id, sighting_time, latitude, longitude, sensor_id, battery_level_pct, ambient_temp_c, notes) VALUES
(10, NOW() - INTERVAL '6 hours', -3.4211, -62.2155, 'AMZ-COLLAR-88', 72.1, 26.8, 'Male hunting near river margin. Active pacing.'),
(10, NOW() - INTERVAL '18 hours', -3.4099, -62.2310, 'AMZ-COLLAR-88', 72.8, 24.2, 'Resting in dense canopy cover after successful capybara capture.'),
(11, NOW() - INTERVAL '2 hours', -3.4250, -62.2120, 'AMZ-TAG-50', 89.9, 28.5, 'Group of 12 grazing on riverbank vegetation.'),
(12, NOW() - INTERVAL '12 hours', -3.3980, -62.1950, 'AMZ-NEST-CAM', 100.0, 25.0, 'Breeding pair brought prey (small mammal) back to nest tree.'),
(13, NOW() - INTERVAL '30 days', -3.3985, -62.1955, 'AMZ-STATIC-01', 99.0, 26.0, 'Nest tree health monitored. Stable growth.');

-- Tier 2: Relational Network & Ecological Interactions (Interactions & Corridors)
INSERT INTO ecological_interactions (species_a_id, species_b_id, interaction_type, energy_transfer_pathway, interaction_details) VALUES
-- Savannah Interactions
(1, 2, 'Predation', 'Herbivore to Apex Carnivore', 'Lions actively hunt Plains Zebras as a primary protein source.'),
(3, 2, 'Predation', 'Herbivore to Carnivore', 'Cheetahs opportunistically target young or isolated zebras using high-speed chases.'),
(4, 5, 'Herbivory', 'Primary Producer to Megafauna', 'Elephants graze on Acacia foliage and disperse seeds through digestion, although high densities can damage tree populations.'),
(2, 5, 'Herbivory', 'Primary Producer to Herbivore', 'Zebras feed on lower leaves and seedlings of the Umbrella Thorn Acacia.'),

-- Coral Reef Interactions
(6, 7, 'Predation', 'Reptilian Herbivore to Apex Marine Predator', 'Tiger Sharks are one of the few natural predators capable of cracking Green Sea Turtle shells.'),
(9, 8, 'Mutualism', 'Symbiosis / Grazing Regulation', 'Rainbow Parrotfish feed on turf algae covering Staghorn Corals, preventing algae from smothering and killing the coral colony.'),

-- Amazon Rainforest Interactions
(10, 11, 'Predation', 'Rodent Herbivore to Apex Feline Predator', 'Jaguars hunt capybaras, especially near water bodies where capybaras feed and swim.'),
(12, 11, 'Predation', 'Rodent Herbivore to Apex Avian Predator', 'Harpy Eagles target young or small capybaras on riverbanks as food for their hatchlings.'),
(11, 13, 'Mutualism', 'Seed Dispersal / Feeding', 'Capybaras consume fallen fruits/seeds of the Brazil Nut Tree, acting as an occasional seed disperser in wet seasons.');

-- Insert Corridors
INSERT INTO corridors (name, geographic_region, corridor_type, length_km, threat_level) VALUES
('Mara-Serengeti Migratory Route', 'East Africa (Kenya/Tanzania)', 'Migratory Corridor', 150.00, 'Medium'),
('Mesoamerican Coral Corridor', 'Caribbean Coast (Mexico/Belize)', 'Protected Buffer', 320.00, 'High'),
('Guiana Shield Forest Corridor', 'Northern Amazon (Brazil/Venezuela)', 'Protected Forest Zone', 850.00, 'Low');

-- Link Species to Corridors
INSERT INTO species_corridors (species_id, corridor_id) VALUES
-- Savannah Route Links
(1, 1), -- Lion
(2, 1), -- Zebra
(4, 1), -- Elephant
-- Mesoamerican Reef Links
(6, 2), -- Tiger Shark
(7, 2), -- Green Sea Turtle
(8, 2), -- Staghorn Coral
(9, 2), -- Rainbow Parrotfish
-- Guiana Shield Links
(10, 3), -- Jaguar
(11, 3), -- Capybara
(12, 3), -- Harpy Eagle
(13, 3); -- Brazil Nut Tree

-- Tier 3: High-Priority Conservation Intelligence
INSERT INTO conservation_intelligence (species_id, iucn_status, poaching_risk_score, protected_breeding_zone, patrol_frequency_days, security_clearance_level, conservation_measures, last_assessment_date) VALUES
-- Lions
(1, 'Vulnerable (VU)', 7, 'Serengeti Central Core Zone A', 3, 'CONFIDENTIAL', 'Anti-poaching wire snare sweeps, community livestock compensation program, and fence boundary monitoring.', '2026-03-15'),
-- Zebras
(2, 'Near Threatened (NT)', 3, 'Mara River Basin Grasslands', 7, 'RESTRICTED', 'General biological counts, water point security, migration path monitoring.', '2026-01-20'),
-- Cheetahs
(3, 'Vulnerable (VU)', 6, 'Lolkisale Protected Hunting-Free Zone', 4, 'CONFIDENTIAL', 'Satellite collaring of females, conflict mitigation with pastoralist groups, research on cub survival.', '2026-05-10'),
-- Elephants
(4, 'Endangered (EN)', 9, 'Ngorongoro Crater East Ridge', 1, 'SECRET', 'Armed ranger patrols, real-time seismic acoustic threat detection, absolute bans on ivory commerce.', '2026-07-01'),
-- Tiger Sharks
(6, 'Near Threatened (NT)', 5, 'Sian Ka''an Marine Reserve Boundary', 10, 'RESTRICTED', 'Ban on longline shark fishing in regional waters, tourism regulation, acoustic receiver arrays.', '2026-02-12'),
-- Green Sea Turtles
(7, 'Endangered (EN)', 8, 'Akumal Breeding Beach Zone Beta', 2, 'CONFIDENTIAL', 'Night nesting beach patrols, beach light mitigation ordinances, hatchery nest relocation.', '2026-06-25'),
-- Staghorn Corals
(8, 'Critically Endangered (CR)', 4, 'Cancun Reef Restorative Sanctuary', 14, 'RESTRICTED', 'Coral micro-fragmentation nurseries, temperature-tolerant genotype cultivation, tourist exclusionary zones.', '2026-08-11'),
-- Jaguars
(10, 'Near Threatened (NT)', 8, 'Mamirauá Blackwater Varzea Sanctuary', 2, 'SECRET', 'High-security cameras, camera-trap sensor lines, community patrol incentives, jaguar corridor corridors connection.', '2026-04-18'),
-- Harpy Eagles
(12, 'Vulnerable (VU)', 5, 'Tumucumaque Heights Forest Reserve', 15, 'CONFIDENTIAL', 'Nesting tree telemetry, logging bans within 5km radius of documented nests, community education.', '2026-05-30');
