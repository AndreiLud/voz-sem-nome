# Reference lists

The anonymizer's name gazetteer (`src/enps/anonymize/gazetteer.py`) and place gazetteer
(`src/enps/anonymize/places.py`, ported to `worker/anonymize/places.js`) read the files in this
folder. They are downloaded once by `scripts/fetch_ibge_names.py` and
`scripts/fetch_ibge_places.py`; the anonymizer never calls the network.

| File | Content |
| --- | --- |
| `ibge_first_names_2022.csv` | The 3,000 most frequent first names in Brazil |
| `ibge_surnames_2022.csv` | The 1,500 most frequent surnames in Brazil |
| `ibge_municipalities.csv` | All 5,571 municipalities |
| `ibge_districts.csv` | All 10,751 districts |
| `ibge_subdistricts.csv` | All 646 subdistricts |
| `place_word_classes.csv` | Words of place names that are also ordinary words (derived, see below) |

## Names

Columns: `name` (lowercase, without accents, as published), `frequency` (number of people with that
name in the census), `rank` (1 is the most frequent).

Source: Instituto Brasileiro de Geografia e Estatística (IBGE), Censo Demográfico 2022, name
rankings for Brazil as a whole (`localidade/0`), served by the IBGE data API:

* First names: `https://servicodados.ibge.gov.br/api/v3/nomes/2022/localidade/0/ranking/nome?page=N`
* Surnames: `https://servicodados.ibge.gov.br/api/v3/nomes/2022/localidade/0/ranking/sobrenome?page=N`

Each page has 30 entries. The script reads pages 1 to 100 (first names) and 1 to 50 (surnames) with a
0.2 s pause between requests. Retrieved on 2026-11-11.

Names that are also common Portuguese words (rosa, clara, luz...) are handled by
`config/name_stoplist.txt`, not by editing these files.

## Places

Source: IBGE, Divisão Territorial Brasileira, served by the localities API with the flat view
(`?view=nivelado`):

* Municipalities: `https://servicodados.ibge.gov.br/api/v1/localidades/municipios`
* Districts: `https://servicodados.ibge.gov.br/api/v1/localidades/distritos`
* Subdistricts: `https://servicodados.ibge.gov.br/api/v1/localidades/subdistritos`

Retrieved on 2026-11-11. The files keep the names exactly as published, one row per locality, sorted
by IBGE code. Columns: `id` (IBGE code), `name`, the codes and names of the units above it
(`district_id`, `district`, `municipality_id`, `municipality`) and `uf` (state).

Districts cover the neighborhoods of São Paulo (Perus, Itaquera, Jardim Ângela...). Subdistricts add
neighborhoods of other big cities, such as Rio de Janeiro (Copacabana, Tijuca, Bangu), Salvador and
the administrative regions of Brasília. The lexicon (`src/enps/anonymize/lexicon.py`,
`place_name_ok`) skips names it cannot match in text or that point to no place in particular:
administrative labels with digits, dots, slashes, quotes or brackets ("Zona 01", "U.T.P. Central",
"RA 02 - Santa Lúcia"), names with words such as distrito, subdistrito, região, administração, setor,
núcleo or zona, all-caps acronyms, and directions or centers alone (Norte, Centro-Sul, Sede, Central,
Industrial). From these lists the anonymizer uses 9,297 distinct names (normalized without case or
accents), taken from 5,570 of the 5,571 municipality rows (all but "Central"), 10,726 of the 10,751
district rows and 171 of the 646 subdistrict rows.

### Word classes

`place_word_classes.csv` lists the words of those names that are also ordinary Portuguese words,
with columns `word` (normalized), `class`, `lower_rank` and `capitalized_rank`. It is derived, not
downloaded: `scripts/fetch_ibge_places.py` compares each word's lowercase and capitalized forms in
the vector table of the spaCy model `pt_core_news_md` 3.8.0, which lists 500,000 word forms from the
most to the least frequent, with case and accents. A form's position is its frequency rank; forms
with and without accents both count ("Sé" is judged with "se").

* `common`: the capitalized form is less than half as frequent as the lowercase one ("saúde",
  "liberdade", "contagem"). As one word, the place matches only capitalized right after a cue.
* `mixed`: between half and twice as frequent ("palmas", "perus", "gramado"). As one word, the place
  matches capitalized inside a sentence, never at its start.
* Words not listed are proper names ("guarulhos", "campinas"): capitalized anywhere, and in lowercase
  after a cue.

Place names that usually mean something else (holidays, saints, brands, clubs, workplace words) are
listed by hand in `config/anonymizer.yaml` (`places.stoplist`), not in these files.

## Terms

These are public statistics published by IBGE. Reuse should cite the source, for example
"Fonte: IBGE, Censo Demográfico 2022" and "Fonte: IBGE, Divisão Territorial Brasileira". Check the
current terms at https://www.ibge.gov.br before redistributing the files outside this project.

## Refreshing

```
.venv/bin/python scripts/fetch_ibge_names.py --first-names 3000 --surnames 1500
.venv/bin/python scripts/fetch_ibge_places.py                 # lists and word classes
.venv/bin/python scripts/fetch_ibge_places.py --classes-only  # word classes only, no network
.venv/bin/python scripts/build_worker_data.py                 # then rebuild the Worker word lists
```
