# Synthetic survey data

The demo and the evaluation use 400 fictitious answers from employees of "Talvana Distribuição", a made-up
distribution company with a warehouse, trucks, a call center, sales, technology, finance, people and legal teams.
No real person answered this survey.

```
PYTHONPATH=src .venv/bin/python -m enps.synth                    # seed 20261111, writes data/synthetic/
PYTHONPATH=src .venv/bin/python -m enps.synth --seed 7 --out /tmp/synth --quiet
```

The command prints a summary: answers and eNPS by area and tenure, theme and sentiment counts, critical cases by
type and the share of subtle ones, planted personal data by type, and the size of each split.

## Files

| File | Content |
|---|---|
| `phrasebank.yaml` | Curated sentences and templates. The only source of text. |
| `responses.csv` | `id, submitted_at, score, area, tenure, comment`. The comment may be empty. |
| `intended_labels.csv` | `id, themes, sentiment, critical, critical_types, subtle, traits`. |
| `pii_gold.jsonl` | One line per comment with planted personal data: `start`, `end`, `type`, `text`. |
| `splits.json` | `seed`, `test` (100 ids), `dev` (the other non-empty comments), `dev_quick` (40 ids from dev). |

Offsets in `pii_gold.jsonl` are Python string offsets into the exact comment, so `comment[start:end] == text`.
A matrícula span covers the number only, not the word "matrícula" before it.

## How a comment is made

1. **Respondents.** Each area gets its exact size and a score list built to land near its target eNPS. Inside an
   area, scores are matched to a hidden mood that depends on tenure plus random noise, so newcomers score higher
   and people with 1 to 3 years score lowest. Each answer gets a time on a business day between 2026-10-19 and
   2026-10-30, from 08:00 to 19:00 (offset -03:00). Ids follow submission order.
2. **Plan.** Critical cases are placed first (mostly low scores, mostly in customer service and operations), then
   empty and very short answers, then rants, near misses, word decoys and comments with personal data. Everyone
   else gets a regular comment. Each special template is used once.
3. **Fragments.** A regular comment has 1 to 4 fragments. Themes are drawn with weights that follow each area's
   story, and the polarity of each fragment is drawn from the score with noise: some 9s still complain and some
   6s are mild. Rarely used fragments are preferred, so few comments repeat.
4. **Style.** Each comment gets a writing style by area: formal, standard, casual or informal. Casual and informal
   comments are typed in lower case, drop accents, use abbreviations (vc, pq, q, mt, n, pra, tá, fds) and have a
   few typos (swapped, dropped or doubled letters), repeated letters and "kkk". Slang and irony come from the
   fragments themselves.
5. **Personal data.** Template slots are filled with fake values and copied verbatim. Noise never touches them,
   and offsets are computed while the pieces are joined.

## Distributions (seed 20261111)

| Area | n | eNPS | Target |
|---|---|---|---|
| operacoes | 110 | -25 | -25 |
| transporte | 60 | -17 | -15 |
| atendimento | 80 | -38 | -35 |
| comercial | 55 | +13 | +10 |
| tecnologia | 45 | +40 | +40 |
| financeiro | 30 | +10 | +5 |
| pessoas | 16 | +19 | +20 |
| juridico | 4 | 0 | 0 |

Overall eNPS is -9. By tenure: lt1 +29, y1to3 -29, y3to5 -18, gt5 -4. The legal team has 4 answers on purpose,
below the minimum group size of 5, so the dashboard must merge it.

* 60 empty comments (15%), 32 one-word or very short ones (8%), the rest 1 to 4 sentences.
* Themes follow the area: workload, targets and leadership in customer service; pay, workload, safety and tools in
  operations; long days, shifts and safety in transport; career, flexibility and recognition in technology;
  variable pay and targets in sales; processes and communication in finance. Culture, recognition,
  communication and flexibility are less frequent. "out" is always alone.
* 32 critical comments: 9 am, 6 as, 6 di, 8 bo (2 of them indirect mentions of not wanting to live), 4 av, with
  one comment carrying two types. 21 of 32 (66%) are subtle: they describe the situation without words such as
  assédio, burnout, discriminação, racismo, ameaça or violência.
* 12 near misses use those words in a harmless way ("aqui nunca vi assédio", "essa meta é uma ameaça pro meu fim
  de semana kkk"). They are labeled not critical with the trait `decoy`.
* 60 comments (15%) carry 104 planted values: 47 PESSOA, 13 LOCAL, 8 EMAIL, 9 TELEFONE, 6 CPF, 5 RG, 4 CEP,
  8 MATRICULA, 4 PLACA.

## Labels

* `themes`: codes from `config/taxonomy.yaml`, joined with `|`, from the fragments used.
* `sentiment`: each fragment has a polarity and a weight (1 for a normal sentence, 2 for a rant, 3 for a critical
  case). Positive adds the weight, negative subtracts it, neutral adds nothing. A positive total is `pos`, a
  negative total is `neg`, zero is `neu`. Irony counts as negative. A comment with only neutral sentences is
  `neu`. Empty comments have empty labels.
* `critical`, `critical_types`, `subtle`: from the critical template, if any.
* `traits`: `empty`, `one_word`, `short`, `formal`, `rant`, `irony`, `mixed` (positive and negative sentences),
  `slang`, `abbreviations`, `typos`, `repeated_letters`, `laughter`, `pii`, `pii_decoy` (numbers or name-like words
  that are not personal data), `decoy` (near miss), `self_harm`.

## Splits

Only non-empty comments enter the splits. `test` has 100 comments with at least 20 critical (3 or more of each
type), 5 near misses, 12 with personal data, 8 very short ones and every theme at least 6 times; the rest is
filled by area. `dev` is every other non-empty comment. `dev_quick` takes 40 from dev with at least 8 critical
(every type at least once), 3 near misses, 4 with personal data, 2 very short ones and every theme at least once.

## Safety of the fake personal data

* CPFs always have a wrong first check digit, so no validator accepts them. Both `123.456.789-01` and
  `12345678901` styles appear.
* E-mails only use `example.com`, `example.org` (reserved for examples) or `talvana.example`.
* Phone numbers use a real area code but the mobile part starts with 90, which is not used by mobile lines.
* Names are common Brazilian first names and surnames combined at random. Rosa, Clara, Vitória, Luz and Graça
  appear as names in some comments and as ordinary words in others.
* Addresses use generic street names and random numbers. Cities and neighborhoods are real places, used only as
  where someone lives.
* Numbers that look like data but are not: amounts in R$, percentages, "13º", "6x1", "nota 10", dates, times,
  order and invoice numbers, years at the company.

## Reproducibility

Every stage uses its own `random.Random` seeded from the main seed and the stage name. Nothing depends on set
order or hash randomization, and a test checks that the same seed writes byte-identical files.

## Limitations

* Comments are composed from 699 curated entries (537 sentences, 51 critical cases, 111 templates with
  personal data). They are more regular than real answers: sentence boundaries are clean, themes rarely blur
  into each other, and about a quarter of the sentences used appear in more than one comment, with different
  noise.
* The noise is rule based. Real typos follow keyboard layouts and autocorrect, and real slang changes by region and
  age in ways a short list cannot cover.
* Labels are intended labels: they come from how each comment was built, not from people reading it. A sentence
  can carry a tone its label does not capture, especially when fragments are joined.
* Critical cases are written to be realistic but they are few and clean. Real reports are often longer, mix several
  issues, or hint at a problem across many answers.
