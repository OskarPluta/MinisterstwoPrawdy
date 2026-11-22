-- name: raise
-- One open alert per fingerprint (partial unique index alerts_open_fingerprint): a repeat
-- raises `count` and moves `last_seen_at` instead of adding a row.
INSERT INTO alerts (created_at, last_seen_at, kind, severity, fingerprint, check_id, message)
VALUES (%(at)s, %(at)s, %(kind)s, %(severity)s, %(fingerprint)s, %(check_id)s, %(message)s)
ON CONFLICT (fingerprint) WHERE acknowledged_at IS NULL DO UPDATE SET
    count        = alerts.count + 1,
    last_seen_at = excluded.last_seen_at,
    check_id     = COALESCE(excluded.check_id, alerts.check_id),
    message      = excluded.message,
    severity     = excluded.severity
RETURNING alert_id;

-- name: fingerprint_seen
SELECT 1 AS seen FROM alerts WHERE fingerprint = %(fingerprint)s LIMIT 1;

-- name: list
SELECT * FROM alerts
WHERE (NOT %(open_only)s OR acknowledged_at IS NULL)
ORDER BY last_seen_at DESC, alert_id DESC
LIMIT %(limit)s;

-- name: open_count
SELECT COUNT(*) AS n,
       COUNT(*) FILTER (WHERE severity = 'error') AS errors
FROM alerts WHERE acknowledged_at IS NULL;

-- name: ack
UPDATE alerts SET acknowledged_at = %(at)s
WHERE alert_id = %(alert_id)s AND acknowledged_at IS NULL
RETURNING alert_id;

-- name: ack_all
UPDATE alerts SET acknowledged_at = %(at)s WHERE acknowledged_at IS NULL;

-- name: open_for_notify
-- The notifier decides in Python (time comparison) which of these to send.
SELECT * FROM alerts WHERE acknowledged_at IS NULL ORDER BY alert_id;

-- name: mark_notified
UPDATE alerts SET notified_at = %(at)s, notified_count = %(count)s WHERE alert_id = %(alert_id)s;
