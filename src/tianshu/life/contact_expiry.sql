ALTER TABLE life.contact_preferences
    ADD COLUMN expiry_seconds integer NOT NULL DEFAULT 14400
    CHECK (expiry_seconds BETWEEN 60 AND 2592000);
