-- Run while connected as the agent role with psql variables:
--   psql -v authorized_relation=... -v denied_relation=... -f 03_verify_read_only.sql
-- The first two statements must work. The remaining statements must fail.

SHOW default_transaction_read_only;
SELECT * FROM :authorized_relation LIMIT 1;

CREATE TABLE must_fail(id integer);
DELETE FROM :authorized_relation;
SELECT * FROM :denied_relation LIMIT 1;
