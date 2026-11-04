# Grow your own

You don't have to run my tracker — you can grow your own, shaped to your body
and your taste. Paste the prompt below into a capable AI assistant (Claude,
or similar — ideally one that can write and run code). It took one afternoon,
and I'm a researcher, not a developer. Change anything in [brackets]; delete
any feature you don't want.

---

I want you to build me a private, self-hosted menstrual cycle tracking web app,
because I'm done with subscription apps monetising my body's data. Requirements:

**Architecture:** A single Python file using Flask with an SQLite database
stored beside it — no accounts, no cloud, no external services, works offline.
I'll run it on [my old laptop / a Raspberry Pi / my home server]. It should be
a PWA (manifest, icons, service worker) so I can install it on my phone like a
real app.

**Logging:** A monthly calendar where I tap any day to log: flow
(spotting/light/medium/heavy/very heavy), symptoms, moods, energy, discharge,
and a free-text note. Make the daily logging fast — chips, not forms.

**Predictions, but honest ones:** Compute my average and median cycle from my
own history (excluding outlier lengths outside 15–60 days). Predict my next
period as an 80% probability window from my own distribution, not a single
overconfident date. Estimate ovulation (14 days before next predicted start)
and fertile window, clearly labelled as calendar estimates, not contraception.

**Views:** the monthly calendar (with cycle-day numbers in each cell and real
moon phases drawn on it), a year-at-a-glance view, and an insights page with
cycle length over time, a histogram, and symptom/mood frequencies.

**Real statistics on my own data:** a page that computes (a) odds ratios with
95% confidence intervals for which symptoms cluster in which cycle phase
(menstrual/follicular/ovulatory/luteal), showing counts honestly when data is
sparse; (b) a Rayleigh circular test of whether my cycle starts track the
lunar cycle; (c) rolling mean and SD of cycle length with the STRAW+10
≥7-day consecutive-difference marker, so perimenopause-related variability
change is visible early; (d) mean cycle length by month of year.

**My data, always:** an import tool for my old app's export
[I'm coming from Clue — their GDPR export is a measurements.json], additive
and idempotent, preserving even data types the app doesn't edit. Plus one-tap
export of everything as CSV and as the raw SQLite file.

**Aesthetic:** [warm and botanical — cream, sage, blush / your taste here].
No dark patterns, no streaks, no notifications guilt. It should feel like a
well-kept garden journal, not a compliance tool.

**Data honesty:** my cycle start dates are reliable; my symptom logging is
occasional; period lengths may miss trailing light days. Design the statistics
to lean on start dates and to display uncertainty rather than hide it.

Build it, test the maths against synthetic data, then walk me through
running it and importing my history.

---

That's the whole prompt. If your assistant can control a computer, it can also
deploy it to your server, set it to start on boot, and help you get HTTPS via
Tailscale so your phone installs it as a proper app — just ask for that too.

Your cycle. Your data. Your instrument.
