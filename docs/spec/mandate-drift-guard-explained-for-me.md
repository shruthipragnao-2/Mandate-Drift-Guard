# Mandate Drift Guard — Explained For My Brain

*Read this whenever you lose the thread. Every section is short on purpose. Skip around, it's fine.*

---

## THE ONE THING TO NEVER FORGET

**This is a tool a payment company (Razorpay) or a merchant uses — not a tool a shopper uses.**

Like a bank's fraud alert system. It watches *your* spending, but it's *the bank's* tool, not yours. Same idea here.

---

## The story, from zero

You hire a personal shopper (an AI agent). You say: *"Order my groceries, up to ₹8,000 a week."*

Weeks go by. Nothing looks wrong on any single day. But slowly, the shopping drifts — more expensive stuff, some non-grocery stuff sneaking in. Nobody did anything obviously bad. It just... crept.

A month later, you look at your statement, don't like what you see, and say *"I never authorized this."* That's a **chargeback** — money gets pulled back from the store that already sent you the groceries. The store loses money it already earned.

**Our system's whole job: catch the drift BEFORE it turns into that angry phone call, and pause it for a quick "hey, still want this?" instead.**

---

## Who actually uses this thing

| | You (the shopper) | The store / Razorpay |
|---|---|---|
| Does the system watch their data? | Yes | No |
| Do they use the dashboard? | Never | Yes |
| Who is it protecting from losing money? | Not really the point | **This is the entire point** |
| What do they ever see? | One "confirm this?" popup, rarely | Everything |

You're the person being *watched*. The store is who the tool is *for*. Different questions, both true at once — like a shop's security camera watches customers but is obviously the shop's tool.

---

## The two ways this goes wrong, without anyone being "bad"

**1. Fast splitting** — instead of one big suspicious purchase, several small ones that add up to the same thing. Easier to catch, it's basically math (did the total cross a line quickly).

**2. Slow drift** — no single purchase looks weird, but over weeks the *pattern* wanders away from what you set up. Harder to catch, because nothing ever looks wrong "in the moment."

---

## Why we need AI at all — the scary question, answered

**A judge WILL ask: "Couldn't a bank already do this with simple math?"**

Here's the actual answer, memorize the shape of it, not the exact words:

> Plain math can tell you spending is *unusual*. It cannot tell you whether unusual is *still okay*, because that needs comparing behavior against what you actually *said* you wanted, in words. That comparison — meaning, not numbers — is the part math can't do.

**The proof, not just the argument:** we build two test cases with the *exact same numbers* — same total, same speed, same everything a rule could measure — but one is totally fine (you hosted a party) and one isn't (real drift). Same numbers, opposite right answer. If math alone can't tell two identical-looking cases apart, that PROVES math alone isn't enough. Not an opinion — a built example.

---

## Wait, isn't this just spying on me?

You pushed on this twice, and the second push found something real — good instinct, don't stop having it.

**The honest correction:** to check "is the agent doing what you said," the system needs your **mandate text itself** ("groceries, ₹8,000/week") — not just transaction logs. That IS more data than transactions alone. My first answer undersold that. Here's the fixed version:

**Why "more data" ≠ "spying" — it's not about the amount, it's about these three things:**

1. **You typed it yourself, on purpose, at setup.** Nobody inferred it by watching you. It's not extracted — it's the instruction you gave, stored as-is.
2. **You can't delegate spending to an agent WITHOUT giving it some instruction.** The mandate isn't extra stuff bolted on — it's the thing that makes delegation possible at all. Even Razorpay's real pilot already needs *some* mandate (right now just merchant + amount) for the agent to work. We're saying it should include a purpose too — not inventing a new category of data.
3. **"Groceries, ₹8,000/week" reveals almost nothing about you.** It's a budget label, like writing "groceries" on a cash envelope — not a diary entry.

**One thing I checked and got right to tell you: don't use the "banks already require a stated purpose" comparison** — that's only true for *international* transfers (RBI purpose codes, FEMA rules), not ordinary domestic UPI. Real precedent, wrong category — checked it so you don't repeat it and get caught out.

**The actual complete answer, if a panel pushes this exact point:**
> *"Yes — checking compliance needs the mandate, not just transactions, because you can't check a rule without knowing the rule. But the mandate is something the person authored themselves at setup, not something extracted by watching them, and it's close to what agent delegation already requires to function. We're proposing it include a stated purpose, not proposing a new kind of data collection."*

Other privacy limits still true and still worth saying: only summary tags reach the AI (not itemized purchases), only a recent window is kept (not your whole history).

---

## How it actually works, step by step

```
1. You set a mandate: "up to ₹8,000/week, groceries"
2. Agent buys stuff over time
3. Step ① — Plain math checks: did spending speed/category shift a lot? (NO AI here — arithmetic is the right tool)
4. Step ② — ONLY IF flagged: an AI looks at the pattern + your original mandate and judges: does this still make sense?
5. Step ③ — Plain code (not AI) turns that judgment into: ALLOW / HOLD / BLOCK
6. Everything gets written down, permanently, so anyone can see exactly why a decision happened
```

AI only shows up in ONE spot (step ②). Everywhere else is boring, provable math — **on purpose**, and that's actually a good thing to say out loud in the pitch, not something to hide.

---

## What happens when things break

**Golden rule: if we're not sure, or something fails, we NEVER let the money move.** We pause it (HOLD) instead.

- AI takes too long to respond → HOLD, not "oh well, let it through."
- AI gives a weird/broken answer → HOLD.
- AI says "I'm not confident" → HOLD.

Being annoyingly cautious (blocking something fine) costs a little annoyance. Letting bad spending through costs real money. So when unsure, we always pick the cheaper mistake.

---

## Things we are deliberately NOT building

(Say these OUT LOUD in the pitch — don't let a judge think you forgot them)

- No real bank/merchant integration — fake/practice data only
- No fine-tuning our own AI model — using an existing one
- Not touching "tricked by fake menu text" (that's a different idea we set aside)
- Not a chatbot, not a fancy multi-agent thing — just three clear steps

---

## If you remember literally nothing else, remember this:

**We're not asking "is this spending weird." We're asking "is this weird spending still okay, given what the person actually said they wanted" — and that second question needs meaning, not just math, which is why AI is genuinely needed and not just added for show.**

And: **it's the payment company's tool, protecting the merchant from chargebacks — you're the one being watched, not the one using it.**
