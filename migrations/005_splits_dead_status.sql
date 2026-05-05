ALTER TABLE splits DROP CONSTRAINT splits_status_check;
ALTER TABLE splits ADD CONSTRAINT splits_status_check
  CHECK (status IN ('pending', 'paid', 'failed', 'dead'));
