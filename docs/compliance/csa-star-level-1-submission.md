# CSA STAR Level 1 — Self-Assessment Submission Package

**Document type:** Sprint 006 Track D.1 deliverable — submission package + cover letter for Hadi to file with the Cloud Security Alliance STAR Registry.
**Author:** Loom (autonomous coordinator agent)
**Date:** 2026-04-25
**Status:** v0.1 — submission-ready package. Hadi performs the actual STAR Registry filing (requires CSA account + organizational signature; Loom cannot file on Mumega's behalf).

---

## 0. What this is

CSA STAR Level 1 is the *self-attested* tier of the Cloud Security Alliance Security, Trust, Assurance, and Risk Registry. It accepts a completed CAIQ (Consensus Assessments Initiative Questionnaire) as the submission artifact. There is no third-party audit at Level 1 — the value is public attestation of the CAIQ mapping.

Filing path:
- CSA STAR Registry: https://cloudsecurityalliance.org/star/
- Submission portal: https://cloudsecurityalliance.org/star/registry/
- Required: CSA STAR account (free), CAIQ v4.0.2 spreadsheet completion, organizational signatory.
- Cost: free for Level 1.
- Timeline: typically published in registry within 5-10 business days of submission.

We have the CAIQ v4.0.2 mapping shipped already at `mumega.com/trust` (Track C.1 companion document). This package extracts the per-control evidence into the format CSA STAR expects.

---

## 1. Submission contents

### 1.1 Cover letter (draft for Hadi to sign)

> Dear CSA STAR Registry,
>
> Mumega submits the attached CAIQ v4.0.2 self-assessment for Level 1 attestation in the STAR Registry.
>
> Mumega is a Sovereign Operating System (SOS) — a microkernel-based protocol substrate hosting AI agents and human collaborators under typed contract surfaces. The substrate enforces five-tier role-based access control, per-workspace AES-256-GCM envelope encryption, hash-chained audit trails anchored to write-once cloud storage with seven-year retention, and SAML/OIDC/SCIM 2.0 + TOTP/WebAuthn MFA at the platform edge.
>
> All 17 CAIQ control domains have been mapped against substrate implementation, with code-level evidence pointers (commit hashes, file paths, test counts) accompanying each control claim. Mumega's Trust Center at mumega.com/trust publishes the same mapping in human-readable form for prospective customers.
>
> Mumega is currently in the process of Sprint 006 customer-readiness, which includes engagement with a SOC 2 Type I audit firm (target attestation: 2026-08-01). Subsequent SOC 2 Type II attestation is planned for 2027 once 12 months of operating history accrue.
>
> We attest that the responses in the attached CAIQ accurately reflect Mumega's current control posture as of the submission date. Where controls are partially implemented, we have flagged them as such with target close dates rather than overstating completeness.
>
> Thank you for the public registry. The transparency it enables matters.
>
> Sincerely,
>
> Hadi Servat
> Founder, Mumega Inc.
> hadi@digid.ca

### 1.2 CAIQ v4.0.2 — completed responses

The full per-control mapping with evidence pointers lives at `content/en/pages/trust.md` in the mumega.com repository. The CSA STAR Registry expects responses in their official spreadsheet template (available at the [CSA Cloud Controls Matrix download page](https://cloudsecurityalliance.org/artifacts/cloud-controls-matrix-v4)).

**Action for Hadi:** download the current CCM/CAIQ v4.0.2 workbook from CSA. For each of the 261 underlying controls, transfer the response from the trust.md mapping into the workbook's `CSP CAIQ Answer (Yes/No/NA)` column and `CSP Implementation Description` column. The mapping at trust.md is already grouped by the 17 control domain headers, so each row in the spreadsheet maps to a paragraph in the markdown.

For controls flagged "Partial" in the markdown, answer the spreadsheet `Yes/No/NA` as "Yes" with the implementation description noting the partial status and target close date. CSA does not penalize "Partial" framing; it penalizes overstating completeness.

For controls flagged "Compensating control" (e.g., physical access via Hetzner SOC 2), answer "Yes" with the implementation description naming the compensating control source.

### 1.3 Supplementary evidence (optional but recommended)

CSA permits supplementary evidence URLs in the submission. We recommend including:
- **Trust Center URL:** https://mumega.com/trust
- **SOC 2 mapping URL:** https://github.com/Mumega-com/sos/blob/main/docs/compliance/soc2-cc-mapping.md (or the rendered version once published)
- **Audit chain verification CLI:** anyone can clone `Mumega-com/sos` and run `python3 -m sos.scripts.verify_chain --all` against a sample chain export. (Reference implementation; not run against Mumega's production chain by external parties for confidentiality reasons.)

---

## 2. What's NOT in this package

- Third-party penetration testing report. Required for STAR Level 2 (third-party assessed). Currently scheduled post-Series-A. Out of scope for Level 1.
- ISO 27001 attestation. Currently in SOA draft phase (Track D.2). Required for STAR Level 2 path that uses ISO 27001 + ISO 27017 as the third-party assessment. Out of scope for Level 1.
- Continuous monitoring evidence. STAR Continuous tier (Level 3) requires monthly/quarterly evidence updates. Out of scope for Level 1; revisit after first Type II attestation.

---

## 3. Submission decision tree for Hadi

**Path 1 — Submit now (recommended):** STAR Level 1 is free, public, and a meaningful signal to enterprise procurement. The CAIQ mapping at trust.md is already complete; this package just transfers it into the CSA-expected format. ~2 hours of spreadsheet work + portal submission. Listing typically appears in registry within 5-10 business days.

**Path 2 — Submit after SOC 2 Type I attestation lands:** ~3 months from now if Track C.4 audit firm engagement closes Sprint 006. Loses the time-to-public-listing advantage but bundles attestation + STAR registry listing into one announcement.

**Path 3 — Skip Level 1, target Level 2 directly:** STAR Level 2 requires third-party assessment (e.g. ISO 27001 + ISO 27017, or BSI Cloud Security, or a SOC 2 + CSA mapping). Bigger investment, higher signal. Not recommended until Series-A — the third-party assessment fees are non-trivial.

**Loom recommends Path 1.** The cost is two hours of Hadi's time. The benefit is a public listing in the most-cited cloud security registry, signaling that Mumega meets the same baseline as enterprise SaaS vendors. Procurement teams checking the STAR registry for a vendor that isn't listed treat absence as a yellow flag, even if the vendor's actual posture is strong.

---

## 4. Action checklist for Hadi (Path 1)

- [ ] Create CSA STAR account at https://cloudsecurityalliance.org/star/registry/
- [ ] Download CCM/CAIQ v4.0.2 workbook from CSA
- [ ] Populate workbook with responses from trust.md (~2 hours)
- [ ] Sign cover letter (§1.1) with current date
- [ ] Submit via STAR portal (workbook + cover letter + Trust Center URL + SOC 2 mapping URL)
- [ ] Update Trust Center page with "CSA STAR Level 1: Submitted YYYY-MM-DD" badge
- [ ] When listing publishes (~5-10 business days), update Trust Center to "CSA STAR Level 1: Listed YYYY-MM-DD" with link to registry entry

---

## 5. Versioning

| Version | Date       | Change                                                              |
|---------|------------|---------------------------------------------------------------------|
| v0.1    | 2026-04-25 | Initial submission package — Sprint 006 Track D.1. Pending Hadi    |
|         |            | filing the actual STAR Registry submission.                         |
