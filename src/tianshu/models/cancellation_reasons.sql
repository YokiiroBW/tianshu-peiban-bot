ALTER TABLE models.cancellations
    ADD COLUMN reason text NOT NULL DEFAULT 'explicit_cancel'
    CHECK (reason IN ('explicit_cancel', 'consumer_interrupted'));
