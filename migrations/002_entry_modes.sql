-- SMARTPARK migration 002: preserve reservation vs walk-in origin.
ALTER TABLE bookings_and_tickets
    ADD COLUMN IF NOT EXISTS entry_mode VARCHAR(20) NOT NULL DEFAULT 'RESERVATION';

CREATE INDEX IF NOT EXISTS idx_tickets_entry_mode_status
    ON bookings_and_tickets (entry_mode, status);
