"""Shared formatters for social media insight events.

These formatters are sport-agnostic — the events carry sport-specific content
(team names, player names) but the formatting logic is identical across sports.
"""

from dojozero.data.socialmedia._events import TwitterTopTweetsEvent


def format_twitter_top_tweets(event: TwitterTopTweetsEvent) -> str:
    """Format TwitterTopTweetsEvent to readable text.

    Only uses the processed summary field. The raw tweets field is kept
    for internal processing/debugging but is not sent to agents.
    """
    lines = ["[Social Media Update]"]
    if event.summary:
        lines.append(f"\n{event.summary}")
    else:
        lines.append("\n(No relevant social media content found)")

    return "\n".join(lines)


SOCIALMEDIA_EVENT_FORMATTERS: dict[str, object] = {
    "twitter_top_tweets": format_twitter_top_tweets,
}
