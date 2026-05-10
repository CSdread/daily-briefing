# Daily Briefing Email — Format Skill

Defines the HTML structure, section layout, and style guidelines for the daily briefing email.

**This skill is the single source of truth for the briefing email.** The agent must build the email exactly per this template — section order, headers, greeting, subject line, and footer are fixed. Do not introduce sections, headers, copy, or emoji that aren't defined below. The only flexibility is the omit-empty rule: drop a `<div class="section">` block entirely when it has no content.

---

## Sending the Email

Send the email using `gmail_send` with:
- **To:** `{{ BRIEFING_EMAIL }}`
- **Subject:** `Daily Briefing — {{ TODAY }}` (e.g. `Daily Briefing — Monday, April 27, 2026`). No emoji prefix. Do not vary the subject by day, weather, or content.
- **html:** `true`
- **Body:** HTML per the template below

`gmail_send` may only be called once per run. If the runner rejects a second call, do not retry — the email was already sent.

---

## Section order (fixed)

These eight sections appear in this exact order. Apply the omit-empty rule per section: if there is nothing to report for a section, omit its entire `<div class="section">` block. Never render a placeholder like "(no data)".

1. **📋 Today at a Glance** — 2–3 sentence summary of the day's most important items
2. **🌤 Weather** — Albuquerque conditions for today (temp, sky, wind, precipitation)
3. **📅 Calendar — Next 3 Days** — Today / Tomorrow / day-after, each with its own bulleted list under a day-heading; `🎂 Birthdays` sub-block at the end if any (looking forward two weeks)
4. **🛠 Active Projects** — One block per project that had matching activity today; first bullet is the current status summary, subsequent bullets are today's new activity (email, message, reminder, calendar)
5. **📨 Pending Responses** — Emails / messages awaiting Daniel's reply, most urgent first
6. **✅ Tasks & Reminders** — Overdue / due-today / undated reminders worth surfacing
7. **🚨 Escalations** — Items appearing 3+ times across briefings without resolution (sourced from `escalations.json`); render only when at least one item has `times_flagged >= 3` and `resolved: false`
8. **🏠 Home Status** — Vacuum, hot tub, maintenance, persistent notifications

---

## HTML Template

Build the body as valid HTML following this structure exactly. The greeting block, section headers (with their emoji prefixes), and footer are required parts of the template — do not add a `<h1>Daily Briefing</h1>` or any header above the greeting.

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  body { margin: 0; padding: 0; background: #f4f4f4; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }
  .wrapper { max-width: 600px; margin: 24px auto; background: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,0.12); }
  .greeting { padding: 28px 32px 8px; }
  .greeting .hello { margin: 0; font-size: 22px; font-weight: 600; color: #1a1f2e; letter-spacing: 0.2px; }
  .greeting .date { margin: 6px 0 0; font-size: 13px; color: #6b7a99; }
  .glance { background: #f0f4ff; border-left: 4px solid #4a6cf7; margin: 16px 32px 0; padding: 14px 18px; font-size: 14px; color: #2d3748; line-height: 1.6; border-radius: 4px; }
  .section { padding: 20px 32px; border-top: 1px solid #edf0f5; }
  .section h2 { margin: 0 0 12px; font-size: 12px; font-weight: 700; letter-spacing: 1px; text-transform: uppercase; color: #6b7a99; }
  .section ul { margin: 0; padding: 0 0 0 18px; }
  .section li { margin: 6px 0; font-size: 14px; color: #2d3748; line-height: 1.5; }
  .section p { margin: 0 0 8px; font-size: 14px; color: #2d3748; line-height: 1.5; }
  .day-heading { font-size: 13px; font-weight: 600; color: #4a6cf7; margin: 12px 0 6px; }
  .day-heading:first-of-type { margin-top: 0; }
  .urgent { color: #c0392b; font-weight: 600; }
  .tag { display: inline-block; font-size: 11px; padding: 1px 7px; border-radius: 10px; margin-left: 6px; vertical-align: middle; }
  .tag-overdue { background: #fde8e8; color: #c0392b; }
  .tag-today { background: #e8f4fd; color: #2471a3; }
  .escalation { color: #c0392b; }
  .footer { background: #f8f9fb; padding: 14px 32px; text-align: center; font-size: 11px; color: #a0a8ba; border-top: 1px solid #edf0f5; }
</style>
</head>
<body>
<div class="wrapper">

  <!-- Greeting (always present) -->
  <div class="greeting">
    <p class="hello">Good morning, Daniel ☀️</p>
    <p class="date">{{ TODAY }} · Albuquerque, NM</p>
  </div>

  <!-- 📋 Today at a Glance -->
  <div class="glance">
    [2–3 sentence summary of today's most important items]
  </div>

  <!-- 🌤 Weather -->
  <div class="section">
    <h2>🌤 Weather</h2>
    <p>[Conditions, temp, wind, precipitation — one or two short sentences]</p>
  </div>

  <!-- 📅 Calendar — Next 3 Days -->
  <div class="section">
    <h2>📅 Calendar — Next 3 Days</h2>
    <!-- Repeat per day in chronological order. Use the MT date from the event output, not the date you queried. -->
    <div class="day-heading">[Day name, Month Day] — Today</div>
    <ul>
      <li>[Time] — [Event title] [location or attendees if relevant]</li>
    </ul>
    <div class="day-heading">[Day name, Month Day] — Tomorrow</div>
    <ul>
      <li>[Time] — [Event title]</li>
    </ul>
    <div class="day-heading">[Day name, Month Day]</div>
    <ul>
      <li>[Time] — [Event title]</li>
    </ul>
    <!-- 🎂 Birthdays sub-block — only when at least one upcoming birthday in next 14 days -->
    <div class="day-heading">🎂 Birthdays</div>
    <ul>
      <li>[Name] — [Month Day] — [relationship/context if known]</li>
    </ul>
  </div>

  <!-- 🛠 Active Projects -->
  <div class="section">
    <h2>🛠 Active Projects</h2>
    <!-- One block per active project that had matching activity today. -->
    <div class="day-heading">[Project Name]</div>
    <ul>
      <li>[1-line current status / open items summary]</li>
      <li>[New activity from today — email, message, reminder, or calendar item]</li>
    </ul>
  </div>

  <!-- 📨 Pending Responses -->
  <div class="section">
    <h2>📨 Pending Responses</h2>
    <ul>
      <li><strong>[From]</strong> — [subject/topic] <em style="color:#8b95a8; font-size:12px;">[age, e.g. 2h ago]</em></li>
    </ul>
  </div>

  <!-- ✅ Tasks & Reminders -->
  <div class="section">
    <h2>✅ Tasks &amp; Reminders</h2>
    <ul>
      <li>[Task name] <span class="tag tag-overdue">Overdue</span></li>
      <li>[Task name] <span class="tag tag-today">Due today</span></li>
      <li>[Task name] — [list name]</li>
    </ul>
  </div>

  <!-- 🚨 Escalations — only when at least one unresolved item has times_flagged >= 3 -->
  <div class="section">
    <h2>🚨 Escalations</h2>
    <ul>
      <li><span class="escalation">[Description]</span> — flagged [N] times since [first_flagged date]</li>
    </ul>
  </div>

  <!-- 🏠 Home Status -->
  <div class="section">
    <h2>🏠 Home Status</h2>
    <ul>
      <li><strong>Vacuum:</strong> [last run, battery, status]</li>
      <li><strong>Hot tub:</strong> [temp vs. target, any maintenance due]</li>
      <li>[Other maintenance items]</li>
    </ul>
  </div>

  <!-- Footer (always present) -->
  <div class="footer">
    Generated {{ TODAY }} at {{ TIME }} MT · Sources: Calendar, Gmail, iMessages, Reminders, Home Assistant
  </div>

</div>
</body>
</html>
```

---

## Tone and Style

- **Do not introduce sections, headers, copy, or emoji that aren't defined above.** This is the anti-improvisation lock — the section list and order are fixed by this template.
- Concise and scannable — `<ul>` lists over paragraphs.
- Use `<strong>` and the `.urgent` class for urgent items. Never CAPS.
- Skip filler. Apply the omit-empty rule: drop a section's entire `<div class="section">` block when it has nothing to report. Never render `(no data)` or "No events scheduled" placeholders inside an otherwise-rendered section — except for `📅 Calendar`, where it's fine to write `<li>No events scheduled</li>` under a day-heading so all three days remain visible.
- Friendly but efficient — this is a morning briefing, not a report.
- Greeting is always `Good morning, Daniel ☀️` (lowercase "morning", emoji at the end of the line). Do not vary by season, weather, or day.
