# Metacritic — GTA VI × Ocarina remake

Cruza user reviews do **GTA VI** com o remake de **The Legend of Zelda: Ocarina of Time (2026)** nos dois sentidos:

- nota baixa no GTA + nota alta no Zelda?
- nota baixa no Zelda + nota alta no GTA?

Artigo com os resultados: [Guerra de notas no Metacritic (aisland.com.br)](https://aisland.com.br/2026/11/21/guerra-de-notas-no-metacritic-cruzamento-de-dados-expoe-fogo-cruzado-entre-fas-de-gta-vi-e-zelda/)

## Dataset (snapshot 21/11/2026)

Dois scrapes paralelos de reviews **com texto** (a API não lista notas sem comentário):

| Fonte | Arquivo | Qtd |
| --- | --- | ---: |
| GTA VI | `data/gta_vi_reviews.json` | **21.851** |
| OoT remake 2026 | `data/zelda_oot_2026_reviews.json` | **10.982** |
| **Total reviews com texto** | | **~32.8k** |
| Perfis de usuário | `data/user_profiles/` | **7.981** |

O GTA passa de ~10k porque o scraper faz merge de `positive` + `neutral` + `negative` (+ `all`), cada um com teto próprio na API. No site, o GTA tem ~60k notas no total — a maioria sem texto.

## Setup

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

## Passos (separados)

```bash
# 1) Reviews do GTA VI  → data/gta_vi_reviews.json
python scrape_and_analyze.py scrape-gta

# 2) Reviews do OoT remake 2026  → data/zelda_oot_2026_reviews.json
python scrape_and_analyze.py scrape-zelda

# 3) Perfis dos usuários com nota baixa no GTA  → data/user_profiles/
python scrape_and_analyze.py scrape-users --low-max 1

# 4) Cruzar (ex.: GTA < 5 e OoT = 10)
python scrape_and_analyze.py analyze --low-max 4 --zelda-min 10
```

Saídas: `data/analysis_report.json`, `data/hypothesis_matches.json`, `data/gta_vi_stats.json`, `data/match_profile_stats.json`.

Entre os matches rivalistas, ~**63%** das contas só avaliaram GTA VI + OoT remake (mediana **2** jogos) — ver `data/match_profile_stats.json`.

## Limitações

- **Só reviews com texto.** Dezenas de milhares de notas sem comentário entram na média do site, mas **não** têm autor listável na API — impossível cruzar com o outro jogo.
- **API com teto ~10k** por filtro de sentimento; o scraper faz merge de positive/mixed/negative/all, mas a cobertura de texto não é 100%.
- Os percentuais altos (~90%+) aplicam-se a **quem avaliou os dois**; não à população inteira de notas baixas.
- Correlação de contas ≠ prova causal de review bomb em cada caso.
- Sem verificação de ownership/compra no Metacritic.
- Dados são um **snapshot**; o site muda o tempo todo.
- `scrape-users` cobre só um subconjunto de perfis (low-scorers do GTA), não todos os usuários.
