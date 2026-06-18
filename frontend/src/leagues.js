/**
 * Shared league display helpers.
 *
 * The arena's canonical league key is the uppercased trial sport_type
 * (e.g. "WORLD_CUP"). Most keys are already display-ready (NBA, NFL, NCAA);
 * this maps the ones that aren't to a friendly label.
 */

/** League keys whose display label differs from the raw key. */
export const LEAGUE_LABELS = { WORLD_CUP: "World Cup" };

/** Friendly display label for a league key, falling back to the key itself. */
export function leagueLabel(value) {
  return LEAGUE_LABELS[value] || value;
}
