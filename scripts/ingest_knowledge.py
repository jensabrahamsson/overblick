#!/usr/bin/env python3
"""
Knowledge Ingestion CLI — populates Neo4j knowledge graph for PolyTrader.

Usage:
    python scripts/ingest_knowledge.py [--uri bolt://localhost:7687] [--password <pw>]

Fetches data from DuckDuckGo, RSS feeds (BBC, NYT, ESPN, CoinTelegraph, etc.)
and stores it as a connected knowledge graph in Neo4j.
"""

import argparse
import asyncio
import logging
import sys

# Add project root to path
sys.path.insert(0, str(__file__).rsplit("/scripts/", 1)[0])

from overblick.plugins.polymarket_monitor.knowledge_ingestion import KnowledgeIngestion


async def main():
    parser = argparse.ArgumentParser(description="Ingest knowledge into Neo4j")
    parser.add_argument("--uri", default="bolt://localhost:7687", help="Neo4j URI")
    parser.add_argument("--user", default="neo4j", help="Neo4j user")
    parser.add_argument("--password", default="", help="Neo4j password")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    parser.add_argument(
        "--enable-scihub",
        action="store_true",
        help="Enable Sci-Hub scientific paper ingestion (experimental)",
    )
    parser.add_argument(
        "--max-papers", type=int, default=5, help="Maximum papers per Sci-Hub query (default: 5)"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    kg = KnowledgeIngestion(
        neo4j_uri=args.uri,
        neo4j_user=args.user,
        neo4j_password=args.password,
    )

    if not await kg.connect():
        print("Failed to connect to Neo4j. Check URI and credentials.")
        sys.exit(1)

    print("Connected to Neo4j. Starting knowledge ingestion...")
    if args.enable_scihub:
        print("This will fetch data from DuckDuckGo + RSS feeds + Sci-Hub scientific papers.")
    else:
        print("This will fetch data from DuckDuckGo + RSS feeds across all categories.")
    print()

    stats = await kg.ingest_all(
        enable_scihub=args.enable_scihub, max_papers_per_query=args.max_papers
    )

    print()
    print("=" * 50)
    print(f"Ingestion complete!")
    print(f"  Topics:   {stats['topics']}")
    print(f"  Articles: {stats['articles']}")
    if "papers" in stats:
        print(f"  Papers:   {stats['papers']}")
    print(f"  Errors:   {stats['errors']}")
    print()

    graph_stats = kg.get_stats()
    print(f"Knowledge Graph totals:")
    print(f"  Topics:     {graph_stats['topics']}")
    print(f"  Articles:   {graph_stats['articles']}")
    print(f"  Facts:      {graph_stats['facts']}")
    print(f"  Categories: {graph_stats['categories']}")

    kg.close()


if __name__ == "__main__":
    asyncio.run(main())
