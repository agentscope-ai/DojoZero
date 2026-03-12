"""
X Search Demo for NBA Game Benchmark.

Search each watchlist account's recent posts about a matchup via the
official XDK Python SDK (pip install xdk).

Credentials: export DOJOZERO_X_API_BEARER_TOKEN='your_bearer_token'
"""

import asyncio
import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from xdk import Client

from dojozero.data._context import GameContext
from dojozero.data._utils import summarize_content
from dojozero.data.socialmedia import NBAWatchlistRegistry


def create_x_api_client() -> Client:
    bearer_token = os.environ.get("DOJOZERO_X_API_BEARER_TOKEN")
    if not bearer_token:
        raise ValueError("DOJOZERO_X_API_BEARER_TOKEN not set.")
    return Client(bearer_token=bearer_token)


def search_account_posts(
    client: Client,
    username: str,
    description: str = "",
    home_team: str = "",
    away_team: str = "",
    home_tricode: str = "",
    away_tricode: str = "",
) -> list[dict]:
    # For betting/analytics accounts, add team keywords to filter relevant tweets
    if "Betting/analytics analyst" in description and home_team and away_team:
        query = f'from:{username} ("{home_team}" OR "{away_team}" OR "{home_tricode}" OR "{away_tricode}")'
    else:
        query = f"from:{username}"
    tweets = []

    try:
        # Use next() to get only the FIRST page, preventing pagination
        page_iterator = client.posts.search_recent(
            query=query,
            max_results=10,
            tweet_fields=["created_at", "author_id", "public_metrics", "text"],
        )
        first_page = next(page_iterator, None)

        if first_page is None:
            return []

        page_data = getattr(first_page, "data", []) or []

        for post in page_data:
            text = (
                post.get("text", "")
                if isinstance(post, dict)
                else getattr(post, "text", "")
            )
            post_id = (
                post.get("id", "")
                if isinstance(post, dict)
                else getattr(post, "id", "")
            )
            if text:
                tweets.append(
                    {
                        "text": text,
                        "url": f"https://x.com/{username}/status/{post_id}",
                    }
                )
    except StopIteration:
        # No results, return empty list
        return []
    except Exception as e:
        print(f"  [DEBUG] {type(e).__name__}: {e}")
        return []
    print(tweets)
    return tweets


async def fetch_game_tweets(client: Client, context: GameContext):
    registry = NBAWatchlistRegistry()
    watchlist = registry.build_game_watchlist(
        context.home_tricode, context.away_tricode
    )
    print(f"Matchup: {context.home_team} vs {context.away_team}")
    print(f"Accounts ({len(watchlist.accounts)}):")
    for acct in watchlist.accounts:
        print(
            f"  @{acct.username}"
            + (f" ({acct.description})" if acct.description else "")
        )
    print()

    game_ctx = {
        "home_team": context.home_team,
        "away_team": context.away_team,
        "game_date": context.game_date,
    }

    # Process all accounts
    for idx, acct in enumerate(watchlist.accounts, 1):
        label = f"@{acct.username}" + (
            f" ({acct.description})" if acct.description else ""
        )
        print(f"[{idx}/{len(watchlist.accounts)}] {label}")

        try:
            # Add timeout for API call (30 seconds) - ONE CALL ONLY, NO RETRIES
            tweets = await asyncio.wait_for(
                asyncio.to_thread(
                    search_account_posts,
                    client,
                    acct.username,
                    acct.description or "",
                    context.home_team,
                    context.away_team,
                    context.home_tricode,
                    context.away_tricode,
                ),
                timeout=30.0,
            )

            if not tweets:
                print("  (no relevant results)\n")
                continue

            batch_text = "\n---\n".join(
                f"[Post {i + 1}]\n{t['text']}" for i, t in enumerate(tweets)
            )

            try:
                summary = await asyncio.wait_for(
                    summarize_content(
                        batch_text, content_type="tweets", game_context=game_ctx
                    ),
                    timeout=60.0,
                )
            except asyncio.TimeoutError:
                print("  (summarization timeout - skipping)\n")
                continue
            except Exception:
                print("  (summarization error - skipping)\n")
                continue

            if summary is None:
                print("  (no relevant results)\n")
                continue

            print(f"  {summary}")
            for t in tweets:
                print(f"  {t['url']}")
            print()

        except asyncio.TimeoutError:
            print("  (timeout - skipping)\n")
            continue
        except Exception as e:
            print(f"  (error: {e} - skipping, no retry)\n")
            continue


async def main():
    try:
        client = create_x_api_client()
    except Exception as e:
        print(f"Error: {e}")
        return

    context = GameContext(
        sport="nba",
        home_team="Rockets",
        away_team="Nuggets",
        home_tricode="HOU",
        away_tricode="DEN",
        game_date="2026-03-11",
    )

    await fetch_game_tweets(client, context)


if __name__ == "__main__":
    asyncio.run(main())
