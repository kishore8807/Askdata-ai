-- Sample company data. Users are NOT seeded here (no passwords in SQL files):
-- run `python -m backend.scripts.bootstrap_users` to create the DBA / Developer / Analyst accounts.

INSERT INTO departments (department_name, description) VALUES
 ('Development', 'Software engineering and product development'),
 ('HR',          'Human resources and talent'),
 ('Finance',     'Accounting, payroll and budgeting'),
 ('Operations',  'IT operations and infrastructure'),
 ('Sales',       'Sales and customer success')
ON CONFLICT (department_name) DO NOTHING;

INSERT INTO employees (employee_code, name, email, department, designation, salary, joining_date, status) VALUES
 ('EMP001', 'Kumar Subramanian', 'kumar@example.com',   'Development', 'Senior Developer',  55000, '2021-03-15', 'Active'),
 ('EMP002', 'Ravi Chandran',     'ravi@example.com',     'HR',          'HR Manager',        45000, '2020-07-01', 'Active'),
 ('EMP003', 'Priya Natarajan',   'priya@example.com',    'Development', 'Backend Developer', 48000, '2022-01-10', 'Active'),
 ('EMP004', 'Anitha Raj',        'anitha@example.com',   'Finance',     'Accountant',        42000, '2019-11-20', 'Active'),
 ('EMP005', 'Suresh Babu',       'suresh@example.com',   'Operations',  'SRE',               52000, '2021-09-05', 'On Leave'),
 ('EMP006', 'Meena Krishnan',    'meena@example.com',    'Sales',       'Account Executive', 40000, '2023-02-14', 'Active'),
 ('EMP007', 'Arun Prakash',      'arun@example.com',     'Development', 'QA Engineer',       38000, '2022-06-27', 'Active'),
 ('EMP008', 'Divya Lakshmi',     'divya@example.com',    'Operations',  'DBA',               60000, '2018-04-02', 'Active')
ON CONFLICT (employee_code) DO NOTHING;

INSERT INTO projects (project_name, description, department_id, start_date, end_date, status)
SELECT p.project_name, p.description, d.id, p.start_date::date, p.end_date::date, p.status
FROM (VALUES
 ('Customer Portal',   'Self-service customer portal',       'Development', '2025-01-10', NULL,         'Active'),
 ('Payroll Migration', 'Move payroll to the new platform',   'Finance',     '2025-02-01', '2025-09-30', 'Active'),
 ('HR Onboarding',     'Digital onboarding workflow',        'HR',          '2024-10-01', '2025-03-31', 'Completed')
) AS p(project_name, description, dept, start_date, end_date, status)
JOIN departments d ON d.department_name = p.dept
WHERE NOT EXISTS (SELECT 1 FROM projects x WHERE x.project_name = p.project_name);

INSERT INTO tasks (project_id, employee_id, task_name, description, status, priority)
SELECT pr.id, e.id, t.task_name, t.description, t.status, t.priority
FROM (VALUES
 ('Customer Portal',   'EMP001', 'Design login API',        'REST endpoints for authentication', 'In Progress', 'High'),
 ('Customer Portal',   'EMP003', 'Build billing service',   'Invoices and payments',             'Todo',        'Medium'),
 ('Customer Portal',   'EMP007', 'Write regression tests',  'Cover the checkout flow',           'Todo',        'Medium'),
 ('Payroll Migration', 'EMP004', 'Reconcile Q1 payroll',    'Compare legacy vs new totals',      'In Progress', 'Critical'),
 ('HR Onboarding',     'EMP002', 'Publish onboarding docs', 'Policies and checklists',           'Done',        'Low')
) AS t(project_name, emp_code, task_name, description, status, priority)
JOIN projects  pr ON pr.project_name = t.project_name
JOIN employees e  ON e.employee_code = t.emp_code
WHERE NOT EXISTS (SELECT 1 FROM tasks x WHERE x.task_name = t.task_name AND x.project_id = pr.id);
