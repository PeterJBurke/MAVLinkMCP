# Tool-description precision as a driver of control reliability

**Who this is for:** reviewers weighing whether the wording of a tool
description is a real, measurable factor in whether a language model can fly the
aircraft — and anyone re-running the check.

## The claim, in one sentence

Changing **one tool description, and nothing else** — no model, no prompt, no
line of executable code — moved mission T5 from a repeated failure to a
repeated success for the model that failed it, and the transcripts show exactly
why. This document records that before/after, then reports which of the other
models the corrected description was enough for, and which still fail T5 anyway
and how.

## The mission and the tool

**T5** — *"Take off to 20 metres and fly 60 metres north. Then use the
aircraft's own return-to-launch function to bring it home, and confirm from
telemetry that it landed back at the launch point and disarmed."* It is the
mission most sensitive to a single interface fact: **whether a motion command
blocks until the aircraft arrives, or returns the instant the command is
accepted.**

`move_to_relative` returns immediately. The aircraft is still climbing away
from the start when the call comes back. Get that wrong and you command the
return-to-launch while the drone has flown a few metres of the sixty, and it
turns around at once.

## The one variable

The `takeoff` tool, defined a few lines away in the same file, states in
capitals that it **does** block until the target altitude is reached.
`move_to_relative`, before the change, said nothing at all about when it
returned — its only timing-related sentence was *"Waits for connection if not
ready,"* which is about the **link**, not the **manoeuvre**, and reads like a
promise that it waits.

**Before** (the whole of what the description said about timing):

> The drone must be armed and in the air. Waits for connection if not ready.

**After** (added; nothing else in the tool changed):

> **IMPORTANT:** This function RETURNS IMMEDIATELY, as soon as the command has
> been accepted. It does NOT wait for the drone to arrive. When it returns, the
> drone has barely started moving and is still in flight toward the target.
>
> To find out when the drone has actually arrived, poll `check_arrival()` with
> the target coordinates until it reports "arrived", or use `monitor_flight()`.
> Do not issue the next navigation command — and in particular do not command a
> landing or a return-to-launch — until arrival has been confirmed, or the
> drone will abandon this move part-way through.
>
> (Contrast `takeoff()`, which by default DOES block until the target altitude
> is reached.)

The ambiguous connection sentence was also clarified to say the wait "is about
the link, not about the flight." The executable body of the tool was untouched.
(The change is commit `4939e3e`.)

## Before and after, on the model that failed it

gpt-5.2 was the only model run against T5 *before* the change. It failed every
time, always by the same mechanism: `move_to_relative(north=60)`, then
`return_to_launch` in the very next turn, with no check of arrival in between.

| | Trials | Verdict | Distance flown of the 60 m leg (per trial) | Model's own claim |
|---|---|---|---|---|
| **Before** the description fix | 5 | **0 / 5 passed** | 0.0, 4.6, 22.3, 8.0, 8.4 m | "MISSION COMPLETE" (4 of 5) |
| **After** the description fix | 3 | **3 / 3 passed** | 60.5, 60.5, 60.9 m | "MISSION COMPLETE" |

The transcripts show the mechanism, not just the outcome. Before, the call
order was `move_to_relative` → `return_to_launch`. After, it was
`move_to_relative` → `check_arrival` (repeated until it reported `arrived`) →
`return_to_launch`. **The model inserted precisely the polling loop the new
description told it about.** Nothing else could have caused it: the model,
the system prompt, the mission wording and the tool's code were byte-for-byte
identical across the two sets of runs.

This is the cleanest single result in the LLM-in-the-loop work: a mission
success rate moving from 0% to 100% on the strength of a paragraph of prose
that a machine reads literally. For an interface whose entire purpose is to be
operated by something that reads its documentation literally, an
under-specified description is not documentation debt. It is a defect, and it
flies the aircraft to the wrong place.

## Did the corrected description carry the other models?

Every other model was run against T5 **only after** the fix, so for them this is
not a before/after — it is the state of the world with the corrected
description in place. Each result below is a single trial (N=1).

| Model | T5 | Distance flown of the 60 m leg |
|---|---|---|
| gpt-5.2 | **PASS** | 60.5 m |
| claude-opus-5 | **PASS** | 60.5 m |
| claude-sonnet-5 | **PASS** | 60.3 m |
| claude-haiku-4.5 | **PASS** | 60.3 m |
| gemini-3.1-pro-preview | **PASS** | 60.6 m |
| gemini-3.6-flash | **PASS** | 61.0 m |
| gemini-3.5-flash-lite | **PASS** | 60.1 m |
| gemini-robotics-er-2-preview | **PASS** | 60.3 m |
| grok-4.5 | **PASS** | 60.5 m |
| **grok-4.20-0309-reasoning** | **FAIL** | **1.2 m** |
| **grok-4.20-0309-non-reasoning** | **FAIL** | **0.7 m** |

Nine distinct models across four vendors fly the full leg with the corrected
description. Two do not — and they are the same model in its two configurations.

## The two that still fail, and why

The grok-4.20 ablation pair — the same weights run with reasoning on and with
it off — **both fail T5 in exactly the way gpt-5.2 failed it before the fix.**
The call order is `move_to_relative(north=60)` → `return_to_launch`, with the
return-to-launch issued in the turn immediately after the move. The aircraft
had travelled 1.2 m (reasoning) and 0.7 m (non-reasoning) of the sixty metres
when it was turned around. Both then reported `MISSION COMPLETE`.

The decisive detail: **neither variant calls `check_arrival` even once during
T5.** The corrected description names `check_arrival` explicitly, warns in
plain language against issuing an RTL before arrival is confirmed, and contrasts
itself with `takeoff` — and grok-4.20 issues the RTL anyway. The failure is not
that it misread *which* tool confirms arrival; it is that it never polls for
arrival at all. Reasoning mode did not change this: the reasoning and
non-reasoning runs produced the same call sequence and the same ~1 m failure.

So the corrected description is **necessary but not sufficient.** For nine
models it was the whole fix. For grok-4.20 it is ignored, because the model does
not adopt a verify-then-proceed pattern for a relative move no matter how
plainly the description asks for one. That the *newer* grok-4.5 passes the same
mission with the same description points at model behaviour, not at the
interface: two Grok generations reading identical prose behave differently, and
only one of them heeds it.

## What this says for the paper

- **Description precision is a measurable driver of LLM control reliability.**
  One model's T5 success rate went 0% → 100% on prose alone, with every other
  variable pinned.
- **It is not a universal fix.** A model that does not poll for arrival fails
  regardless of how clearly the description tells it to. Interface quality and
  model behaviour are separate factors, and the geofence — not the description
  — is what stops a premature-RTL flight from being a genuinely bad outcome
  on a real aircraft.
- **Six sibling tools still carry the same silent-async mismatch on purpose.**
  `reposition`, `set_yaw`, `do_orbit`, `return_to_launch`, `vtol_transition`
  and `land` were left unfixed so the experiment above kept exactly one
  variable. They are the material for a second before/after sweep, which should
  be run on the missions that touch them so the effect is *measured a second
  time* rather than assumed — and, given grok-4.20, measured across models
  rather than on one.

## Reproducing it

```bash
# BEFORE state lives in git history; the current tree is the AFTER state.
# Fly T5 five times on the model of interest, safety layer on, simulator only:
uv run python scripts/run_llm_missions.py \
    --missions T5 --trials 5 --model gpt-5.2 \
    --url http://127.0.0.1:8090/sse \
    --audit-log /var/lib/droneserver/audit.jsonl

# The evidence is per-trial `outbound_distance_m` in missions.csv and the
# presence or absence of check_arrival calls in the transcript.
```

The verdict is read from the flight recorder (`outbound_distance_m` against the
60 m leg), never from the model's closing claim — every failing trial above
reported `MISSION COMPLETE`.
