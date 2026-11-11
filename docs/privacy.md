# How Voz sem nome handles personal data

This is a portfolio project, not legal advice. It shows how a climate survey pipeline can be built so that
personal data is removed before any analysis. A company using something like it should review the choices below
with its data protection officer (encarregado) and legal team.

Brazilian law referenced: Lei Geral de Proteção de Dados (LGPD, Lei 13.709/2018).

## What the demo collects

| Field | Why | Stored as |
|---|---|---|
| Score from 0 to 10 | eNPS | number |
| Area | results by area | code from a fixed list |
| Tenure band | results by tenure | code from a fixed list |
| Open comment | themes, sentiment, critical cases | **anonymized text only** |

The demo company is fictitious and the form asks people not to write real data. People sometimes do anyway,
so the pipeline treats every comment as if it could contain personal data.

## Minimization, step by step

1. **Anonymization before anything is stored.** The comment exists in memory only while the request is handled.
   Rules for Brazilian formats (CPF, CNPJ, RG, phone, email, CEP, employee ID, vehicle plate, street address)
   and a Portuguese entity recognizer plus a list of common Brazilian first names replace personal data with
   markers such as `[PESSOA]` and `[EMAIL]`. Only the result is written to the database.
2. **The model only sees anonymized text,** and it runs on the same server. No comment is sent to an external AI
   service.
3. **No raw text anywhere.** Not in the database, not in logs, not in n8n (execution data is not saved), not in
   alerts. What is kept for audit is the anonymizer version and how many items of each type were removed, never
   the removed values.
4. **No IP addresses on disk.** Rate limits keep counters in memory. The count of distinct people per day uses a
   hash with a salt that changes every day and lives only in memory; only the number is stored.
5. **Tracking codes** are shown once to the person who submitted; the database keeps only their hash.

Measured quality of step 1 is published in `metrics/metrics.json` (recall and precision by type of data, on a
synthetic set with planted fake data). Recall is the number that matters: it is the share of personal data that
was removed.

## Who sees what

| Who | Sees |
|---|---|
| Anyone on the site | aggregates for groups with at least k answers (k = 5, configurable), anonymized excerpts of critical cases, and their own result through the tracking code |
| The operator of the server | the database: anonymized comments, codes for area and tenure, scores, classifications, alert log |
| The local model | the anonymized comment being classified |
| Cloudflare | requests in transit, as the hosting and tunnel provider. The Worker does not log request bodies |

**Minimum group size.** No area, tenure band or combination with fewer than k answers is shown. Small groups are
merged into "other areas", and when that merged group would still be small, the next smallest group joins it, so
no hidden group can be recovered by subtracting the visible ones from the total. In the list of critical cases the
area appears only when that area is visible; tenure never appears.

## Retention

| Data | Kept for |
|---|---|
| Public submissions (anonymized) | 30 days, then deleted by a daily n8n job |
| Alert log (ids, types, timestamps, no text) | 90 days |
| Daily counters (no text, no identifiers) | indefinitely, for the usage metrics |
| Synthetic dataset | versioned in the repository; it is fictitious |

## Suggested legal basis for a real company

* **Survey answers without sensitive data:** legitimate interest (art. 7, IX), supported by a legitimate interest
  assessment, clear notice to employees and easy opt out. Consent is weak in employment because of the
  subordination between employer and employee.
* **Comments may contain sensitive data** (health, origin, religion, sex life, art. 5, II), for example a mention
  of burnout or discrimination. Two arguments support processing them: compliance with a legal or regulatory
  obligation (art. 11, II, a), given the risk management duties of NR-1 for psychosocial risks and the
  harassment prevention duties of Lei 14.457/2022; and the anonymization itself, since anonymized data is not
  personal data when the process cannot be reversed with reasonable effort (art. 12).
* **The public demo** relies on the consent checkbox in the form (art. 7, I) for the text people choose to type.

## Optional channels

The repository includes n8n workflows that are off by default: reading answers from Google Forms, and sending
alerts by Telegram or email. If you turn them on:

* Google Forms keeps the **raw** answers in the Google account that owns the form, outside this pipeline. Set a
  retention routine there too.
* Telegram and email alerts carry only the anonymized excerpt, the type of case, the area when it is visible and
  a link to the dashboard.
