import assert from "node:assert/strict";
import test from "node:test";

import { buildLeaderboardEndpoint } from "./leaderboardApi.js";

test("builds global leaderboard endpoint for All filters", () => {
  assert.equal(
    buildLeaderboardEndpoint({ league: "All", time: "All Time" }),
    "/api/leaderboard"
  );
});

test("builds World Cup leaderboard endpoint with league and period", () => {
  assert.equal(
    buildLeaderboardEndpoint({ league: "WORLD_CUP", time: "7d" }),
    "/api/leaderboard?league=WORLD_CUP&period=7d"
  );
});

test("encodes supported non-World Cup league and period filters", () => {
  assert.equal(
    buildLeaderboardEndpoint({ league: "NBA", time: "30d" }),
    "/api/leaderboard?league=NBA&period=30d"
  );
});
