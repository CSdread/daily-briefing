# Daily Briefing Email — Format Skill

Defines the HTML structure, section layout, and style guidelines for the daily briefing email.

---

## Sending the Email

Send the email using `gmail_send` with:
- **To:** `{{ BRIEFING_EMAIL }}`
- **Subject:** `Daily Briefing — {{ TODAY }}`
- **html:** `true`
- **Body:** HTML per the template below

`gmail_send` may only be called once per run. If the runner rejects a second call, do not retry — the email was already sent.

---

## HTML Template

Build the body as valid HTML following this structure and style. Omit any section block (including its `<div class="section">`) if there is nothing to report for it.

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  body { margin: 0; padding: 0; background: #f4f4f4; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }
  .wrapper { max-width: 600px; margin: 24px auto; background: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,0.12); }
  .header { background: #1a1f2e; padding: 24px 32px; }
  .header h1 { margin: 0; color: #ffffff; font-size: 20px; font-weight: 600; letter-spacing: 0.3px; }
  .header .date { margin: 4px 0 0; color: #8b95a8; font-size: 13px; }
  .glance { background: #f0f4ff; border-left: 4px solid #4a6cf7; padding: 16px 32px; font-size: 14px; color: #2d3748; line-height: 1.6; }
  .section { padding: 20px 32px; border-top: 1px solid #edf0f5; }
  .section h2 { margin: 0 0 12px; font-size: 11px; font-weight: 700; letter-spacing: 1px; text-transform: uppercase; color: #6b7a99; }
  .section ul { margin: 0; padding: 0 0 0 18px; }
  .section li { margin: 6px 0; font-size: 14px; color: #2d3748; line-height: 1.5; }
  .section p { margin: 0 0 8px; font-size: 14px; color: #2d3748; line-height: 1.5; }
  .day-heading { font-size: 13px; font-weight: 600; color: #4a6cf7; margin: 12px 0 6px; }
  .day-heading:first-child { margin-top: 0; }
  .urgent { color: #c0392b; font-weight: 600; }
  .tag { display: inline-block; font-size: 11px; padding: 1px 7px; border-radius: 10px; margin-left: 6px; vertical-align: middle; }
  .tag-overdue { background: #fde8e8; color: #c0392b; }
  .tag-today { background: #e8f4fd; color: #2471a3; }
  .footer { background: #f8f9fb; padding: 14px 32px; text-align: center; font-size: 11px; color: #a0a8ba; border-top: 1px solid #edf0f5; }
</style>
</head>
<body>
<div class="wrapper">

  <!-- Header -->
  <div class="header">
    <h1>Daily Briefing</h1>
    <p class="date">{{ TODAY }}</p>
  </div>

  <!-- Today at a Glance -->
  <div class="glance">
    [2–3 sentence summary of the most important things for today]
  </div>

  <!-- Calendar -->
  <div class="section">
    <h2>Calendar</h2>
    <!-- Repeat for each day that has events: -->
    <div class="day-heading">[Day name, Month Day] — Today</div>
    <ul>
      <li>[Time] — [Event title] [location or attendees if relevant]</li>
    </ul>
    <div class="day-heading">[Day name, Month Day] — Tomorrow</div>
    <ul>
      <li>[Time] — [Event title]</li>
    </ul>
    <!-- Birthdays sub-section if any -->
    <div class="day-heading">Birthdays</div>
    <ul>
      <li>[Name] — [relationship/context if known]</li>
    </ul>
  </div>

  <!-- Active Projects (only if items from any source match a project) -->
  <div class="section">
    <h2>Projects</h2>
    <!-- One block per active project that had activity today: -->
    <div class="day-heading">[Project Name]</div>
    <ul>
      <li>[Summary of current status and open items]</li>
      <li>[New activity from today — email, message, reminder, or calendar item]</li>
    </ul>
  </div>

  <!-- Pending Responses -->
  <div class="section">
    <h2>Pending Responses</h2>
    <ul>
      <!-- Most urgent first. For each item: -->
      <li><strong>[From]</strong> — [subject/topic] <em style="color:#8b95a8; font-size:12px;">[age, e.g. 2h ago]</em></li>
    </ul>
  </div>

  <!-- Tasks & Reminders -->
  <div class="section">
    <h2>Tasks &amp; Reminders</h2>
    <ul>
      <!-- Use tags for overdue vs due today -->
      <li>[Task name] <span class="tag tag-overdue">Overdue</span></li>
      <li>[Task name] <span class="tag tag-today">Due today</span></li>
      <li>[Task name] — [list name]</li>
    </ul>
  </div>

  <!-- Home Status -->
  <div class="section">
    <h2>Home Status</h2>
    <ul>
      <li><strong>Vacuum:</strong> [last run, battery, status]</li>
      <li><strong>Hot tub:</strong> [temp vs. target, any maintenance due]</li>
      <li>[Other maintenance items]</li>
    </ul>
  </div>

  <!-- Footer -->
  <div class="footer">
    Generated {{ TODAY }} · {{ TIME }}
  </div>

</div>
</body>
</html>
```

---

## Tone and Style

- Concise and scannable — `<ul>` lists over paragraphs
- Use `<strong>` and the `.urgent` class for urgent items (never CAPS)
- Skip filler — remove any section with nothing to report
- Focus on actionable items
- Friendly but efficient — this is a morning briefing, not a report
