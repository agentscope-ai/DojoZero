"""NBA-specific utility functions."""

# Common NBA team name variations for matching
TEAM_NAME_VARIATIONS = {
    "heat": ["heat", "miami heat", "miami"],
    "magic": ["magic", "orlando magic", "orlando"],
    "lakers": ["lakers", "los angeles lakers", "la lakers"],
    "celtics": ["celtics", "boston celtics", "boston"],
    "warriors": ["warriors", "golden state warriors", "golden state", "gsw"],
    "suns": ["suns", "phoenix suns", "phoenix"],
    "thunder": ["thunder", "oklahoma city thunder", "okc", "oklahoma city"],
    "pistons": ["pistons", "detroit pistons", "detroit"],
    "raptors": ["raptors", "toronto raptors", "toronto"],
    "bulls": ["bulls", "chicago bulls", "chicago"],
    "knicks": ["knicks", "new york knicks", "ny knicks"],
    "nets": ["nets", "brooklyn nets", "brooklyn"],
    "76ers": ["76ers", "sixers", "philadelphia 76ers", "philadelphia"],
    "hawks": ["hawks", "atlanta hawks", "atlanta"],
    "hornets": ["hornets", "charlotte hornets", "charlotte"],
    "cavaliers": ["cavaliers", "cavs", "cleveland cavaliers", "cleveland"],
    "mavericks": ["mavericks", "mavs", "dallas mavericks", "dallas"],
    "nuggets": ["nuggets", "denver nuggets", "denver"],
    "rockets": ["rockets", "houston rockets", "houston"],
    "pacers": ["pacers", "indiana pacers", "indiana"],
    "clippers": ["clippers", "la clippers", "los angeles clippers"],
    "grizzlies": ["grizzlies", "memphis grizzlies", "memphis"],
    "timberwolves": ["timberwolves", "wolves", "minnesota timberwolves", "minnesota"],
    "pelicans": ["pelicans", "new orleans pelicans", "new orleans"],
    "trail blazers": ["trail blazers", "blazers", "portland trail blazers", "portland"],
    "kings": ["kings", "sacramento kings", "sacramento"],
    "spurs": ["spurs", "san antonio spurs", "san antonio"],
    "jazz": ["jazz", "utah jazz", "utah"],
    "wizards": ["wizards", "washington wizards", "washington"],
}


def extract_team_names_from_query(query: str) -> set[str]:
    """Extract team names from query string.
    
    Args:
        query: Search query string
        
    Returns:
        Set of normalized team names found in query
    """
    query_lower = query.lower()
    found_teams = set()
    
    # Check each team's variations
    for team_key, variations in TEAM_NAME_VARIATIONS.items():
        for variation in variations:
            if variation in query_lower:
                found_teams.add(team_key)
                break
    
    return found_teams


def normalize_team_name(team_name: str) -> str | None:
    """Normalize a team name to match our team key.
    
    Args:
        team_name: Team name from ranking data
        
    Returns:
        Normalized team key or None if not found
    """
    team_lower = team_name.lower()
    
    # Direct match
    if team_lower in TEAM_NAME_VARIATIONS:
        return team_lower
    
    # Check variations
    for team_key, variations in TEAM_NAME_VARIATIONS.items():
        if team_lower in variations:
            return team_key
        # Also check if team name contains any variation
        for variation in variations:
            if variation in team_lower or team_lower in variation:
                return team_key
    
    return None

