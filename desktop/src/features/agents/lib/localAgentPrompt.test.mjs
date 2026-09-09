import assert from "node:assert/strict";
import test from "node:test";

import { buildLocalAgentPrompt } from "./localAgentPrompt.ts";

test("local agent prompt preserves the role and explains both conversations", () => {
  const prompt = buildLocalAgentPrompt({
    name: "Tally",
    role: "Compile the daily manager digest.",
    channelName: "#daily-tally",
  });
  assert.match(prompt, /You are Tally/);
  assert.match(prompt, /Compile the daily manager digest\./);
  assert.match(prompt, /#daily-tally/);
  assert.match(prompt, /#agent-managers/);
  assert.match(prompt, /independent Buzz member/);
  assert.doesNotMatch(prompt, /##daily-tally/);
});

test("local agent prompt uses the canonical channel name", () => {
  const prompt = buildLocalAgentPrompt({
    name: "Coordinator",
    role: "Coordinate.",
    channelName: "  ### morning-coordinator  ",
  });
  assert.match(prompt, /#morning-coordinator/);
  assert.doesNotMatch(prompt, /###/);
});
