# ai-quota-monitor Design System Guidelines

## 1. Purpose

This design system defines the visual language and interaction rules for **ai-quota-monitor**, a self-hosted operational dashboard for monitoring Codex quota windows, authentication state, schedules, anchor runs, events, and deployment health across multiple accounts.

The interface should feel like a **quiet precision instrument panel**: dependable, compact, legible, and calm under continuous monitoring. It must make the most important operational facts understandable at a glance without turning every value into a badge, card, or alert.

The system supports:

- A dark-first desktop dashboard.
- A complete light theme with equivalent hierarchy and contrast.
- A compact monitor designed for a physical **3.7-inch display**.
- Desktop, tablet, and mobile layouts.
- Dense event history and configuration interfaces.

## 2. Design principles

### 2.1 Glance first, inspect second

The first visual layer answers four questions immediately:

1. Are both accounts healthy and authenticated?
2. How much quota remains in each window?
3. When does each window reset?
4. When is the next anchor scheduled?

Detailed metadata, actions, history, and settings belong in lower-priority regions or progressive-disclosure surfaces.

### 2.2 Quiet by default

- Use neutral surfaces for normal operation.
- Reserve color for status, warnings, failures, progress, focus, and selected actions.
- Avoid gradients, decorative glows, oversized shadows, and ornamental charts.
- Do not place every section inside a visually heavy card.
- Prefer spacing, alignment, typography, and thin separators over excessive borders.

### 2.3 Numbers are the primary content

Quota percentages, reset times, and schedule times receive the clearest hierarchy. Labels explain numbers but must not compete with them.

### 2.4 One source of truth

Use semantic design tokens instead of inserting colors directly into templates. A status such as `success` must look consistent in account state, telemetry, progress bars, events, and the small monitor.

### 2.5 Compact does not mean cramped

The desktop dashboard may be information-dense, but each region needs a clear reading order. On the 3.7-inch display, remove nonessential controls and prose rather than shrinking everything indiscriminately.

## 3. Visual direction

**Direction name:** Columned Instrument Panel
**Personality:** technical, calm, utilitarian, trustworthy
**Primary accent:** Signal Amber
**Supporting status color:** Operational Green
**Shape language:** restrained radii, thin outlines, flat layered surfaces
**Motion:** minimal and functional

Avoid the visual language of generic AI products: purple gradients, glowing orbs, glass panels, very large rounded cards, chat bubbles, or decorative illustrations.

## 4. Color system

### 4.1 Brand reference palette

These values describe the intended visual identity:

| Role | Reference | Usage |
|---|---:|---|
| Near-black | `#0E0F12` | Dark page background |
| Graphite | `#1A1C21` | Dark elevated surfaces |
| Signal Amber | `#F4B400` | Primary action, focus, warning, active emphasis |
| Operational Green | `#72D88A` | Healthy, connected, available, successful |
| Warm White | `#F4F2EC` | Dark-theme primary text and light-theme canvas family |

The implementation may use OKLCH equivalents to improve theme consistency. Do not mix raw color values throughout templates.

### 4.2 Required semantic tokens

Define every token in both light and dark themes:

- `background`: page canvas.
- `foreground`: primary text.
- `card`: grouped section surface.
- `card-foreground`: text on grouped sections.
- `surface-raised`: account panels and nested instrument surfaces.
- `popover`: drawers, menus, and overlays.
- `primary`: Signal Amber.
- `primary-foreground`: dark text on amber.
- `secondary`: quiet control surface.
- `secondary-foreground`: text on secondary controls.
- `muted`: meter tracks and subdued backgrounds.
- `muted-foreground`: timestamps, labels, and supporting metadata.
- `border`: dividers and low-emphasis outlines.
- `input`: form control border.
- `ring`: focus indicator.
- `success`: connected, operational, healthy, available.
- `warning`: partial, stale, watch, delayed, attention needed.
- `destructive`: failed, disconnected, error, dangerous action.
- `info`: waiting or neutral informational state, if needed.

### 4.3 Implemented theme values

Use these as the canonical starting point.

#### Light theme

```css
--background: oklch(0.96 0.009 91);
--foreground: oklch(0.18 0.008 265);
--card: oklch(0.985 0.004 91);
--card-foreground: oklch(0.18 0.008 265);
--primary: oklch(0.78 0.17 84);
--primary-foreground: oklch(0.17 0.008 265);
--secondary: oklch(0.91 0.009 91);
--secondary-foreground: oklch(0.24 0.008 265);
--muted: oklch(0.92 0.008 91);
--muted-foreground: oklch(0.48 0.012 265);
--border: oklch(0.84 0.009 91);
--ring: oklch(0.78 0.17 84);
--success: oklch(0.76 0.16 145);
--warning: oklch(0.78 0.17 84);
--surface-raised: oklch(1 0 0);
```

#### Dark theme

```css
--background: oklch(0.16 0.008 265);
--foreground: oklch(0.96 0.009 91);
--card: oklch(0.21 0.01 265);
--card-foreground: oklch(0.96 0.009 91);
--primary: oklch(0.78 0.17 84);
--primary-foreground: oklch(0.16 0.008 265);
--secondary: oklch(0.25 0.01 265);
--secondary-foreground: oklch(0.96 0.009 91);
--muted: oklch(0.25 0.01 265);
--muted-foreground: oklch(0.68 0.012 265);
--border: oklch(0.32 0.012 265);
--ring: oklch(0.78 0.17 84);
--success: oklch(0.76 0.16 145);
--warning: oklch(0.78 0.17 84);
--surface-raised: oklch(0.18 0.009 265);
```

### 4.4 Status rules

| State | Color | Presentation |
|---|---|---|
| Healthy, connected, ready, completed | Success | Small dot, bar fill, or concise text |
| Partial, stale, watch, delayed | Warning | Amber indicator; explain with text |
| Failed, auth failure, telemetry error | Destructive | Red indicator and visible error copy |
| Waiting, unknown | Muted or Info | Never imply success |
| Disabled or paused | Muted | Reduced emphasis, not warning by default |

Never communicate state through color alone. Pair status color with readable text, an accessible label, or both.

## 5. Typography

### 5.1 Font families

- **Display and numeric emphasis:** Sora, weights 500–700.
- **Body and controls:** Manrope, weights 400–700.
- **Fallbacks:** `ui-sans-serif, system-ui, sans-serif`.

Use Sora for page titles, section headings, account names when space allows, large quota values, and short time values. Use Manrope for explanatory copy, labels, forms, buttons, tables, and metadata.

Do not use Inter as the primary family. Do not add a monospace font unless a value is genuinely code, an identifier, or a machine-formatted path.

### 5.2 Type scale

| Role | Desktop guidance | Compact monitor guidance |
|---|---|---|
| Page title | 30–36 px, semibold | Not shown visually |
| Primary quota value | 44–52 px, semibold | 18–24 px, bold |
| Section title | 14–16 px, semibold | 11–13 px |
| Body | 13–14 px | 9–11 px |
| Metadata | 11–12 px | 8–10 px |
| Eyebrow/micro-label | 10–11 px, uppercase, semibold | 8–9 px |

- Keep letter spacing at `0`.
- Use uppercase only for short labels.
- Use tabular numerals where changing values would otherwise shift horizontally.
- Truncate long account labels to one line in constrained areas, while exposing the full value through `title` where appropriate.

## 6. Shape, border, and elevation

- Base radius: **8 px**.
- Small controls and meter tracks: **4–6 px**.
- Primary grouped section: no more than **12 px**.
- Avoid pill shapes except for compact status labels and progress tracks.
- Default outline: 1 px using the semantic border token.
- Use shadows sparingly; the hierarchy should remain understandable with shadows removed.
- Never nest several fully outlined cards. Use a section surface, then internal spacing and dividers.

## 7. Spacing and layout

Use a 4 px base grid.

| Token concept | Value | Typical use |
|---|---:|---|
| 1 | 4 px | Tight icon gap |
| 2 | 8 px | Compact control gap |
| 3 | 12 px | Internal row spacing |
| 4 | 16 px | Mobile page padding, standard gap |
| 5 | 20 px | Card padding |
| 6 | 24 px | Section padding and separation |
| 7 | 28 px | Desktop horizontal padding |
| 8 | 32 px | Desktop page rhythm |

### 7.1 Desktop container

- Maximum content width: **1440 px**.
- Horizontal page padding: 16 px on small screens and 28 px from the small breakpoint upward.
- Main vertical padding: 24 px mobile, 32 px desktop.

### 7.2 Responsive behavior

- Below approximately 640 px: one-column content; hide secondary header copy before shrinking controls.
- From approximately 640 px: account telemetry windows may use two columns.
- From approximately 768 px: reveal full primary navigation.
- From approximately 1024 px: show two account columns and the three-column secondary dashboard.
- Long tables scroll horizontally rather than compressing columns into illegibility.
- Configuration forms collapse naturally into one column on narrow screens.

## 8. Dashboard information architecture

### 8.1 Header

The header contains:

- A compact `Q` brand mark.
- `ai-quota-monitor` and the installed version.
- Navigation: Overview, Events, Monitor.
- Theme toggle.
- Settings button.

The header is a single quiet band separated from the page by one border. Do not make navigation links look like large pills. On narrow screens, preserve brand, theme, and settings; collapse or reduce navigation labels as needed.

### 8.2 Primary quota section

Use one main grouped section titled **Quota window**.

Its top row contains:

- Live state and account count.
- One page-level H1.
- Next-refresh countdown and local time on wider screens.

Below it, show one account panel per account. Each panel contains, in order:

1. Account identity, plan, authentication state, and enabled/paused state.
2. The most important remaining quota value.
3. The 5-hour and 7-day windows side by side where space permits.
4. Reset timing and telemetry freshness.
5. Next anchor or schedule summary.
6. Essential contextual actions only.

Use a large number and a thin progress bar. Avoid radial gauges: they consume space and slow comparison.

### 8.3 Secondary operational row

On desktop, use three balanced columns:

1. **Scheduled anchors** — next jobs and relative time.
2. **Recent activity** — concise event list with status dots.
3. **System column** — deployment health and a compact monitor preview.

On smaller screens, stack in that same priority order.

### 8.4 Footer actions

Place global data freshness and storage status on the left. Place low-frequency actions such as refresh and schedules on the right. The footer should feel like a utility strip, not another card.

## 9. Account and quota patterns

### 9.1 Account identity

- Use the actual display label or email.
- Truncate safely with an accessible full label.
- Show the plan as quiet metadata.
- Use a small success/warning/error dot beside the account, not a large colored banner.

### 9.2 Quota windows

Each quota window must display:

- Window name: `5-hour window` or `7-day window`.
- Remaining percentage as the main value.
- Thin horizontal progress bar.
- Reset time and source when available.
- Status text when stale, waiting, partial, or errored.

Bar color is status-dependent:

- Success for healthy available capacity.
- Amber for watch/partial/stale conditions.
- Destructive for high usage or errors.
- Muted for unavailable values.

Clamp meter widths to 0–100% and preserve the server-provided status rules.

### 9.3 Actions

Keep account actions visually subordinate to telemetry:

- Primary action: the most likely immediate action, such as Anchor or Login depending on state.
- Secondary actions: Check, Refresh, Settings.
- Destructive action: Logout, visually restrained until hovered or focused.
- Disabled actions must remain readable and clearly unavailable.

## 10. Small-screen monitor: 3.7-inch target

The monitor is a dedicated operational surface, not a compressed desktop dashboard.

### 10.1 Content priority

Show only:

1. Live indicator and current local time.
2. Account label and health indicator.
3. 5-hour remaining quota.
4. 7-day remaining quota.
5. Reset timing and meaningful drift/error state.

Do not show the standard app header, settings, long descriptions, full event history, deployment details, or general dashboard actions.

### 10.2 Layout

- Use the full viewport with 4–6 px outer padding.
- Stack account panels vertically by default.
- Within each account, place the two windows in a stable two-column grid.
- Use a fixed visual hierarchy so changing percentages never reflow the layout.
- Keep bars at approximately 6–7 px tall.
- Use short labels: `5h`, `7d`, `obs`, and compact times.
- Account links may remain as tiny overflow-safe navigation, but never crowd the readings.

### 10.3 Theme

The physical monitor remains dark-first for stable low-light viewing. It may inherit status tokens, but should not become a bright light-theme surface unless the product explicitly requires this.

### 10.4 Physical-display verification

Verify at the actual browser viewport used by the device. At minimum, test:

- 360 px or narrower layouts.
- Short viewports around 280 px tall.
- Multiple accounts with long labels.
- Missing telemetry.
- Stale telemetry.
- Error text.
- Percentages from `0%` through `100%`.

No important value may be clipped, overlap another element, or require horizontal scrolling.

## 11. Event history

- Keep filters in one unframed or lightly framed toolbar above the results.
- Use labels above controls and a clear Apply filters action.
- The table should prioritize timestamp, level, category, account, and message.
- Use compact status text; avoid a bright badge in every row.
- Keep payload details collapsed by default and reveal them with a native disclosure control.
- On small screens, retain horizontal scrolling and frozen comprehension rather than transforming each row into a large card.
- Empty states should be direct and quiet.

## 12. Settings drawer

Settings remain in a right-side drawer to protect dashboard context.

- Width: approximately 34 rem, capped at the viewport width.
- Fixed heading and close control; independently scrolling content.
- Group sections by purpose: deployment, global anchor prompt, then one schedule group per account.
- Use semantic form labels and native inputs.
- Use checkboxes or switches for binary values, not text buttons.
- Use compact day controls for run-day selection.
- Place Save beside the section it affects; do not use one ambiguous global Save.
- Require clear confirmation for destructive or restart-capable operations.
- Close on Escape and backdrop interaction; restore focus to the opener.
- Trap keyboard focus while open.

## 13. Components

Build or maintain these reusable patterns:

- `Button`: default, secondary/outline, ghost, destructive, icon-only; small and default sizes.
- `IconButton`: always includes an accessible label and tooltip/title.
- `StatusDot`: success, warning, error, waiting, muted.
- `StatusLabel`: use only when text is necessary; do not overuse pills.
- `UsageBar`: accepts value and semantic state; clamps values and provides an accessible text equivalent nearby.
- `QuotaWindow`: label, remaining percentage, reset information, state.
- `AccountPanel`: identity, account state, quota windows, next anchor, actions.
- `SectionHeader`: title, optional count/status, optional action.
- `ActivityItem`: state dot, title, account/context, timestamp.
- `Drawer`: accessible close behavior, backdrop, focus handling.
- `DataTable`: responsive scroll wrapper and readable cell hierarchy.

## 14. Icons and controls

- Use a consistent outlined icon family such as Lucide, or use a small internal SVG set with matching stroke weight.
- Use familiar icons for theme, settings, refresh, schedule, history, and monitor.
- Icon-only controls must have `aria-label` and `title` or a tooltip.
- Standard controls should be at least 36 px square on the dashboard.
- The 3.7-inch monitor is display-first; its tiny navigation links are an exception only when physically necessary.

## 15. Motion

- Control transitions: 120–180 ms.
- Drawer entrance: approximately 180 ms ease-out.
- A live indicator may pulse slowly around 2.6 seconds, using opacity only.
- Never animate percentages or progress bars continuously.
- Under `prefers-reduced-motion: reduce`, remove nonessential animation and transitions.

## 16. Accessibility

- Maintain at least WCAG AA contrast for text and controls.
- Use exactly one visible H1 per page.
- Keep semantic landmarks: header, nav, main, section/article, footer.
- Provide visible keyboard focus using the amber ring token.
- Associate every input with a label.
- Use `aria-live` sparingly for meaningful refresh failures, not every polling update.
- Announce drawer semantics with `role="dialog"`, `aria-modal="true"`, and a labelled heading.
- Provide textual state alongside color.
- Preserve keyboard use for filters, forms, drawer controls, and actions.
- Do not let automatic refresh move keyboard focus.

## 17. Data refresh states

Polling regions require stable states:

- **Idle:** normal presentation.
- **Loading:** retain current data; reduce opacity only slightly or show a compact refresh indicator. Do not blank the region.
- **Success:** update values without changing the panel footprint.
- **Error:** retain the last known data and show a concise error message or outline.
- **Stale:** explicitly label data as stale; do not present it as current.

## 18. Content style

Use short operational language:

- `Quota window`
- `98% available`
- `Resets 19:39`
- `Next anchor 05:00`
- `Telemetry stale`
- `Anchor completed`

Avoid marketing copy, conversational filler, unexplained acronyms, and long instructional prose inside the interface.

## 19. Do / do not

### Do

- Lead with remaining quota and reset timing.
- Use columns and alignment to support comparison.
- Keep normal operation visually quiet.
- Let amber mark attention and interaction.
- Let green indicate healthy operation.
- Preserve all current server behavior and data states.
- Verify both themes and the actual tiny display.

### Do not

- Rebuild the product as a marketing page.
- Replace the real application with static mock data.
- hide operational errors to make the interface look cleaner.
- Use one card for every label/value pair.
- Use large pills for ordinary navigation.
- Use multiple competing accent colors.
- Use radial quota charts.
- shrink the desktop dashboard wholesale for the monitor.
- Remove working forms, routes, polling, filters, or state logic during the redesign.

## 20. Acceptance checklist

- [ ] Dark mode is the default and light mode is complete.
- [ ] Theme choice persists across reloads.
- [ ] Signal Amber, Operational Green, and semantic error states are tokenized.
- [ ] Sora is used for display hierarchy; Manrope is used for body copy.
- [ ] The dashboard first viewport shows both account quota states on common desktop sizes.
- [ ] The two account panels are directly comparable.
- [ ] 5-hour and 7-day values, resets, telemetry state, and next anchors remain visible.
- [ ] Event history filters and payload disclosure still work.
- [ ] Settings remain accessible in a keyboard-friendly drawer.
- [ ] The monitor fits a 3.7-inch screen without clipping or horizontal scrolling.
- [ ] Missing, stale, partial, error, disconnected, paused, and disabled states are visually distinct.
- [ ] Automatic refresh does not cause major layout shift or focus loss.
- [ ] All existing routes, form actions, template variables, and tests continue to work.
