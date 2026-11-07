-- Only future rows change. Existing group and person policies retain their CAS.
ALTER TABLE life.contact_preferences ALTER COLUMN enabled SET DEFAULT false;
