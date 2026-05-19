# Incident 2026-04-16 — app.mumega.com redirected instead of proxying

**Reporter:** hadi (via sos-dev session)
**Severity:** blocker
**Time to resolve:** ~5 min
**Pipe:** nginx
**Medic version:** 1.0.0 (seeded retroactively)

## Symptom

`https://app.mumega.com/dashboard` 301-redirected to `https://mumega.com/dashboard`, which 404s on the marketing site. Dashboard was unreachable via its public URL even though the backend on `:8090` was healthy.

## Reproduction

```
$ curl -I https://app.mumega.com/dashboard
HTTP/2 301
location: https://mumega.com/dashboard
```

## Root cause

`/etc/nginx/sites-enabled/app.mumega.com` had a blanket `location / { return 301 https://mumega.com$request_uri; }` — placeholder from when the subdomain wasn't yet wired to the dashboard service.

## Fix

Replaced the 301 block with:

```nginx
location = / { return 302 /login; }
location / {
    proxy_pass http://127.0.0.1:8090;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_read_timeout 60s;
}
```

`nginx -t` clean → `systemctl reload nginx`.

## Verification

```
$ curl -X POST https://app.mumega.com/login -d "token=<valid>" -i | head -3
HTTP/2 303
location: /dashboard
```

Dashboard HTML rendered the correct tenant header.

## Pattern class

**Placeholder nginx redirect** — site-enabled config left as a stub redirect after DNS is live. Any time a new subdomain is added, verify `sites-enabled/<domain>` contains `proxy_pass` and not `return 301`.

## Followups

None.
