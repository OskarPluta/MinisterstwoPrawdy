-- name: insert
INSERT INTO admin_audit (at, action, target, details_json)
VALUES (%(at)s, %(action)s, %(target)s, %(details_json)s);

-- name: list
SELECT audit_id, at, action, target, details_json FROM admin_audit
ORDER BY audit_id DESC
LIMIT %(limit)s;
