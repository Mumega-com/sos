"""
Deep Research Tool (SOS Port)
Adapted from mumega-cli/mumega/core/tools/deep_research.py

Flow: Search -> Extraction -> Batch Summarization (Simulated)
"""

import os
import re
import json
import asyncio
import aiohttp
import logging
import argparse
import sys
from typing import Dict, Any, List, Optional
from datetime import datetime
from dataclasses import dataclass

# Setup basic logging to stderr
logging.basicConfig(level=logging.INFO, format='%(message)s', stream=sys.stderr)
logger = logging.getLogger("deep_research")

# Check optional dependencies
try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

try:
    from readability import Document
    READABILITY_AVAILABLE = True
except ImportError:
    READABILITY_AVAILABLE = False

@dataclass
class ResearchResult:
    url: str
    title: str
    content: str
    word_count: int
    extraction_method: str
    error: Optional[str] = None

class DeepResearchTool:
    def __init__(self):
        self.max_content_length = 15000
        self.timeout = 15
        self.max_concurrent = 3
        self.session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout),
                headers={
                    'User-Agent': 'Mozilla/5.0 (compatible; SOSBot/1.0; +https://mumega.com)',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                }
            )
        return self.session

    async def research(self, query: str, count: int = 5) -> Dict[str, Any]:
        """
        Main research flow.
        """
        logger.info(f"🔬 Starting deep research for: {query}")
        
        # 1. Search for URLs
        from web_search import WebSearchTool
        search_tool = WebSearchTool()
        
        provider = "tavily" if os.getenv("TAVILY_API_KEY") else "duckduckgo"
        
        # Initialize report with metadata early
        report = {
            "query": query,
            "timestamp": datetime.now().isoformat(),
            "sources_found": 0,
            "successful_extractions": 0,
            "results": [],
            "status": "processing"
        }
        
        search_results = await search_tool.search(query, count=count, provider=provider)
        
        if "error" in search_results:
            report["status"] = "error"
            report["error"] = search_results["error"]
            return report
            
        urls = search_results.get("results", [])
        if not urls:
            report["status"] = "no_results_found"
            return report

        # 2. Extract content from URLs
        report["sources_found"] = len(urls)
        extracted_content = await self._extract_content_batch(urls)
        
        # 3. Finalize report
        report["successful_extractions"] = len([c for c in extracted_content if not c.get("error")])
        report["results"] = extracted_content
        report["status"] = "complete"
        
        return report

    async def _extract_content_batch(self, sources: List[Dict]) -> List[Dict[str, Any]]:
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def extract_with_limit(source):
            async with semaphore:
                return await self._extract_content(source["url"], source.get("title", ""))

        tasks = [extract_with_limit(source) for source in sources]
        return await asyncio.gather(*tasks)

    async def _extract_content(self, url: str, title: str = "") -> Dict[str, Any]:
        result = {
            "url": url,
            "title": title,
            "content": "",
            "word_count": 0,
            "extraction_method": "none",
            "error": None
        }

        try:
            session = await self._get_session()
            async with session.get(url) as response:
                if response.status != 200:
                    result["error"] = f"HTTP {response.status}"
                    return result

                html = await response.text()

            # Extraction
            text = self._extract_text(html)
            if len(text) > self.max_content_length:
                text = text[:self.max_content_length] + "...[truncated]"

            result["content"] = text
            result["word_count"] = len(text.split())
            result["extraction_method"] = "readability" if READABILITY_AVAILABLE else "bs4" if BS4_AVAILABLE else "regex"
            
            return result
        except Exception as e:
            result["error"] = str(e)
            return result

    def _extract_text(self, html: str) -> str:
        if READABILITY_AVAILABLE:
            try:
                doc = Document(html)
                summary = doc.summary()
                if BS4_AVAILABLE:
                    soup = BeautifulSoup(summary, 'html.parser')
                    return soup.get_text(separator='\n', strip=True)
                return re.sub(r'<[^>]+>', '', summary)
            except: pass

        if BS4_AVAILABLE:
            try:
                soup = BeautifulSoup(html, 'html.parser')
                for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
                    tag.decompose()
                return soup.get_text(separator='\n', strip=True)
            except: pass

        # Fallback
        return re.sub(r'<[^>]+>', ' ', html).strip()

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("query", help="The research query")
    parser.add_argument("--count", type=int, default=3, help="Number of URLs to extract")
    args = parser.parse_args()

    tool = DeepResearchTool()
    try:
        report = await tool.research(args.query, count=args.count)
        print(json.dumps(report, indent=2))
        sys.exit(0)
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
    finally:
        if tool.session:
            await tool.session.close()

if __name__ == "__main__":
    asyncio.run(main())
