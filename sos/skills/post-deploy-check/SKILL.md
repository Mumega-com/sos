---
name: post-deploy-check
description: Verify a deployment didn't break key pages. Checks homepage + critical pages return 200 with expected content. Use after every deploy.
labels: [deploy, smoke, verify]
keywords: [deploy, check, smoke, verify, post-deploy]
entrypoint: ""
fuel_grade: diesel
trust_tier: 4
version: "1.0.0"
input_schema:
  type: object
  properties:
    url:
      type: string
      description: Base URL of the site
    check_pages:
      type: array
      items:
        type: object
        properties:
          path: {type: string}
          expect_text: {type: string}
  required: [url]
---

# Post-Deploy Check

After every deploy, verify key pages didn't break.

## Checks
1. Homepage returns 200
2. Key pages return 200 AND contain expected content
3. No empty pages (body size > threshold)
4. No Supabase/API errors visible in HTML

## DNU Check Pages
```
/en/                → contains "DentalNearYou"
/en/cities/         → contains "View city" (proves cities loaded)
/en/toronto/        → contains "Toronto" (proves city data loaded)
/en/cdcp/           → contains "CDCP" (proves content loaded)
/en/for-dentists/   → contains "Dentist" (proves B2B page loaded)
```

## How to run
```bash
for page in "/" "/en/cities/" "/en/toronto/" "/en/cdcp/"; do
  status=$(curl -sf "https://dentalnearyou.ca${page}" | wc -c)
  echo "$page: ${status} bytes"
done
```

## Rule
**Never deploy without running post-deploy checks.** If any page returns empty or missing content, rollback immediately.
