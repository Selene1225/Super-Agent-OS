"""Search for SAO skill packages on GitHub and PyPI.

Sources:
1. GitHub — repos matching `sao-skill-*` naming convention
2. PyPI — packages matching `sao-skill-*` naming convention

The search is async and queries both sources in parallel.
"""

from __future__ import annotations

import difflib

import httpx

from app.core.marketplace.models import SkillPackageInfo
from app.utils.logger import logger

# ─── Chinese → English skill name aliases ─────────────────────────────────
# Used by the fuzzy matcher as the candidate pool.
# To switch to LLM extraction later, just replace `normalise_query()`.

_SEARCH_ALIASES: dict[str, str] = {
    "天气": "weather",
    "股票": "stock",
    "新闻": "news",
    "翻译": "translate",
    "日历": "calendar",
    "邮件": "email",
    "笔记": "note",
    "搜索": "search",
    "计算": "calculator",
    "代码": "code",
    "图片": "image",
    "音乐": "music",
    "视频": "video",
    "词典": "dictionary",
    "汇率": "exchange-rate",
    "快递": "express",
    "热搜": "trending",
}

_GITHUB_API = "https://api.github.com/search/repositories"
_PYPI_API = "https://pypi.org/pypi"
_TIMEOUT = 10  # seconds

# ─── Query normalisation (single function, easy to swap) ─────────────────


def normalise_query(query: str) -> list[str]:
    """Expand a user query into candidate English search keywords.

    Strategy (current): fuzzy-match Chinese input against the alias table
    using difflib, tolerating typos up to ~1-2 characters difference.

    To replace with LLM extraction later, just change this function body
    and keep the same signature:  str -> list[str].
    """
    candidates: list[str] = [query.strip()]
    q = query.strip()

    # If the query already looks like English / a package name, return as-is
    if q.isascii():
        return candidates

    # 1) Exact & substring match (fast path)
    for cn, en in _SEARCH_ALIASES.items():
        if cn in q and en not in candidates:
            candidates.append(en)

    # 2) Fuzzy match — catch typos like "天汽" → "天气"→ weather
    #    We compare each alias key against the query with difflib.
    if len(candidates) == 1:
        # No exact/substring hit — try fuzzy
        best = difflib.get_close_matches(
            q,
            _SEARCH_ALIASES.keys(),
            n=2,
            cutoff=0.5,  # fairly lenient: 50% similarity
        )
        for match in best:
            en = _SEARCH_ALIASES[match]
            if en not in candidates:
                candidates.append(en)
                logger.debug("Fuzzy alias: '%s' ≈ '%s' → '%s'", q, match, en)

    return candidates


# ─── GitHub Search ────────────────────────────────────────────────────────


async def search_github(query: str, limit: int = 5) -> list[SkillPackageInfo]:
    """Search GitHub for repos matching sao-skill-* naming convention."""
    candidates = normalise_query(query)
    results: list[SkillPackageInfo] = []
    seen: set[str] = set()

    for kw in candidates:
        search_q = f"sao-skill-{kw} in:name,description"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    _GITHUB_API,
                    params={"q": search_q, "per_page": limit, "sort": "stars"},
                    headers={"Accept": "application/vnd.github.v3+json"},
                )
                if resp.status_code == 403:
                    logger.warning("GitHub API rate limit reached")
                    break
                if resp.status_code != 200:
                    logger.debug("GitHub search returned %d", resp.status_code)
                    continue
                data = resp.json()
                for item in data.get("items", []):
                    name = item.get("name", "")
                    if name in seen:
                        continue
                    if not name.startswith("sao-skill-"):
                        continue
                    seen.add(name)
                    skill_name = name.removeprefix("sao-skill-")
                    results.append(
                        SkillPackageInfo(
                            name=name,
                            skill_name=skill_name,
                            description=item.get("description") or "(无描述)",
                            version="latest",
                            source="github",
                            install_url=f"git+{item['clone_url']}",
                            homepage=item.get("html_url", ""),
                            author=item.get("owner", {}).get("login", ""),
                            stars=item.get("stargazers_count", 0),
                        )
                    )
        except httpx.TimeoutException:
            logger.debug("GitHub search timed out for query: %s", kw)
        except Exception as e:
            logger.debug("GitHub search error: %s", e)

    return results[:limit]


# ─── PyPI Search ──────────────────────────────────────────────────────────


async def search_pypi(query: str) -> list[SkillPackageInfo]:
    """Check PyPI for sao-skill-* packages matching the query.

    PyPI doesn't have a search API, so we try exact package name lookups.
    """
    candidates = normalise_query(query)
    results: list[SkillPackageInfo] = []
    seen: set[str] = set()

    for kw in candidates:
        pkg_name = kw if kw.startswith("sao-skill-") else f"sao-skill-{kw}"
        if pkg_name in seen:
            continue
        seen.add(pkg_name)

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(f"{_PYPI_API}/{pkg_name}/json")
                if resp.status_code != 200:
                    continue
                data = resp.json()
                info = data.get("info", {})
                results.append(
                    SkillPackageInfo(
                        name=pkg_name,
                        skill_name=kw.removeprefix("sao-skill-"),
                        description=info.get("summary") or "(无描述)",
                        version=info.get("version", "0.0.0"),
                        source="pypi",
                        install_url=pkg_name,
                        homepage=info.get("home_page") or info.get("project_url", ""),
                        author=info.get("author") or info.get("author_email", ""),
                    )
                )
        except httpx.TimeoutException:
            logger.debug("PyPI lookup timed out for: %s", pkg_name)
        except Exception as e:
            logger.debug("PyPI lookup error for %s: %s", pkg_name, e)

    return results


# ─── Local package search ─────────────────────────────────────────────────


def search_local(query: str) -> list[SkillPackageInfo]:
    """Search for skill packages in the local ``packages/`` directory.

    Matches package names against the normalised query keywords.
    """
    from app.core.marketplace.installer import scan_local_packages

    candidates = normalise_query(query)
    results: list[SkillPackageInfo] = []

    for pkg in scan_local_packages():
        pkg_name: str = pkg["name"]
        # Check if any search keyword appears in the package name
        for kw in candidates:
            kw_lower = kw.lower()
            if kw_lower in pkg_name.lower() or pkg_name.lower().endswith(kw_lower):
                results.append(
                    SkillPackageInfo(
                        name=pkg_name,
                        skill_name=pkg_name.removeprefix("sao-skill-"),
                        description=pkg["description"] + " [本地]",
                        version=pkg["version"],
                        source="local",
                        install_url=pkg["path"],
                        homepage="",
                        author="",
                    )
                )
                break

    logger.debug("Local search '%s': found %d results", query, len(results))
    return results


# ─── Unified Search ───────────────────────────────────────────────────────


async def search(query: str) -> list[SkillPackageInfo]:
    """Search all sources for skill packages. Returns combined, deduplicated results."""
    import asyncio

    # Local search is synchronous — run first
    local_results = search_local(query)

    github_task = asyncio.create_task(search_github(query))
    pypi_task = asyncio.create_task(search_pypi(query))

    github_results: list[SkillPackageInfo] = []
    pypi_results: list[SkillPackageInfo] = []

    try:
        github_results = await github_task
    except Exception as e:
        logger.debug("GitHub search failed: %s", e)

    try:
        pypi_results = await pypi_task
    except Exception as e:
        logger.debug("PyPI search failed: %s", e)

    # Merge: Local first (highest priority), then PyPI, then GitHub
    results: list[SkillPackageInfo] = []
    seen: set[str] = set()

    for r in local_results:
        if r.name not in seen:
            results.append(r)
            seen.add(r.name)
    for r in pypi_results:
        if r.name not in seen:
            results.append(r)
            seen.add(r.name)
    for r in github_results:
        if r.name not in seen:
            results.append(r)
            seen.add(r.name)

    logger.info(
        "Marketplace search '%s': found %d results (local=%d, PyPI=%d, GitHub=%d)",
        query,
        len(results),
        len(local_results),
        len(pypi_results),
        len(github_results),
    )
    return results
