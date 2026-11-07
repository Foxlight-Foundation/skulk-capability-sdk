BEGIN TRANSACTION;
CREATE TABLE operations (
    key TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    state TEXT NOT NULL,
    submission TEXT NOT NULL,
    progress REAL,
    stage TEXT,
    submitted_at INTEGER NOT NULL,
    started_at INTEGER,
    updated_at INTEGER NOT NULL,
    finished_at INTEGER,
    input_digest TEXT NOT NULL,
    input_json TEXT NOT NULL,
    backend_reference TEXT,
    result_json TEXT,
    error TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    sequence INTEGER NOT NULL
);
INSERT INTO "operations" VALUES('legacy-scopepending','pending','legacy-scope','queued','pending',NULL,NULL,1000,NULL,1000,NULL,'f3b11d0ec4ca63cc3e4bc689d28e4cf418279d0e2eeced19fb934725d183e9a8','{"label":"pending"}',NULL,NULL,NULL,0,3);
INSERT INTO "operations" VALUES('legacy-scopeuncertain','uncertain','legacy-scope','running','uncertain',NULL,NULL,1000,1000,1000,NULL,'8e773ac45189094418836eb74986fc4d87946d4cee546b9284371a3fcd124396','{"label":"uncertain"}',NULL,NULL,NULL,0,4);
INSERT INTO "operations" VALUES('legacy-scopeaccepted','accepted','legacy-scope','running','accepted',NULL,NULL,1000,1000,1000,NULL,'3a7a79f179a51fcdabfceb92b1161ce6e6075e60d7a34e65365c74d39ffb308f','{"label":"accepted"}','synthetic-backend-job',NULL,NULL,1,5);
INSERT INTO "operations" VALUES('legacy-scopefinished','finished','legacy-scope','succeeded','pending',NULL,NULL,1000,NULL,1000,1000,'38318d5da5de866072385aaec30c49b2d77d0bbf57da8ceec6bf2e80148c5244','{"label":"finished"}',NULL,'{"artifact":"synthetic-local-result"}',NULL,0,6);
CREATE TABLE reservations (
    key TEXT PRIMARY KEY,
    input_digest TEXT NOT NULL,
    reserved_at INTEGER NOT NULL
);
INSERT INTO "reservations" VALUES('legacy-scopepruned','af5d8058463dafafba5b3bb397ee1647b2cbed8de02800163239ea131469043b',1000);
INSERT INTO "reservations" VALUES('legacy-scopeolder','7fedc12576ccc0a0ea90286c5ddb9198fccadcd03200bc88709dee841a365dcf',1000);
INSERT INTO "reservations" VALUES('legacy-scopepending','f3b11d0ec4ca63cc3e4bc689d28e4cf418279d0e2eeced19fb934725d183e9a8',1000);
INSERT INTO "reservations" VALUES('legacy-scopeuncertain','8e773ac45189094418836eb74986fc4d87946d4cee546b9284371a3fcd124396',1000);
INSERT INTO "reservations" VALUES('legacy-scopeaccepted','3a7a79f179a51fcdabfceb92b1161ce6e6075e60d7a34e65365c74d39ffb308f',1000);
INSERT INTO "reservations" VALUES('legacy-scopefinished','38318d5da5de866072385aaec30c49b2d77d0bbf57da8ceec6bf2e80148c5244',1000);
CREATE INDEX operations_sequence ON operations(sequence);
COMMIT;
