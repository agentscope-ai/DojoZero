# NBA team data lookup: tricode -> TeamIdentity
# Used to fill in team details when not available in trial metadata
# Logo URLs use ESPN CDN: https://a.espncdn.com/i/teamlogos/nba/500/{tricode}.png
from dojozero.data._models import TeamIdentity

_NBA_TEAMS: dict[str, TeamIdentity] = {
    "ATL": TeamIdentity(
        name="Hawks",
        tricode="ATL",
        location="Atlanta",
        color="#E03A3E",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/atl.png",
    ),
    "BOS": TeamIdentity(
        name="Celtics",
        tricode="BOS",
        location="Boston",
        color="#007A33",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/bos.png",
    ),
    "BKN": TeamIdentity(
        name="Nets",
        tricode="BKN",
        location="Brooklyn",
        color="#000000",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/bkn.png",
    ),
    "CHA": TeamIdentity(
        name="Hornets",
        tricode="CHA",
        location="Charlotte",
        color="#1D1160",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/cha.png",
    ),
    "CHI": TeamIdentity(
        name="Bulls",
        tricode="CHI",
        location="Chicago",
        color="#CE1141",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/chi.png",
    ),
    "CLE": TeamIdentity(
        name="Cavaliers",
        tricode="CLE",
        location="Cleveland",
        color="#860038",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/cle.png",
    ),
    "DAL": TeamIdentity(
        name="Mavericks",
        tricode="DAL",
        location="Dallas",
        color="#00538C",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/dal.png",
    ),
    "DEN": TeamIdentity(
        name="Nuggets",
        tricode="DEN",
        location="Denver",
        color="#0E2240",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/den.png",
    ),
    "DET": TeamIdentity(
        name="Pistons",
        tricode="DET",
        location="Detroit",
        color="#C8102E",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/det.png",
    ),
    "GSW": TeamIdentity(
        name="Warriors",
        tricode="GSW",
        location="Golden State",
        color="#1D428A",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/gs.png",
    ),
    "HOU": TeamIdentity(
        name="Rockets",
        tricode="HOU",
        location="Houston",
        color="#CE1141",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/hou.png",
    ),
    "IND": TeamIdentity(
        name="Pacers",
        tricode="IND",
        location="Indiana",
        color="#002D62",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/ind.png",
    ),
    "LAC": TeamIdentity(
        name="Clippers",
        tricode="LAC",
        location="Los Angeles",
        color="#C8102E",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/lac.png",
    ),
    "LAL": TeamIdentity(
        name="Lakers",
        tricode="LAL",
        location="Los Angeles",
        color="#552583",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/lal.png",
    ),
    "MEM": TeamIdentity(
        name="Grizzlies",
        tricode="MEM",
        location="Memphis",
        color="#5D76A9",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/mem.png",
    ),
    "MIA": TeamIdentity(
        name="Heat",
        tricode="MIA",
        location="Miami",
        color="#98002E",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/mia.png",
    ),
    "MIL": TeamIdentity(
        name="Bucks",
        tricode="MIL",
        location="Milwaukee",
        color="#00471B",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/mil.png",
    ),
    "MIN": TeamIdentity(
        name="Timberwolves",
        tricode="MIN",
        location="Minnesota",
        color="#0C2340",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/min.png",
    ),
    "NOP": TeamIdentity(
        name="Pelicans",
        tricode="NOP",
        location="New Orleans",
        color="#0C2340",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/no.png",
    ),
    "NYK": TeamIdentity(
        name="Knicks",
        tricode="NYK",
        location="New York",
        color="#F58426",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/ny.png",
    ),
    "OKC": TeamIdentity(
        name="Thunder",
        tricode="OKC",
        location="Oklahoma City",
        color="#007AC1",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/okc.png",
    ),
    "ORL": TeamIdentity(
        name="Magic",
        tricode="ORL",
        location="Orlando",
        color="#0077C0",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/orl.png",
    ),
    "PHI": TeamIdentity(
        name="76ers",
        tricode="PHI",
        location="Philadelphia",
        color="#006BB6",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/phi.png",
    ),
    "PHX": TeamIdentity(
        name="Suns",
        tricode="PHX",
        location="Phoenix",
        color="#1D1160",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/phx.png",
    ),
    "POR": TeamIdentity(
        name="Trail Blazers",
        tricode="POR",
        location="Portland",
        color="#E03A3E",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/por.png",
    ),
    "SAC": TeamIdentity(
        name="Kings",
        tricode="SAC",
        location="Sacramento",
        color="#5A2D81",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/sac.png",
    ),
    "SAS": TeamIdentity(
        name="Spurs",
        tricode="SAS",
        location="San Antonio",
        color="#C4CED4",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/sa.png",
    ),
    "TOR": TeamIdentity(
        name="Raptors",
        tricode="TOR",
        location="Toronto",
        color="#CE1141",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/tor.png",
    ),
    "UTA": TeamIdentity(
        name="Jazz",
        tricode="UTA",
        location="Utah",
        color="#002B5C",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/utah.png",
    ),
    "WAS": TeamIdentity(
        name="Wizards",
        tricode="WAS",
        location="Washington",
        color="#002B5C",
        logo_url="https://a.espncdn.com/i/teamlogos/nba/500/wsh.png",
    ),
}

# NFL team data lookup
# Logo URLs use ESPN CDN: https://a.espncdn.com/i/teamlogos/nfl/500/{tricode}.png
_NFL_TEAMS: dict[str, TeamIdentity] = {
    "KC": TeamIdentity(
        name="Chiefs",
        tricode="KC",
        location="Kansas City",
        color="#E31837",
        logo_url="https://a.espncdn.com/i/teamlogos/nfl/500/kc.png",
    ),
    "SF": TeamIdentity(
        name="49ers",
        tricode="SF",
        location="San Francisco",
        color="#AA0000",
        logo_url="https://a.espncdn.com/i/teamlogos/nfl/500/sf.png",
    ),
    "BUF": TeamIdentity(
        name="Bills",
        tricode="BUF",
        location="Buffalo",
        color="#00338D",
        logo_url="https://a.espncdn.com/i/teamlogos/nfl/500/buf.png",
    ),
    "PHI": TeamIdentity(
        name="Eagles",
        tricode="PHI",
        location="Philadelphia",
        color="#004C54",
        logo_url="https://a.espncdn.com/i/teamlogos/nfl/500/phi.png",
    ),
    "DAL": TeamIdentity(
        name="Cowboys",
        tricode="DAL",
        location="Dallas",
        color="#003594",
        logo_url="https://a.espncdn.com/i/teamlogos/nfl/500/dal.png",
    ),
    "GB": TeamIdentity(
        name="Packers",
        tricode="GB",
        location="Green Bay",
        color="#203731",
        logo_url="https://a.espncdn.com/i/teamlogos/nfl/500/gb.png",
    ),
}

_DEFAULT_TEAM_COLOR = "#666666"


def _get_team_identity(tricode: str, league: str = "NBA") -> TeamIdentity:
    """Get team identity by tricode.

    Returns TeamIdentity from static lookup. Falls back to a minimal identity
    with the tricode as the name if not found, but still generates a logo URL
    using the ESPN CDN pattern.
    """
    teams = _NBA_TEAMS if league == "NBA" else _NFL_TEAMS
    if tricode in teams:
        return teams[tricode]

    # Generate logo URL dynamically for teams not in static lookup
    league_lower = league.lower()
    logo_url = (
        f"https://a.espncdn.com/i/teamlogos/{league_lower}/500/{tricode.lower()}.png"
    )

    return TeamIdentity(
        name=tricode,
        tricode=tricode,
        color=_DEFAULT_TEAM_COLOR,
        logo_url=logo_url,
    )
