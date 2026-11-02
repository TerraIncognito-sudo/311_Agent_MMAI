-- A handful of SYNTHETIC open tickets so duplicate checks and status lookups
-- work in the demo. W2 (owner A) replaces this with the ~400-ticket seed.
-- Fake addresses only. Safe to re-run (skips existing IDs).

insert into tickets (ticket_id, code, unit, status, location, postal_code, fields, opened_at) values
  ('311-2026-900001', 'RD-POT',  'Transportation',        'open',        '45 Elm Street',                'G1L 2B4', '{"lane_or_sidewalk": "lane", "approximate_size": "dinner plate"}', now() - interval '2 days'),
  ('311-2026-900002', 'SW-BIN',  'Solid Waste',           'in_progress', '12 Harbour Road',              'G1M 3C5', '{"bin_type": "recycling", "damage_type": "cracked lid"}',            now() - interval '5 days'),
  ('311-2026-900003', 'IL-DUMP', 'Solid Waste',           'open',        'Maple Avenue and King Street', 'G1N 4D6', '{"material_type": "mattress and furniture"}',                        now() - interval '1 day'),
  ('311-2026-900004', 'TR-TREE', 'Urban Forestry',        'open',        '300 Birch Crescent',           'G1P 5E7', '{"city_or_private_tree": "city", "what_is_wrong": "dead branches"}',  now() - interval '9 days'),
  ('311-2026-900005', 'SW-MISS', 'Solid Waste',           'closed',      '88 Willow Way',                'G1R 6F8', '{"stream_missed": "organics", "scheduled_day": "Tuesday"}',          now() - interval '20 days')
on conflict (ticket_id) do nothing;
