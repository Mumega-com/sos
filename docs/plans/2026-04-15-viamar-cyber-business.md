# Viamar Cyber Business — Phase 2

**Date:** 2026-04-14 (planned for Apr 15 build)
**Goal:** Turn Viamar's operations into a digital organism that the team controls through WhatsApp and voice.

## The Insight

Bruno's team runs on WhatsApp and phone calls. They tried GHL as a CRM and it didn't work. They use Google Workspace (AppSheet, Sheets, CSV). GHL costs $400/mo and is only used for contract signing.

**Don't change how the team works. Wrap the organism around their existing workflow.**

## Architecture

```
                WhatsApp Group (team's natural habitat)
                    ↕
            Organism (listens, structures, acts)
                    ↕
    ┌───────────────┼───────────────┐
    ↓               ↓               ↓
Google Sheets    Inkwell Portal   Notifications
(quote tracker)  (customer-facing) (milestone alerts)
```

## How It Works

### 1. Lead comes in (website form)
- WordPress form → webhook → organism creates record
- WhatsApp message to team: "New lead: Ahmed, Toyota Camry, Toronto → Lagos"
- Google Sheet row created automatically

### 2. Bruno quotes (phone/WhatsApp — no change)
- Bruno calls Ahmed, discusses shipping options
- After call, Bruno messages WhatsApp group: "Quoted Ahmed $4,200 container"
- OR Bruno speaks to Vapi: "New quote, Ahmed, Camry, Lagos, container, forty-two hundred"
- Organism structures the data, updates Sheet

### 3. Contract generated (automatic)
- Organism fills Google Docs template from Sheet data
- Contract fields: customer, vehicle, route, rate, service type, insurance
- Sends shareable link to Ahmed via email + WhatsApp
- Ahmed reviews on his phone → signs → pays deposit via Stripe

### 4. Customer portal activates (automatic)
- Inkwell portal: viamar.app/track/{reference}
- Ahmed sees: timeline, document checklist, milestone status
- No login needed — magic link in the same WhatsApp/email

### 5. Milestones (team updates via WhatsApp, customer gets alerts)
- Bruno messages group: "Ahmed's car picked up"
- Organism detects milestone → updates portal → sends Ahmed SMS
- "Your vehicle has been picked up. Next: delivery to port. Track: viamar.app/track/VM-2026-0423"

### 6. Delivery + review (automatic)
- Final milestone → customer gets: "Your vehicle has arrived! How was your experience?"
- One-tap Google review link
- Referral prompt: "Know someone shipping? Share this link for $100 off"

## Voice Input (Vapi)

Bruno or team says after a call:
"New quote, [customer name], [vehicle], [destination], [service type], [price]"

Organism:
1. Transcribes (Vapi)
2. Structures into record
3. Updates Google Sheet
4. Generates contract if requested
5. Confirms via WhatsApp: "Created quote for Ahmed — $4,200 container to Lagos. Send contract? Reply YES"

## Data That Flows to Mirror

Every transaction becomes an engram:
```json
{
  "customer": "Ahmed Saheli",
  "route": "Toronto → Lagos",
  "vehicle": "Toyota Camry 2019",
  "service": "container",
  "rate": 4200,
  "currency": "CAD",
  "insurance": "all_risk",
  "lead_source": "organic_seo",
  "landing_page": "/car-shipping-to-nigeria/",
  "query": "car shipping from canada to nigeria",
  "days_to_quote": 1,
  "days_to_sign": 3,
  "days_to_deliver": 42,
  "outcome": "delivered",
  "review_left": true,
  "review_rating": 5,
  "referrals": 1
}
```

50 of these → organism knows everything about the business.

## Contract Template (from actual PDF)

### Auto Export Contract — Fields
- Reference #
- Date
- Customer name
- Vehicle description (year, make, model, VIN)
- From (origin city/address)
- To (destination country/port)
- Type of service (Shared / Container / RoRo)
- Rate + currency + payment terms
- Service description: inclusions
- Insurance: Total Loss (% of value) / All Risk (% of value, $1000 USD deductible)
- Exclusions (standard terms — 15 clauses)
- CIFFA member statement
- Validity: 30 days

### Household Goods Contract — Fields
- Similar structure, different inclusions
- Packing/unpacking services
- Storage terms
- Weight-based pricing

## GHL Removal Plan

```
Cancel GHL:                                    -$400/mo (-$4,800/yr)
Replace with:
  Contract signing → Inkwell portal + Stripe     $0
  SMS alerts → Twilio                            ~$20/mo
  Email → Resend free tier                       $0
  Pipeline → Google Sheets + D1                  $0
  WhatsApp automation → Vapi + webhook           ~$50/mo
                                        Net savings: ~$4,000/yr
```

## Build Order

1. Google Docs contract template (auto-filled from Sheet data)
2. WhatsApp webhook → organism listener
3. Vapi voice input for team
4. Inkwell portal: /track/{reference} timeline page
5. Milestone SMS alerts (Twilio)
6. Google review automation (post-delivery)
7. Mirror storage (every transaction = engram)
8. Cancel GHL

## What This Proves for Arrow

If we can make Viamar's business run through WhatsApp + voice with zero new tools for the team, we can do it for any service business. The cyber business template:

```
config = {
  team_channel: "whatsapp",      // or slack, telegram, discord
  input_method: "voice",          // or text, form
  contract_template: "auto_export", // or services, subscription
  customer_portal: true,
  milestone_alerts: true,
  review_automation: true,
  voice_ai: "vapi"
}
```

Same organism. Different config. Different business.
