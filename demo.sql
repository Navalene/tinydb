-- tinydb demo: Medicare clients, plans and enrollment periods.
-- All names
and data are made up.
-- Run it on a fresh folder:  rm -rf demo_data && python3 main.py --dir demo_data < demo.sql

CREATE TABLE plans (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,          -- MAPD, PDP or Supplement
    monthly_premium FLOAT NOT NULL,
    star_rating FLOAT
);

CREATE TABLE clients (
    id INT PRIMARY KEY,
    name TEXT NOT NULL,
    birth_date DATE NOT NULL,
    zip TEXT,
    plan_code TEXT,
    medicaid BOOL
);

CREATE TABLE enrollment_periods (
    id INT PRIMARY KEY,
    client_id INT NOT NULL,
    period TEXT NOT NULL,        -- IEP, AEP, OEP or SEP
    reason TEXT,
    starts DATE NOT NULL,
    deadline DATE NOT NULL
);

INSERT INTO plans VALUES
    ('H1001-001', 'Sunrise Advantage PPO', 'MAPD', 0, 4.5),
    ('H1001-002', 'Sunrise Advantage HMO', 'MAPD', 29.5, 4.0),
    ('S2002-010', 'Harbor Rx Basic', 'PDP', 12.4, 3.5),
    ('MS-G', 'Plan G Supplement', 'Supplement', 148, NULL);

INSERT INTO clients VALUES
    (1, 'Ada Reyes', '1956-03-14', '33101', 'H1001-001', FALSE),
    (2, 'Bob Okafor', '1959-11-02', '33139', 'H1001-002', TRUE),
    (3, 'Cy Lindqvist', '1945-07-21', '33101', 'MS-G', FALSE),
    (4, 'Di Patel', '1961-12-30', '33125', NULL, FALSE),
    (5, 'Eve O''Brien', '1950-05-09', '33139', 'S2002-010', TRUE);

INSERT INTO enrollment_periods VALUES
    (1, 4, 'IEP', 'Turning 65', '2026-09-01', '2027-03-31'),
    (2, 2, 'SEP', 'Has Medicaid', '2026-10-01', '2026-12-15'),
    (3, 5, 'SEP', 'Moved to a new ZIP code', '2026-11-01', '2026-12-31'),
    (4, 1, 'AEP', NULL, '2026-10-15', '2026-12-07'),
    (5, 3, 'SEP', 'Lost employer coverage', '2026-08-01', '2026-10-31');

SHOW TABLES;
DESCRIBE clients;

-- Which clients have a SEP deadline coming up before the end of the year?
SELECT client_id, reason, deadline FROM enrollment_periods
    WHERE period = 'SEP' AND deadline >= '2026-11-18' AND deadline <= '2026-12-31'
    ORDER BY deadline;

-- Same question with an index on deadline: watch the plan change.
CREATE INDEX ON enrollment_periods (deadline);
SELECT client_id, reason, deadline FROM enrollment_periods
    WHERE period = 'SEP' AND deadline >= '2026-11-18' AND deadline <= '2026-12-31'
    ORDER BY deadline;

-- Look up one client by primary key.
SELECT name, plan_code FROM clients WHERE id = 2;

-- Clients on Medicaid in one ZIP code, using a hash index.
CREATE INDEX ON clients USING HASH (zip);
SELECT name, medicaid FROM clients WHERE zip = '33139' AND medicaid = TRUE;

-- The three cheapest plans.
SELECT code, name, monthly_premium FROM plans ORDER BY monthly_premium LIMIT 3;

-- Di picks a plan; Cy's lapsed SEP is removed.
UPDATE clients SET plan_code = 'H1001-001' WHERE id = 4;
DELETE FROM enrollment_periods WHERE deadline < '2026-11-18';
SELECT id, name, plan_code FROM clients WHERE plan_code = 'H1001-001';

-- Bad data is stopped at the door.
INSERT INTO clients VALUES (6, 'Fay', '03/01/1958', '33101', NULL, FALSE);
INSERT INTO clients (id, name, birth_date) VALUES (1, 'Duplicate', '1950-01-01');
