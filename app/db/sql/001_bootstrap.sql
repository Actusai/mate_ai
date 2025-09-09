PRAGMA foreign_keys=ON;
BEGIN;

-- ========== TABLICE ==========

-- Audit logovi (koristi ih audit viewer i export)
CREATE TABLE IF NOT EXISTS audit_logs (
  id INTEGER PRIMARY KEY,
  company_id INTEGER NOT NULL,
  user_id INTEGER,
  action TEXT NOT NULL,
  entity_type TEXT,
  entity_id INTEGER,
  meta TEXT,
  ip_address TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_audit_logs_company_id  ON audit_logs(company_id);
CREATE INDEX IF NOT EXISTS ix_audit_logs_created_at  ON audit_logs(created_at);
CREATE INDEX IF NOT EXISTS ix_audit_logs_action      ON audit_logs(action);
CREATE INDEX IF NOT EXISTS ix_audit_logs_entity_type ON audit_logs(entity_type);

-- Članstvo korisnika u AI sustavima (filteri u exportu i listama)
CREATE TABLE IF NOT EXISTS ai_system_members (
  id INTEGER PRIMARY KEY,
  ai_system_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (ai_system_id, user_id),
  FOREIGN KEY (ai_system_id) REFERENCES ai_systems(id) ON DELETE CASCADE,
  FOREIGN KEY (user_id)      REFERENCES users(id)      ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_ai_system_members_user ON ai_system_members(user_id);

-- ========== VIEW-OVI ZA IZVJEŠTAJE/EXPORT ==========

-- Task statusi po sustavu (za /reports/export?type=task_status)
DROP VIEW IF EXISTS vw_task_status_counts;
CREATE VIEW vw_task_status_counts AS
SELECT
  ct.company_id,
  ct.ai_system_id,
  SUM(CASE WHEN ct.status='open'         THEN 1 ELSE 0 END) AS open_cnt,
  SUM(CASE WHEN ct.status='in_progress'  THEN 1 ELSE 0 END) AS in_progress_cnt,
  SUM(CASE WHEN ct.status='blocked'      THEN 1 ELSE 0 END) AS blocked_cnt,
  SUM(CASE WHEN ct.status='postponed'    THEN 1 ELSE 0 END) AS postponed_cnt,
  SUM(CASE WHEN ct.status='done'         THEN 1 ELSE 0 END) AS done_cnt
FROM compliance_tasks ct
GROUP BY ct.company_id, ct.ai_system_id;

-- Breakdown po reference (EU AI Act članci itd.)
DROP VIEW IF EXISTS vw_reference_breakdown;
CREATE VIEW vw_reference_breakdown AS
SELECT
  ct.company_id,
  COALESCE(ct.reference,'') AS reference,
  COUNT(*) AS total,
  SUM(CASE WHEN ct.status='done' THEN 1 ELSE 0 END) AS done_cnt,
  SUM(CASE WHEN ct.status IN ('open','blocked')
           AND ct.due_date IS NOT NULL
           AND DATE(ct.due_date) < DATE('now')
      THEN 1 ELSE 0 END) AS overdue_cnt
FROM compliance_tasks ct
GROUP BY ct.company_id, COALESCE(ct.reference,'');

-- Compliance KPI po sustavu
DROP VIEW IF EXISTS vw_system_compliance;
CREATE VIEW vw_system_compliance AS
SELECT
  s.id AS ai_system_id,
  s.company_id,
  CASE
    WHEN COUNT(ct.id)=0 THEN 100.0
    ELSE (SUM(CASE WHEN ct.status='done' THEN 1 ELSE 0 END) * 100.0) / COUNT(ct.id)
  END AS compliance_pct,
  SUM(CASE WHEN ct.status IN ('open','blocked')
           AND ct.due_date IS NOT NULL
           AND DATE(ct.due_date) < DATE('now')
      THEN 1 ELSE 0 END) AS overdue_cnt
FROM ai_systems s
LEFT JOIN compliance_tasks ct ON ct.ai_system_id = s.id
GROUP BY s.id, s.company_id;

-- Aggregat po kompaniji
DROP VIEW IF EXISTS vw_company_compliance;
CREATE VIEW vw_company_compliance AS
WITH sys AS (
  SELECT company_id, id AS ai_system_id FROM ai_systems
),
sc AS (
  SELECT company_id, ai_system_id, compliance_pct, overdue_cnt FROM vw_system_compliance
)
SELECT
  c.id AS company_id,
  COUNT(DISTINCT sys.ai_system_id)                 AS systems_cnt,
  COALESCE(AVG(sc.compliance_pct), 100.0)          AS avg_compliance_pct,
  COALESCE(SUM(sc.overdue_cnt), 0)                 AS overdue_cnt
FROM companies c
LEFT JOIN sys ON sys.company_id = c.id
LEFT JOIN sc  ON sc.ai_system_id = sys.ai_system_id
GROUP BY c.id;

COMMIT;