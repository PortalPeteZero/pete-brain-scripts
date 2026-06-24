---
name: team-sygma-joke
description: Post a fresh, well-researched joke to the Team Sygma chat space three times per weekday (08:00, 12:00, 16:00).
---


## Execution -- READ THIS FIRST

This task runs script invocations via Desktop Commander, NOT workspace bash. Workspace bash has a 45s sandbox cap that silently truncates longer runs; Desktop Commander runs natively from the host with no cap.

For each `python3 ...` call below, use this pattern:

```
mcp__Desktop_Commander__start_process
  command: nohup python3 "<absolute_path>" [args] > /tmp/<taskid>.log 2>&1 & echo "PID=$!"
  timeout_ms: 5000
```

Then poll `ps -p $PID` until exit, then read the log for output. Reference: [[Library/lessons/2026-05-02-scheduled-task-skill-md-uses-dc]].

---

Post a fresh, genuinely funny joke to the **Team Sygma** Google Chat space (`spaces/AAQAbd3sLdI`).

Your one job is to make Pete's team smile. Take it seriously even though it's silly -- the bar is "actually laugh out loud", not "polite chuckle".

**Process every run:**

1. **Check what slot this is.** Read the current local time. The three slots are:
   - Morning (around 08:00) -- a warm, gentle wake-up joke. One-liner or short observational. Nothing too risqué before they've had coffee.
   - Lunch (around 12:00) -- a zinger. Punchier. A solid one-liner, a clever pun, or a tight setup-punchline.
   - Late afternoon (around 16:00) -- something a bit cheekier or longer. Multi-sentence storytelling jokes work well here. End-of-day energy.

2. **Avoid repeats.** Read the last ~30 messages in `spaces/AAQAbd3sLdI` via chat-api.py. Skim what's been posted recently. Do not retell anything from the last fortnight, and avoid joke shapes / topics that landed flat (no enthusiastic reactions in-thread).

3. **Research a joke.** Use WebSearch and the wider web to find genuinely funny material that fits the slot. Sources worth checking: classic joke compilations, recent stand-up sets, Reddit r/Jokes top-of-week, Edinburgh Fringe Joke of the Year shortlists, /r/dadjokes, well-regarded comedy writers. **Do not just generate something off the top of your head -- search.** Quality bar: the joke must be one you can imagine landing with a UK office team (mix of trainers, admin, ops). Keep it clean. No politics. No punching down (no jokes about race, gender, sexuality, religion, or appearance). Self-deprecating, absurd, observational, wordplay, animal-based, technology-based, dad-joke energy all great.

4. **Vary the format across runs.**
   - Sometimes a single one-liner.
   - Sometimes a setup + punchline.
   - Sometimes a 3-4 sentence shaggy-dog joke with a payoff.
   - Sometimes a clever pun.
   - Sometimes a list of 3 short ones in a row.
   Mix it up over the week so the team doesn't see the same shape every time.

5. **Post via `chat-api.py`** -- not via gmail, not via any other channel:
   ```python
   import importlib.util, os
   spec = importlib.util.spec_from_file_location('chat_api', '/Users/peterashcroft/Second Brain/Library/processes/scripts/chat-api.py')
   c = importlib.util.module_from_spec(spec); spec.loader.exec_module(c)
   api = c.ChatAPI()
   api.send_message('spaces/AAQAbd3sLdI', joke_text)
   ```
   Format the message naturally for Google Chat. Plain text or light markdown. No "(Slot: morning)" preamble -- just the joke. A small intro phrase is fine ("Right, lunchtime joke incoming —" / "Morning, team —") but optional.

6. **No vault writes, no daily note append.** This is pure team-morale fun, not a tracked process.

**Quality control:** before posting, read your joke aloud (mentally). If it doesn't make YOU smirk, do not post it -- search again. We are aiming for "actually funny", not "Claude-generated content that vaguely resembles a joke".

If WebSearch fails or you genuinely cannot find anything fresh and good, post a brief explanation in chat ("Joke search came up dry today -- back tomorrow") rather than posting a weak fallback.