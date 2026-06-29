CREATE SCHEMA IF NOT EXISTS payroll_es;

-- Lock down: usable only by postgres (owner) + service_role; never anon/authenticated, never on PostgREST.
REVOKE ALL ON SCHEMA payroll_es FROM PUBLIC;
GRANT USAGE ON SCHEMA payroll_es TO service_role;

CREATE TABLE IF NOT EXISTS payroll_es.employee (
  entity       text NOT NULL CHECK (entity IN ('CD','Atico')),
  ref          int  NOT NULL,
  full_name    text NOT NULL,
  dni          text,
  naf          text,
  categoria    text,
  antiguedad   date,
  status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active','left')),
  left_on      date,
  current_net  numeric(12,2),
  notes        text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (entity, ref)
);

CREATE TABLE IF NOT EXISTS payroll_es.nomina (
  entity         text NOT NULL CHECK (entity IN ('CD','Atico')),
  ref            int  NOT NULL,
  period         date NOT NULL,                 -- 1st of the month
  dias           int,
  devengado      numeric(12,2),
  salario_base   numeric(12,2),
  pagas_extras   numeric(12,2),
  complementos   numeric(12,2),
  ss_empleado    numeric(12,2),
  irpf           numeric(12,2),
  total_deducido numeric(12,2),
  liquido        numeric(12,2),
  ss_empresa     numeric(12,2),
  coste_empresa  numeric(12,2),
  base_ss        numeric(12,2),
  base_irpf      numeric(12,2),
  pdf_path       text,
  notes          text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (entity, ref, period),
  FOREIGN KEY (entity, ref) REFERENCES payroll_es.employee (entity, ref)
);

CREATE TABLE IF NOT EXISTS payroll_es.edit_audit (
  id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  at         timestamptz NOT NULL DEFAULT now(),
  actor      text,
  entity     text,
  table_name text,
  row_ref    text,
  field      text,
  old_value  text,
  new_value  text
);

-- Calendar-year (Jan–Dec) aggregate per employee — matches the Lanzarote FY.
CREATE OR REPLACE VIEW payroll_es.year_summary AS
SELECT n.entity,
       n.ref,
       e.full_name,
       EXTRACT(YEAR FROM n.period)::int      AS calendar_year,
       COUNT(*)                              AS months,
       SUM(n.devengado)                      AS devengado,
       SUM(n.irpf)                           AS irpf,
       SUM(n.ss_empleado)                    AS ss_empleado,
       SUM(n.ss_empresa)                     AS ss_empresa,
       SUM(n.liquido)                        AS liquido,
       SUM(n.coste_empresa)                  AS coste_empresa
FROM payroll_es.nomina n
JOIN payroll_es.employee e ON e.entity = n.entity AND e.ref = n.ref
GROUP BY n.entity, n.ref, e.full_name, EXTRACT(YEAR FROM n.period);

-- Grants: service_role only (postgres owns). No anon/authenticated.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA payroll_es TO service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA payroll_es GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO service_role;
