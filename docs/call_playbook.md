# Call playbook: close every real conversation with a payment step

This is plan action 1 and needs no code. On 5 Oct 2026, 45% of calls that converted reached a
concrete payment step, against 2% of long calls that did not convert. Price came up in almost every
call, so talking about price alone does not close the sale. Asking for the money does.

## The close (every answered call of 2 minutes or more)

Before you hang up, do all four:

1. **Say the exact amount.** Say "The fee is ₹X", not "around" or "I'll send details".
2. **Offer the EMI split.** Say "That's ₹Y a month for Z months", and name the finance option.
3. **Send the payment link during the call** on WhatsApp, and stay on the line until they see it.
4. **Agree a date and time to pay.** Say "So you'll complete it by Thursday 6 pm?" Then log that time as the next step in LeadSquared.

Lines that worked on converting calls:

- "How would you like to pay your balance amount?"
- "I will just share you the payment link over WhatsApp."
- "You have to complete the balance amount for ₹___ to block your seat in this batch."

If the lead can't pay today, still do steps 1, 2 and 4, and record why: price, EMI refused, family
approval, or time. Never end a real conversation without a dated next step.

## The day's order

| When | What |
|---|---|
| 09:45 | Huddle: each caller reads out their Tier A leads and the exact ask for each |
| 10:00–11:30 | Tier A first: payment started, link sent and not paid, missed calls from leads, explicit buying words |
| All day | Call back a missed call from a lead within 15 minutes, and always the same day |
| 12:00, 15:00, 18:00 | Team leader check-ins (below) |
| 18:30–19:30 | Final attempts on every Tier A not yet closed |

## Team leader check-ins

- **12:00:** every Tier A lead has had one attempt, and every missed call so far has been returned.
- **15:00:** at least one payment link sent per caller. Listen to one real conversation per caller and check all four close steps.
- **18:00:** every "link sent, not paid" lead has had a second call with a dated pay time.
- **Dialer first:** if a caller's calls fail at the same second as other leads' calls (CallFailure), switch the line or use WhatsApp. That is the dialer, not the caller.

## How we measure it

`python -m analytics.team_performance YYYY-MM-DD` writes `plan_tracker_YYYY-MM-DD.pdf` next to
the daily report. For each team and caller it shows:

- **Payment step:** the share of Zipteams-scored calls whose summary shows a payment step, plus the transcript sample's rate.
- **Full pitch:** the Zipteams product-pitch score.
- **Same-day callback:** the share of leads with a missed inbound call who were called back that day.
- **Dial failures:** CallFailure as a share of dials.

The goal is for the payment-step rate to rise clearly above the 5 Oct level within a week. If
refunds or complaints rise with it, the close is too hard.
