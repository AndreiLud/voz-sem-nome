# Reference name lists

The anonymizer's name gazetteer (`src/enps/anonymize/gazetteer.py`) reads two CSV files from this folder.
They are downloaded once by `scripts/fetch_ibge_names.py`; the anonymizer never calls the network.

| File | Content |
| --- | --- |
| `ibge_first_names_2022.csv` | The 3,000 most frequent first names in Brazil |
| `ibge_surnames_2022.csv` | The 1,500 most frequent surnames in Brazil |

Columns: `name` (lowercase, without accents, as published), `frequency` (number of people with that
name in the census), `rank` (1 is the most frequent).

## Source

Instituto Brasileiro de Geografia e Estatística (IBGE), Censo Demográfico 2022, name rankings for
Brazil as a whole (`localidade/0`), served by the IBGE data API:

* First names: `https://servicodados.ibge.gov.br/api/v3/nomes/2022/localidade/0/ranking/nome?page=N`
* Surnames: `https://servicodados.ibge.gov.br/api/v3/nomes/2022/localidade/0/ranking/sobrenome?page=N`

Each page has 30 entries. The script reads pages 1 to 100 (first names) and 1 to 50 (surnames) with a
0.2 s pause between requests.

Retrieved on 2026-11-11.

## Terms

These are public statistics published by IBGE. Reuse should cite the source, for example
"Fonte: IBGE, Censo Demográfico 2022". Check the current terms at https://www.ibge.gov.br before
redistributing the files outside this project.

## Refreshing

```
.venv/bin/python scripts/fetch_ibge_names.py --first-names 3000 --surnames 1500
```

Names that are also common Portuguese words (rosa, clara, luz...) are handled by
`config/name_stoplist.txt`, not by editing these files.
