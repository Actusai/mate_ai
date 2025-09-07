-- app/db/sql/002_notifications.sql
CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY,
  company_id INTEGER NOT NULL,
  ai_system_id INTEGER,
  task_id INTEGER,
  user_id INTEGER,
  channel TEXT NOT NULL DEFAULT 'log',
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  error_text TEXT,
  scheduled_for DATETIME,
  sent_at DATETIME,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(company_id)  REFERENCES companies(id)       ON DELETE CASCADE,
  FOREIGN KEY(ai_system_id)REFERENCES ai_systems(id)     ON DELETE CASCADE,
  FOREIGN KEY(task_id)     REFERENCES compliance_tasks(id) ON DELETE CASCADE,
  FOREIGN KEY(user_id)     REFERENCES users(id)          ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_notifications_company ON notifications(company_id);
CREATE INDEX IF NOT EXISTS ix_notifications_status  ON notifications(status);
CREATE INDEX IF NOT EXISTS ix_notifications_task    ON notifications(task_id);