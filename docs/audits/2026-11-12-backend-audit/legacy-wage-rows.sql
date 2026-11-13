-- Legacy wage rows: read-only detection for operators (Postgres).
--
-- Follow-up to PR #209 (per-entry / per-shift wages) and PR #227 (N-2). Before 2026-10-19
-- (commit af1aaaff) a timesheet approval posted ONE ledger row per approved WINDOW,
-- ref_id '<employee>:<from>:<to>'. From then until #209 it posted one row per employee and
-- UTC day, ref_id '<employee>:<day>:<day>'. Neither has an accrual_key. Until #227, reopening an
-- entry paid by a window row reversed nothing, and re-approving it paid it a second time.
-- Attendance wages had the same window format until #209 (ref_type 'attendance').
--
-- Every statement here is a SELECT. Output is ids, dates and counts only: no names, emails or
-- amounts (join employee_balance_ledger.amount yourself if you need the money). Fix anything
-- found with adjusting ledger rows, never by editing or deleting rows.
--
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f docs/audits/2026-11-12-backend-audit/legacy-wage-rows.sql
--
-- Safe to run on a live database: wrap it in a read-only transaction if you want a guarantee.
BEGIN TRANSACTION READ ONLY;

-- 1. How many legacy rows each shop has, by format. A shop with window_rows = 0 and
--    attendance_window_rows = 0 needs nothing from sections 2-4.
SELECT l.org_id,
       COUNT(*) FILTER (WHERE l.ref_type = 'timesheet'
                          AND split_part(l.ref_id, ':', 2) <> split_part(l.ref_id, ':', 3)) AS window_rows,
       COUNT(*) FILTER (WHERE l.ref_type = 'timesheet'
                          AND split_part(l.ref_id, ':', 2) = split_part(l.ref_id, ':', 3))  AS day_rows,
       COUNT(*) FILTER (WHERE l.ref_type = 'attendance')                                    AS attendance_window_rows
FROM employee_balance_ledger l
WHERE l.entry_type = 'hourly_wage'
  AND l.accrual_key IS NULL
  AND l.ref_type IN ('timesheet', 'attendance')
  AND l.ref_id ~ '^[^:]+:\d{4}-\d{2}-\d{2}:\d{4}-\d{2}-\d{2}$'
GROUP BY l.org_id
ORDER BY window_rows DESC, attendance_window_rows DESC, l.org_id;

-- 2. Window-format timesheet rows (the format the #209 detection missed), one line each.
--    entries_reopened_after: entries in the window reopened after the row was posted. Before #227
--      each of those, once re-approved, was paid again on top of this row (N-2).
--    entries_paid_per_entry: entries in the window that now also have their own wage group
--      ('hourly:<entry>'), i.e. were paid again per entry.
--    legacy_reversals: reversing rows #227 has posted against this window
--      ('hourly-legacy:<emp>:<from>:<to>'). A row with entries_paid_per_entry > 0 and
--      legacy_reversals = 0 was very likely paid twice for those entries.
WITH w AS (
    SELECT l.id AS ledger_id, l.org_id, l.employee_id, l.created_at,
           split_part(l.ref_id, ':', 2)::date AS date_from,
           split_part(l.ref_id, ':', 3)::date AS date_to
    FROM employee_balance_ledger l
    WHERE l.entry_type = 'hourly_wage' AND l.ref_type = 'timesheet' AND l.accrual_key IS NULL
      AND l.ref_id ~ '^[^:]+:\d{4}-\d{2}-\d{2}:\d{4}-\d{2}-\d{2}$'
      AND split_part(l.ref_id, ':', 2) <> split_part(l.ref_id, ':', 3)
)
SELECT w.ledger_id, w.org_id, w.employee_id, w.date_from, w.date_to,
       (w.date_to - w.date_from) + 1 AS window_days,
       (SELECT COUNT(*) FROM time_entries t
         WHERE t.org_id = w.org_id AND t.employee_id = w.employee_id
           AND t.started_at >= w.date_from AND t.started_at < w.date_to + 1
           AND t.state = 'approved') AS approved_entries,
       (SELECT COUNT(DISTINCT t.id) FROM time_entries t
          JOIN time_entry_audits a ON a.time_entry_id = t.id AND a.action = 'reopen'
         WHERE t.org_id = w.org_id AND t.employee_id = w.employee_id
           AND t.started_at >= w.date_from AND t.started_at < w.date_to + 1
           AND a.at > w.created_at) AS entries_reopened_after,
       (SELECT COUNT(DISTINCT t.id) FROM time_entries t
          JOIN employee_balance_ledger g ON g.org_id = t.org_id AND g.accrual_key = 'hourly:' || t.id
         WHERE t.org_id = w.org_id AND t.employee_id = w.employee_id
           AND t.started_at >= w.date_from AND t.started_at < w.date_to + 1) AS entries_paid_per_entry,
       (SELECT COUNT(*) FROM employee_balance_ledger r
         WHERE r.org_id = w.org_id
           AND r.accrual_key = 'hourly-legacy:' || w.employee_id || ':' || w.date_from || ':' || w.date_to)
           AS legacy_reversals
FROM w
ORDER BY w.org_id, w.employee_id, w.date_from;

-- 3. Legacy timesheet rows of one person whose dates overlap (window/window or window/day): the
--    days in the overlap were paid by both rows.
WITH r AS (
    SELECT l.id, l.org_id, l.employee_id,
           split_part(l.ref_id, ':', 2)::date AS date_from,
           split_part(l.ref_id, ':', 3)::date AS date_to
    FROM employee_balance_ledger l
    WHERE l.entry_type = 'hourly_wage' AND l.ref_type = 'timesheet' AND l.accrual_key IS NULL
      AND l.ref_id ~ '^[^:]+:\d{4}-\d{2}-\d{2}:\d{4}-\d{2}-\d{2}$'
)
SELECT a.org_id, a.employee_id, a.id AS ledger_id_a, b.id AS ledger_id_b,
       GREATEST(a.date_from, b.date_from) AS overlap_from,
       LEAST(a.date_to, b.date_to) AS overlap_to
FROM r a
JOIN r b ON b.org_id = a.org_id AND b.employee_id = a.employee_id AND a.id < b.id
        AND a.date_from <= b.date_to AND b.date_from <= a.date_to
ORDER BY a.org_id, a.employee_id, overlap_from;

-- 4. Attendance window rows (LAB-02 format) whose dates overlap another of the same person's
--    window rows: shifts that started in the overlap were paid twice. Rows from #209 on are per
--    shift ('attendance:<shift>' accrual key) and never appear here.
WITH r AS (
    SELECT l.id, l.org_id, l.employee_id,
           split_part(l.ref_id, ':', 2)::date AS date_from,
           split_part(l.ref_id, ':', 3)::date AS date_to
    FROM employee_balance_ledger l
    WHERE l.entry_type = 'hourly_wage' AND l.ref_type = 'attendance' AND l.accrual_key IS NULL
      AND l.ref_id ~ '^[^:]+:\d{4}-\d{2}-\d{2}:\d{4}-\d{2}-\d{2}$'
)
SELECT a.org_id, a.employee_id, a.id AS ledger_id_a, b.id AS ledger_id_b,
       GREATEST(a.date_from, b.date_from) AS overlap_from,
       LEAST(a.date_to, b.date_to) AS overlap_to,
       (SELECT COUNT(*) FROM shifts s
         WHERE s.employee_id = a.employee_id AND s.ended_at IS NOT NULL
           AND s.started_at >= GREATEST(a.date_from, b.date_from)
           AND s.started_at < LEAST(a.date_to, b.date_to) + 1) AS shifts_in_overlap
FROM r a
JOIN r b ON b.org_id = a.org_id AND b.employee_id = a.employee_id AND a.id < b.id
        AND a.date_from <= b.date_to AND b.date_from <= a.date_to
ORDER BY a.org_id, a.employee_id, overlap_from;

ROLLBACK;
