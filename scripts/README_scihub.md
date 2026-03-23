# Sci-Hub Knowledge Ingestion for Polymarket

Dessa skript ingesterar vetenskaplig kunskap från Sci-Hub till Neo4j-databasen för Polymarket-pluginet.

## Bakgrund

Sci-Hub har ingen officiell API för sökning. Därför finns två skript:

1. **scihub_curated_ingestion.py** - Använder en fördefinierad lista med metadata för viktiga artiklar (rekommenderas)
2. **scihub_knowledge_ingestion.py** - Försöker hämta metadata direkt från Sci-Hub via DOI (experimentellt)

## Användning

### Recommenend: Kuraterad ingestion

```bash
# Ingestera alla kuraterade artiklar
python scripts/scihub_curated_ingestion.py

# Med Neo4j-lösenord
python scripts/scihub_curated_ingestion.py --password YOUR_PASSWORD

# Med extra loggning
python scripts/scihub_curated_ingestion.py --verbose
```

### Experimentell: Direkt från Sci-Hub

```bash
# Ingestera kända DOI-nummer
python scripts/scihub_knowledge_ingestion.py --known-only

# Sök på Sci-Hub
python scripts/scihub_knowledge_ingestion.py --search "prediction market"

# Båda
python scripts/scihub_knowledge_ingestion.py --search "forecasting" --known-only
```

## Inkluderade kategorier

- **prediction_markets** - Prediktionsmarknadsteori
- **forecasting** - Prognosmetoder
- **behavioral_economics** - Beteendekonomi
- **decision_theory** - Beslutsteori
- **market_microstructure** - Marknadsstruktur
- **superforecasting** - Superprognostik

## Viktiga artiklar som inkluderas

1. "Information Aggregation in a Prediction Market" (Wolfers & Zitzewitz)
2. "Prospect Theory: An Analysis of Decision under Risk" (Kahneman & Tversky)
3. "Judgment under Uncertainty: Heuristics and Biases" (Tversky & Kahneman)
4. "Superforecasting: The Art and Science of Prediction" (Tetlock & Gardner)
5. Och många fler...

## Nyttjande i Polymarket

Kunskapsbasen kan nås via `KnowledgeIngestion.query_context()`:

```python
from overblick.plugins.polymarket_monitor.knowledge_ingestion import KnowledgeIngestion

kg = KnowledgeIngestion()
await kg.connect()
context = await kg.query_context("Will BTC reach $100k?", "crypto")
print(context)
```

## Underhåll

### Lägga till fler artiklar

Redigera `CURATED_PAPERS` i `scripts/scihub_curated_ingestion.py`:

```python
CURATED_PAPERS = {
    "prediction_markets": [
        {
            "doi": "10.xxxx/yyyy",
            "title": "Article Title",
            "authors": ["Author 1", "Author 2"],
            "journal": "Journal Name",
            "year": "2024",
            "abstract": "Abstract text...",
        },
    ],
}
```

### Rensa och köra om

```bash
# Rensa gamla artiklar (VARSAM!)
# python -c "from neo4j import GraphDatabase; d = GraphDatabase.driver(...); ..."
# Kör sedan om ingestion
python scripts/scihub_curated_ingestion.py
```

## Obs

- Sci-Hub-metadata används enbart för akademiska ändamål
- Fulltext av artiklar hämtas ej, endast metadata
- Kunskapsbasen används av Polymarket-agenten för att förbättra marknadsanalyser
