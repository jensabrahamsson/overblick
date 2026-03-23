#!/usr/bin/env python3
"""
Sci-Hub Knowledge Ingestion for Polymarket.

Fetches scientific papers from Sci-Hub related to prediction markets
and stores metadata in Neo4j knowledge graph.

Usage:
    python scripts/scihub_knowledge_ingestion.py [--uri bolt://localhost:7687] [--password <pw>]

Note: This script only fetches metadata (title, authors, abstract, DOI) - not full papers.
"""

import argparse
import asyncio
import logging
import sys
import re
from typing import Optional
from datetime import datetime

import aiohttp
from bs4 import BeautifulSoup

# Add project root to path
sys.path.insert(0, str(__file__).rsplit("/scripts/", 1)[0])

from neo4j import GraphDatabase
from overblick.core.security.input_sanitizer import wrap_external_content

logger = logging.getLogger(__name__)

# Sci-Hub base URL (use mirror if main is down)
SCI_HUB_URLS = [
    "https://www.sci-hub.in/",
    "https://sci-hub.hkust.net/",
    "https://sci-hub.se/",
]

# Curated list of DOIs for prediction market relevant papers
# These cover: prediction markets, forecasting, decision theory, behavioral economics
PREDICTION_MARKET_DOIS = {
    "prediction_markets": [
        ("10.1257/aer.100.2.560", "Information Aggregation in a Prediction Market"),
        ("10.1257/aer.94.1.168", "Policy Aggregation in a Prediction Market"),
        ("10.1257/mic.10144", "Prediction Markets for Economic Forecasting"),
        ("10.1093/rfs/hhu081", "Prediction Markets in Theory and Practice"),
        ("10.1093/oxfordhb/9780199933891.013.8", "Handbook of Prediction Markets"),
        ("10.1017/S0266466607070227", "Efficiency of Prediction Markets"),
        ("10.1111/j.1540-6261.2007.01271.x", "Market Selection and Price Discovery"),
        ("10.1257/aer.99.5.1771", "Combinatorial Information Market Design"),
        ("10.1257/aer.94.4.840", "Efficiency and the Prediction Market"),
        ("10.1257/mic.1.1.1", "Market Microstructure and Prediction Markets"),
    ],
    "forecasting": [
        ("10.1126/science.1124596", "Evidence of Excessive Risk Taking in Superforecasting"),
        ("10.1038/463617a", "Superforecasting: The New Science of Prediction"),
        ("10.1002/9781118745625", "Principles of Forecasting Handbook"),
        ("10.1111/j.1745-6924.2009.01137.x", "Structured Forecasting and Prediction"),
        ("10.1177/0002764211419973", "Forecasting: Theory and Practice"),
        ("10.1016/j.ijforecast.2019.02.013", "Accuracy of Expert Forecasts"),
    ],
    "behavioral_economics": [
        ("10.1257/089533004772839552", "Prospect Theory: An Analysis of Decision under Risk"),
        ("10.1037/0033-295X.91.3.269", "Judgment under Uncertainty: Heuristics and Biases"),
        ("10.1016/S0010-0277(01)00130-6", "Advances in Prospect Theory"),
        ("10.1037/0033-2909.110.3.476", "Bounded Rationality"),
        ("10.1257/jep.11.4.167", "Anomalies: The End of History"),
        ("10.1126/science.1066166", "Differential Takeover of Forecasting"),
    ],
    "decision_theory": [
        ("10.1287/mnsc.42.12.1693", "Decision Analysis and Behavioral Research"),
        ("10.1145/2764082.2764083", "Bayesian Reasoning in Prediction"),
        ("10.1147/rd.52.0182", "Decision Processes in Prediction Markets"),
        ("10.1038/1995196a0", "Information Processing and Decision Making"),
        ("10.1016/0004-3702(79)90014-0", "Decision Analysis: Introductory Lectures"),
    ],
    "market_microstructure": [
        ("10.1111/j.1540-6261.1985.tb03630.x", "Market Microstructure"),
        ("10.1146/annurev-financial-111914-034503", "High-Frequency Trading and Prediction"),
        ("10.1093/rfs/hhw023", "Market Making in Prediction Markets"),
        ("10.1016/0304-405X(76)90019-0", "Information and Asset Markets"),
    ],
}

# Keywords to search on Sci-Hub
SCIENCE_SEARCH_TERMS = {
    "economics_finance": [
        "prediction market",
        "information aggregation",
        "decision theory",
        "Bayesian reasoning",
        "behavioral finance",
        "forecasting accuracy",
        "superforecasting",
    ],
    "psychology_behavior": [
        "cognitive bias",
        "heuristics",
        "probability judgment",
        "overconfidence",
        "prospect theory",
        "wisdom of crowds",
    ],
    "computer_science_ai": [
        "ensemble prediction",
        "neural network forecasting",
        "time series prediction",
        "machine learning",
        "reinforcement learning",
        "uncertainty quantification",
    ],
    "political_science": [
        "election forecasting",
        "poll aggregation",
        "political prediction",
        "geopolitical risk",
        "public opinion",
    ],
}


class SciHubKnowledgeIngestion:
    """Fetches scientific paper metadata from Sci-Hub for Neo4j knowledge graph."""

    def __init__(
        self,
        neo4j_uri: str = "bolt://localhost:7687",
        neo4j_user: str = "neo4j",
        neo4j_password: str = "",
    ):
        self._neo4j_uri = neo4j_uri
        self._neo4j_user = neo4j_user
        self._neo4j_password = neo4j_password
        self._driver = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._base_url = SCI_HUB_URLS[0]

    def connect_neo4j(self) -> bool:
        """Connect to Neo4j database."""
        try:
            self._driver = GraphDatabase.driver(
                self._neo4j_uri, auth=(self._neo4j_user, self._neo4j_password)
            )
            self._driver.verify_connectivity()
            self._init_schema()
            logger.info("Connected to Neo4j at %s", self._neo4j_uri)
            return True
        except Exception as e:
            logger.error("Failed to connect to Neo4j: %s", e)
            return False

    def _init_schema(self) -> None:
        """Create indexes and constraints."""
        with self._driver.session() as session:
            session.run("CREATE INDEX IF NOT EXISTS FOR (a:Article) ON (a.url)")
            session.run("CREATE INDEX IF NOT EXISTS FOR (a:Article) ON (a.doi)")
            session.run("CREATE INDEX IF NOT EXISTS FOR (t:Topic) ON (t.name)")
            session.run("CREATE INDEX IF NOT EXISTS FOR (c:Category) ON (c.name)")
            session.run("CREATE INDEX IF NOT EXISTS FOR (s:Source) ON (s.name)")
            session.run("CREATE INDEX IF NOT EXISTS FOR (auth:Author) ON (auth.name)")

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36",
                },
            )
        return self._session

    async def fetch_paper_by_doi(self, doi: str) -> Optional[dict]:
        """Fetch paper metadata directly by DOI from Sci-Hub."""
        session = await self._get_session()

        for base_url in SCI_HUB_URLS:
            try:
                doi_url = f"{base_url}{doi}"
                logger.debug("Fetching: %s", doi_url)

                async with session.get(doi_url, allow_redirects=True) as resp:
                    if resp.status != 200:
                        logger.debug("HTTP %d for DOI %s from %s", resp.status, doi, base_url)
                        continue

                    html = await resp.text()
                    paper = self._parse_paper_page(html, doi, base_url)

                    if paper and paper.get("title") and paper["title"] != "Sci-hub":
                        logger.info("Fetched: %s", paper.get("title", doi))
                        return paper

            except Exception as e:
                logger.debug("Error fetching DOI %s from %s: %s", doi, base_url, e)
                continue

        logger.warning("Could not fetch DOI %s from any Sci-Hub mirror", doi)
        return None

    def _parse_paper_page(self, html: str, doi: str, base_url: str) -> Optional[dict]:
        """Parse Sci-Hub paper page to extract metadata."""
        try:
            soup = BeautifulSoup(html, "html.parser")

            # Title
            title_elem = soup.find("div", id="title")
            title = ""
            if title_elem:
                title_text = title_elem.get_text(strip=True)
                # Clean up title
                title = re.sub(r"^citation\s*", "", title_text, flags=re.IGNORECASE)
                title = re.sub(r"^Sci-hub$", "", title).strip()

            # Authors
            authors = []
            authors_elem = soup.find("div", id="authors")
            if authors_elem:
                author_links = authors_elem.find_all("a")
                authors = [a.get_text(strip=True) for a in author_links if a.get_text(strip=True)]

            # Journal
            journal = ""
            journal_elem = soup.find("div", id="journal")
            if journal_elem:
                journal_text = journal_elem.get_text(strip=True)
                journal = re.sub(r"^citation\s*", "", journal_text, flags=re.IGNORECASE)

            # Abstract
            abstract = ""
            abstract_elem = soup.find("div", id="abstract")
            if abstract_elem:
                abstract = abstract_elem.get_text(strip=True)

            # Extract year
            year = ""
            text_to_search = f"{title} {journal}"
            year_match = re.search(r"\b(19|20)\d{2}\b", text_to_search)
            if year_match:
                year = year_match.group(0)

            if not title or title == "Sci-hub":
                return None

            return {
                "title": title,
                "authors": authors,
                "journal": journal,
                "year": year,
                "doi": doi,
                "abstract": abstract[:2000] if abstract else "",
                "url": f"{base_url}{doi}",
            }

        except Exception as e:
            logger.error("Error parsing paper page: %s", e)
            return None

    def store_paper(self, paper: dict, category: str) -> bool:
        """Store paper metadata in Neo4j."""
        if not self._driver or not paper:
            return False

        doi = paper.get("doi", "")
        title = paper.get("title", "")

        if not doi and not title:
            return False

        paper_id = doi if doi else f"title:{hash(title) % 1000000}"

        safe_title = wrap_external_content(title, source="sci_hub")
        safe_abstract = wrap_external_content(paper.get("abstract", "")[:1500], source="sci_hub")
        safe_journal = wrap_external_content(paper.get("journal", ""), source="sci_hub")

        try:
            with self._driver.session() as session:
                # Create category
                session.run("MERGE (c:Category {name: $category})", category=category)

                # Store paper as Article node
                session.run(
                    """
                    MERGE (a:Article {doi: $doi})
                    SET a.title = $title,
                        a.summary = $summary,
                        a.source = $source,
                        a.category = $category,
                        a.ingested_at = datetime(),
                        a.journal = $journal,
                        a.authors = $authors,
                        a.year = $year,
                        a.paper_id = $paper_id,
                        a.url = $url,
                        a.type = 'scientific_paper'
                    WITH a
                    MATCH (c:Category {name: $category})
                    MERGE (c)-[:CONTAINS]->(a)
                    """,
                    doi=doi,
                    title=safe_title,
                    summary=safe_abstract,
                    source="Sci-Hub",
                    category=category,
                    journal=safe_journal,
                    authors=paper.get("authors", []),
                    year=paper.get("year", ""),
                    paper_id=paper_id,
                    url=paper.get("url", ""),
                )

                # Store authors
                for author in paper.get("authors", [])[:5]:
                    if author:
                        session.run(
                            """
                            MERGE (auth:Author {name: $name})
                            WITH auth
                            MATCH (a:Article {paper_id: $paper_id})
                            MERGE (a)-[:AUTHORED_BY]->(auth)
                            """,
                            name=author[:200],
                            paper_id=paper_id,
                        )

                # Store topics from title
                topics = self._extract_topics(title, safe_abstract)
                for topic in topics:
                    session.run(
                        """
                        MERGE (t:Topic {name: $topic})
                        SET t.source = 'sci_hub'
                        WITH t
                        MATCH (a:Article {paper_id: $paper_id})
                        MERGE (a)-[:MENTIONS]->(t)
                        """,
                        topic=topic[:100],
                        paper_id=paper_id,
                    )

            return True

        except Exception as e:
            logger.error("Error storing paper: %s", e)
            return False

    def _extract_topics(self, title: str, abstract: str) -> list[str]:
        """Extract relevant topics from paper title and abstract."""
        text = f"{title} {abstract}".lower()

        topic_keywords = {
            "prediction_market": ["prediction market", "information market", "betting market"],
            "forecasting": ["forecast", "prediction", "predictive"],
            "decision_making": ["decision", "judgment", "choice"],
            "probability": ["probability", "probabilistic", "bayesian"],
            "bias": ["bias", "heuristic", "cognitive"],
            "uncertainty": ["uncertainty", "risk", "volatility"],
            "machine_learning": ["machine learning", "neural network", "deep learning"],
            "economics": ["economics", "market", "trading"],
            "psychology": ["psychology", "behavioral", "cognitive"],
            "statistics": ["statistical", "regression", "correlation"],
            "rationality": ["rationality", "rational", "bounded rationality"],
            "crowdsourcing": ["crowd", "collective", "wisdom of crowds"],
        }

        found_topics = []
        for topic_name, keywords in topic_keywords.items():
            if any(kw in text for kw in keywords):
                found_topics.append(topic_name)

        return found_topics[:10]

    async def ingest_known_papers(self) -> dict:
        """Ingest all known prediction market DOIs from Sci-Hub."""
        stats = {"fetched": 0, "stored": 0, "errors": 0}

        for category, doi_list in PREDICTION_MARKET_DOIS.items():
            logger.info("Ingesting category: %s (%d papers)", category, len(doi_list))

            for doi, expected_title in doi_list:
                try:
                    logger.info("Fetching: %s (%s)", expected_title, doi)
                    paper = await self.fetch_paper_by_doi(doi)

                    if paper:
                        if self.store_paper(paper, category):
                            stats["stored"] += 1
                            logger.info("Stored: %s", paper.get("title", doi))
                        else:
                            logger.warning("Failed to store: %s", doi)
                    else:
                        logger.warning("Could not fetch: %s", doi)

                    stats["fetched"] += 1

                    # Rate limiting
                    await asyncio.sleep(1)

                except Exception as e:
                    logger.error("Error processing DOI %s: %s", doi, e)
                    stats["errors"] += 1

        return stats

    async def search_and_ingest(self, query: str, category: str, max_results: int = 5) -> int:
        """Search Sci-Hub for papers and ingest them."""
        session = await self._get_session()
        stored_count = 0

        try:
            # Sci-Hub search (POST)
            search_url = self._base_url
            data = {"request": query}

            async with session.post(search_url, data=data) as resp:
                if resp.status != 200:
                    logger.warning("Search failed for '%s': HTTP %d", query, resp.status)
                    return 0

                html = await resp.text()

            # Parse search results
            soup = BeautifulSoup(html, "html.parser")

            # Check if we got a direct paper result
            title_elem = soup.find("div", id="title")
            if title_elem:
                title_text = title_elem.get_text(strip=True)
                if title_text and title_text != "Sci-hub":
                    # We got a paper result
                    paper = self._parse_paper_page(html, "", self._base_url)
                    if paper and self.store_paper(paper, category):
                        stored_count += 1

            logger.info("Search '%s': stored %d papers", query, stored_count)
            return stored_count

        except Exception as e:
            logger.error("Search error for '%s': %s", query, e)
            return 0

    async def close(self):
        """Close connections."""
        if self._session and not self._session.closed:
            await self._session.close()
        if self._driver:
            self._driver.close()


async def main():
    parser = argparse.ArgumentParser(
        description="Ingest knowledge from Sci-Hub into Neo4j for Polymarket"
    )
    parser.add_argument("--uri", default="bolt://localhost:7687", help="Neo4j URI")
    parser.add_argument("--user", default="neo4j", help="Neo4j user")
    parser.add_argument("--password", default="", help="Neo4j password")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    parser.add_argument(
        "--known-only",
        action="store_true",
        help="Only ingest known DOIs, skip search",
    )
    parser.add_argument(
        "--search",
        type=str,
        help="Search query for Sci-Hub (e.g., 'prediction market')",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=" * 60)
    print("Sci-Hub Knowledge Ingestion for Polymarket")
    print("=" * 60)
    print()

    ingester = SciHubKnowledgeIngestion(
        neo4j_uri=args.uri,
        neo4j_user=args.user,
        neo4j_password=args.password,
    )

    # Connect to Neo4j
    if not ingester.connect_neo4j():
        print("Failed to connect to Neo4j. Check URI and credentials.")
        sys.exit(1)

    print("Connected to Neo4j. Starting Sci-Hub ingestion...")
    print()

    stats = {"fetched": 0, "stored": 0, "errors": 0}

    if args.search:
        # Search mode
        print(f"Searching Sci-Hub for: {args.search}")
        count = await ingester.search_and_ingest(args.search, "search_results", max_results=10)
        stats["stored"] = count
        stats["fetched"] = count

    if args.known_only or not args.search:
        # Ingest known papers
        print("Ingesting known prediction market papers from Sci-Hub...")
        print(f"Total DOIs to fetch: {sum(len(dois) for dois in PREDICTION_MARKET_DOIS.values())}")
        print()

        known_stats = await ingester.ingest_known_papers()
        stats["fetched"] += known_stats["fetched"]
        stats["stored"] += known_stats["stored"]
        stats["errors"] += known_stats["errors"]

    await ingester.close()

    print()
    print("=" * 60)
    print("Ingestion complete!")
    print(f"  Fetched: {stats['fetched']}")
    print(f"  Stored:  {stats['stored']}")
    print(f"  Errors:  {stats['errors']}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
