-- Retire non-ground-level bays from active inventory without deleting
-- parking_slots rows that may be referenced by historical tickets.
UPDATE parking_slots
SET status = 'MAINTENANCE',
    sensor_state = 'EMPTY',
    active_vehicle_number = NULL
WHERE floor_level > 0
  AND status = 'AVAILABLE';
