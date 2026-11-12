# How Voz sem nome handles personal data

This is a portfolio project, not legal advice. It shows how a climate survey pipeline can be built so that
personal data is removed before any analysis. A company using something like it should review the choices below
with its data protection officer (encarregado) and legal team.

Brazilian law referenced: Lei Geral de Proteção de Dados (LGPD, Lei 13.709/2018).

The system runs in two ways:

* **Live demo (Cloudflare).** The public site, the anonymizer, the model and the database all run on Cloudflare.
  Nothing runs on a home server. Processing and storage happen outside Brazil.
* **Local mode (own server).** The same pipeline runs with `docker compose` on the organization's own server
  (spaCy NER, Ollama with Gemma 4 E2B, SQLite, n8n). Nothing leaves that infrastructure. The published analysis
  of the synthetic dataset and the CPU benchmark come from this mode.

## What the demo collects

| Field | Why | Stored as |
|---|---|---|
| Score from 0 to 10 | eNPS | number |
| Area | results by area | code from a fixed list |
| Tenure band | results by tenure | code from a fixed list |
| Open comment | themes, sentiment, critical cases | **anonymized text only** |

The demo company is fictitious and the form asks people not to write real data. People sometimes do anyway,
so the pipeline treats every comment as if it could contain personal data.

## Live demo: where the data goes

| Part | What it does |
|---|---|
| Turnstile | bot check before the answer is accepted |
| Worker | validates the answer, anonymizes the comment, stores the result, records alerts |
| Workers AI | classifies the anonymized comment with Gemma 4 26B A4B (`@cf/google/gemma-4-26b-a4b-it`) |
| D1 | database for anonymized comments, codes for area and tenure, scores, classifications and alerts |

**Roles.** The site owner is the controller (controlador) and Cloudflare is the processor (operador). The
[Cloudflare Customer DPA](https://www.cloudflare.com/cloudflare-customer-dpa/) states that "the Customer is the
Controller" and that "Cloudflare is a Processor". Cloudflare describes its LGPD position on its
[LGPD page](https://www.cloudflare.com/trust-hub/brazil-lgpd/). The DPA itself does not mention the LGPD or
the ANPD standard clauses, which a real company should review with its legal team.

**Anonymization happens inside the Worker, before the model.** Rules for Brazilian formats (CPF, CNPJ, RG, phone,
email, CEP, employee ID, vehicle plate, street address) and a list of common Brazilian first names from IBGE
replace personal data with markers such as `[PESSOA]` and `[EMAIL]`. There is no NER at the edge, so place names
slip through more easily than in local mode. The measured difference goes to `metrics/metrics.json`, and the site
shows "not measured yet" until the Worker has been evaluated.
Only the anonymized text reaches Workers AI and D1.

**What Cloudflare says about Workers AI content.** From the
[Workers AI data usage page](https://developers.cloudflare.com/workers-ai/platform/data-usage/):

* "Cloudflare does not use Customer Content processed by Workers AI to train AI models."
* For standard inference requests, Cloudflare "does not retain inputs or outputs after they are no longer needed
  to process the request" and to deliver the response. The same page lists exceptions, such as legal obligations.
* Cloudflare may keep operational metadata (timestamps, model ids, token counts, latency), without the content.

The Worker calls the model directly (no AI Gateway logging) and never writes comment text to its logs.

**Outside Brazil.** Cloudflare lists third party GPU sub-processors for Workers AI in the United States and in
England ([sub-processor list](https://www.cloudflare.com/gdpr/subprocessors/cloudflare-services/)). D1 has no
location in Brazil or South America
([D1 data location](https://developers.cloudflare.com/d1/configuration/data-location/)). Both are an
international data transfer under LGPD art. 33, and the demo form says so next to the consent checkbox.

**Turnstile.** The bot check processes the visitor's IP address, TLS fingerprint and user agent
([Turnstile privacy addendum](https://www.cloudflare.com/turnstile-privacy-policy/)). The token is verified once
and dropped. The addendum makes Cloudflare a processor of these signals when it runs the check, and a controller
when it uses them to improve its bot detection.

## Minimization, step by step

1. **Anonymization before anything is stored or sent to the model.** The raw comment exists only in the memory
   of the request that receives it. What is kept for audit is the anonymizer version and how many items of each
   type were removed, never the removed values.
2. **No raw text anywhere.** Not in D1, not in logs, not in alerts, not in n8n.
3. **No IP addresses stored.** The IP is the key of Cloudflare's rate limiting binding, which keeps only short
   lived counters, and it is never written to D1. Distinct people per day are counted with an HMAC of the IP
   under a salt that changes every day; yesterday's hashes and salt are deleted by the daily job, and only the
   count stays.
4. **Tracking codes** are shown once to the person who submitted; the database keeps only their hash.

Measured quality of step 1, for the Worker and for local mode, goes to `metrics/metrics.json` (recall and
precision by type of data, on a synthetic set with planted fake data). A number that was not measured yet is shown
as such on the site. Recall is the number that matters: it is
the share of personal data that was removed.

## Who sees what

| Who | Sees |
|---|---|
| Anyone on the site | aggregates for groups with at least k answers (k = 5, configurable), anonymized excerpts of critical cases, and their own result through the tracking code |
| The site owner (controller) | the D1 database: anonymized comments, codes for area and tenure, scores, classifications, alert log |
| Cloudflare (processor) | connection data to serve the site and run Turnstile; the anonymized comment while Workers AI classifies it; the D1 data at rest |
| n8n, only when a webhook is set up | critical alerts: anonymized excerpt, case types, area when visible |

**Minimum group size.** No area, tenure band or combination with fewer than k answers is shown. Small groups are
merged into "other areas", and when that merged group would still be small, the next smallest group joins it, so
no hidden group can be recovered by subtracting the visible ones from the total. In the list of critical cases the
area appears only when that area is visible; tenure never appears.

## Retention

| Data | Kept for |
|---|---|
| Public submissions (anonymized) | 30 days, then deleted by the Worker's daily job |
| Alert log (ids, types, timestamps, no text) | 90 days |
| Daily IP hashes for distinct counts | until the next day |
| Worker request logs ([Workers Logs](https://developers.cloudflare.com/workers/observability/logs/workers-logs/), no comment text) | 3 days on the Workers Free plan, kept by Cloudflare |
| Daily counters (no text, no identifiers) | indefinitely, for the usage metrics |
| Synthetic dataset | versioned in the repository; it is fictitious |

**D1 Time Travel.** D1 keeps a point in time history that is always on: deleted rows can be restored for 7 days
on the Workers Free plan ([D1 Time Travel](https://developers.cloudflare.com/d1/reference/time-travel/)). A
public submission is therefore gone for good at most 37 days after it was sent. Any promise to delete data on
request has to account for this window.

## Local mode

Everything above that mentions Cloudflare does not apply. The comment is anonymized by the same rules plus the
spaCy entity recognizer, classified by Gemma 4 E2B through Ollama on the server's CPU, and stored in SQLite on the
same machine. n8n sends the alerts, the daily digest and deletes old submissions. No comment goes to Cloudflare
or any other external service, except optional alert channels the organization turns on (see below). This is the
mode for organizations that need nothing to leave their own infrastructure.

## Suggested legal basis for a real company

* **Survey answers without sensitive data:** legitimate interest (art. 7, IX), supported by a legitimate interest
  assessment, clear notice to employees and easy opt out. Consent is weak in employment because of the
  subordination between employer and employee.
* **Comments may contain sensitive data** (health, origin, religion, sex life, art. 5, II), for example a mention
  of burnout or discrimination. Two arguments support processing them: compliance with a legal or regulatory
  obligation (art. 11, II, a), given the risk management duties of NR-1 for psychosocial risks and the
  harassment prevention duties of Lei 14.457/2022; and the anonymization itself, since anonymized data is not
  personal data when the process cannot be reversed with reasonable effort (art. 12).
* **International transfer:** running on Cloudflare means processing in other countries, which needs a basis
  under art. 33. Local mode avoids the transfer.
* **The public demo** relies on the consent checkbox in the form (art. 7, I) for the text people choose to type;
  the checkbox says the analysis runs on Cloudflare outside Brazil.

## Optional channels

The repository includes n8n workflows that are off by default: reading answers from Google Forms, and sending
alerts by Telegram or email. If you turn them on:

* Google Forms keeps the **raw** answers in the Google account that owns the form, outside this pipeline. Set a
  retention routine there too.
* Telegram and email alerts carry only the anonymized excerpt, the type of case, the area when it is visible and
  a link to the dashboard.
