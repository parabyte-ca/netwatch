# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.33.0] - 2026-11-28
### Added
- **Traffic volume on the network map.** A **Traffic** menu above the map switches every tile and
  link from live rates to the bytes moved in the last hour, 24 hours, 7 days or 30 days (kept in
  your browser). A tile's details show the figure and the link thickness follows the average rate.
  `GET /api/map` takes `traffic_hours` (1, 24, 168 or 720). Volume comes from the Decos' per-device
  counts, so it covers internet-bound traffic only, and it is not shown in replay.

## [0.32.1] - 2026-11-27
### Changed
- **Pin from either side.** The Home Hub and each Deco tile now list the devices pinned behind them
  and let you pin more, just like a switch, and a device's **Connected through** card gains **The
  Home Hub**. Both write the same pins.
- A device last reported on Wi-Fi by a Deco can now be pinned behind a switch (with a note) instead
  of being refused. After a network card swap the report can be out of date, which made it
  impossible to attach a machine such as moogbox.

## [0.32.0] - 2026-11-27
### Added
- **Switches on the map.** An unmanaged switch cannot be seen on the network, so you now draw it:
  **Add switch** above the map, choose what it plugs into (the Home Hub, a Deco or another
  switch), and pin devices behind it from its tile or from a device's new **Connected through**
  card. Decos count as pin targets too (their wired clients are their Ethernet ports). A switch is
  up when anything behind it is. NetWatch suggests a switch on a Deco with three or more wired
  devices and lists devices no Deco reported, but never moves anything itself.
### Changed
- The fixed, inferred "Switch" tile becomes your first switch (TrueNAS pinned behind it, its saved
  position kept). If you remove every switch, the inferred tile comes back.
- Switches are map only: the diagnosis, health score and Bell report do not use them. A Wi-Fi
  device cannot be pinned behind a switch. Replay shows today's wiring.
- The map tidies itself again whenever a tile or link is added, removed or re-pinned, so a new
  switch or a re-pinned device lands in its place (tiles you dragged keep their position).

## [0.31.0] - 2026-11-27
### Fixed
- **Map links no longer curl around tiles.** Links always left a tile's bottom edge and arrived at
  the top edge, so where a tile sat above its parent (Internet above Bell network above the Home
  Hub, a remote server above Internet, or any tile you dragged) the line looped out of the wrong
  side. Each link now uses the sides that face the other tile, and re-routes as you drag.
### Added
- **Pin a tile's link side.** Tap a tile and set **Links attach to** to Top, Bottom, Left or Right
  (or Auto). Saved with the layout; Reset layout clears it.

## [0.30.1] - 2026-11-22
### Changed
- **YouTube Allowed also allows YouTube's domains.** When a device or group's YouTube setting is
  **Allowed** (no Restricted Mode), NetWatch now adds allow rules for `youtube.com`,
  `youtube-nocookie.com`, `youtube.googleapis.com` and `youtubei.googleapis.com` for it (subdomains
  such as `www.` are covered), and removes them when the setting changes or a schedule turns
  YouTube off. They are kept apart from your own always-allowed list: not shown there and not
  counted toward its limits. This fixes YouTube staying restricted or half-loading when a filter
  list blocks one of those domains.

## [0.30.0] - 2026-11-22
### Added
- **"Same machine?" suggestions.** After a network card swap (or any change of MAC on a wired
  device) the machine appeared twice, the new card online and the old one grey. A **Same
  machine?** card on the Devices page now proposes the pair when they share a real hostname or an
  address and only one is online. **Merge** keeps the older entry's name, group, tags, notes and
  monitoring; **Not the same** hides the pair for good. Nothing merges on its own.

## [0.29.0] - 2026-11-21
### Added
- **Copy a device's DNS settings.** A device's AdGuard Home settings card has **Copy from another
  device**: choose a source, tick what to copy (always-allowed domains, blocked apps, filtering
  and YouTube, Internet-off schedules), choose Merge or Replace, and optionally add more devices
  to copy to. A preview shows the result for each device before you confirm; AdGuard Home is
  updated once, and each device is noted in the Security log.

## [0.28.2] - 2026-11-19
### Added
- **Ask NetWatch types its answers out.** A new answer rolls onto the screen as if quickly typed
  (never more than about 5 seconds), with a blinking cursor, and the tray stays scrolled to the
  end. Tap the answer to show the rest at once. With reduced motion turned on, answers appear whole.

## [0.28.1] - 2026-11-19
### Fixed
- A remote server is now drawn straight above the Internet tile (centred over it, or in a row
  when there are several), and its link runs down to the tile instead of looping over it.

## [0.28.0] - 2026-11-19
### Added
- **Remote servers on the map.** Add a server of yours outside the network (a VPS, a friend's NAS,
  a public IP) as a target with the group **Remote server**. It is probed by ping, a TCP port or a
  web page like any target, and drawn on the map beyond the Internet node with its live status and
  latency, in replay too. Its page has an Access card: save the SSH user and port, and an admin page
  address, by hand (remote servers are never scanned).
### Changed
- Remote servers are kept out of the diagnosis: a remote server that is down opens its own event, but
  never changes the Internet, Bell or Home Hub verdict, the health score or the Bell report.

## [0.27.0] - 2026-11-19
### Added
- Device pages have an **Access** card. "Open admin page" appears for a device whose web interface
  the scan found (ports 80, 443, 8080, 8443, 5000, 8123, 32400; the usual ports first, the rest
  listed under it). "Open in SSH app" and "Copy command" appear for a device with SSH open: save the
  user you log in as (and the port) under Settings. A per-device switch can always show or hide SSH,
  an admin page address can be saved, and "Check now" re-scans just that device.
### Changed
- The Admin, Servers list from 0.26.0 is gone: SSH belongs on the device. When NetWatch updates, each
  saved server moves onto the device with its address; one that matches no device is noted in the
  Security log. Needs the database to update (added and removed tables, automatic).

## [0.26.0] - 2026-11-18
### Added
- Admin, Servers: a short list of the machines you log in to (AdGuard Home, TrueNAS and so on) with
  a name, an address on your network, a port and a user. Each one has "Open in SSH app", a link that
  opens an SSH app on your phone or computer, and "Copy command". The address box suggests machines
  NetWatch already knows. NetWatch stores no password or key and has no terminal of its own; adding,
  changing and removing a server goes in the security log. Needs the database to update (one new
  table, added automatically).

## [0.25.2] - 2026-11-17
### Changed
- Restarting a Deco from NetWatch did nothing on one home network, and NetWatch could not say
  why. Each restart request now waits up to a minute for the Deco's reply (a satellite's restart
  goes through the main Deco), and NetWatch keeps what it saw: whether the Deco answered, how long
  it took, an error code if the Deco sent one, and which kind of Deco sign-in was used. Admin, Deco
  mesh shows the last attempts ("no reply after 60 s (app session); it never went offline"), and
  the Security log says the same. A restart the Deco accepted but never acted on is now reported
  too, not only one that got no reply.

## [0.25.1] - 2026-11-17
### Fixed
- Restarting a Deco showed an error ("502") even though a Deco that accepts a restart simply stops
  answering. NetWatch now sends the restart once (it used to send it a second time when the Deco
  did not answer), treats a Deco that goes quiet as a restart in progress, and checks it: if the
  Deco goes offline and comes back the timeline says so, and if it is still online three minutes
  later NetWatch warns that it did not restart. Real failures now answer with a message your
  reverse proxy will not replace, and an error the proxy did hide now points you to the Security
  log.

## [0.25.0] - 2026-11-17
### Added
- Restart a Deco, or the whole Wi-Fi network, from NetWatch. Off by default: turn on "Deco actions
  (restart)" under Admin settings, Integrations. Admin, Deco mesh then has a Restart button for
  each Deco and "Restart all Decos" (satellites first, the main Deco last). A diagnosis that
  blames one Deco offers "Restart <name>" next to the cause. Every restart asks first and says
  what will drop, goes in the security log, and is limited to one restart of a Deco every five
  minutes and one whole-network restart every 15. When the Deco comes back the timeline says how long it took;
  a restart you asked for is information, not a critical outage. Ask NetWatch knows about it
  but cannot restart anything itself.

## [0.24.4] - 2026-11-17
### Changed
- A laptop you monitor that drops off the network is no longer a warning. A laptop is usually
  switched off or taken to work, so the timeline now says "<name> left the network (away or
  switched off)" as information, and closes it when the laptop is back. Phones, tablets and wired
  devices still warn. Who's home already treated an offline laptop as away, and still does.

## [0.24.3] - 2026-11-17
### Fixed
- A phone that had left home still showed as home. AdGuard Home keeps a client and a DHCP lease
  for every device it has met, and NetWatch counted each AdGuard poll as seeing the device. Names
  and leases from AdGuard Home (and reverse DNS names) now only name a device; only the Deco, the
  network itself and its traffic say it is on the network. A phone with an AdGuard profile now
  goes away about 15 minutes after it leaves (the "Away after" setting in Who's home).

## [0.24.2] - 2026-11-17
### Changed
- On a phone the NetWatch header and its menu button stay at the top of the screen while you scroll,
  so you can open the menu from anywhere on a long page. The open menu is a panel over the page
  that fits the screen (and scrolls inside itself on a small phone), so opening it no longer
  pushes the page down or moves your place. Things scrolled into view stay clear of the header.
  On a computer the header scrolls away as before.

## [0.24.1] - 2026-11-17
### Changed
- The Ask NetWatch tray now slides up when opened and slides down when closed (no slide if your
  device asks for reduced motion). Tapping anywhere outside it, pressing Esc or using the close
  button slides it away; the tap still goes to whatever you tapped. Your conversation and half-typed
  question are kept while it is down. Closed, it is skipped by keyboard focus and screen readers.

## [0.24.0] - 2026-11-16
### Added
- **Always allowed** domains per device and per group, in the device's AdGuard Home settings and
  the group's dialog. Subdomains are included, a pasted address keeps its host, and AdGuard Home's
  or the gateway's address is refused; up to 50 each. A device gets its group's exceptions plus
  its own, and adding one never drops its group's filtering. Each is an
  `@@||domain^$client='NetWatch: …'` rule among NetWatch's own lines; internet off schedules still
  win. Every change is confirmed and written to the security log.
- On a device's DNS card, **Allow here** next to a blocked domain fills in the device's exception
  (beside **Allow for all**, the global rule). The DNS page counts each client's exceptions.
- Ask NetWatch reads AdGuard Home: overview, per-device and per-group settings with exceptions and
  conflicts, schedules, a device's DNS lookups, plus integration health, speed against your plan
  and who's home. Its instructions explain how settings, schedules and exceptions combine, so it
  can answer "why is this site blocked on Sam's phone?".
- **Ask NetWatch: DNS lookups per device** (Admin settings, Integrations, on by default): off
  keeps per-device domain lists out of what is sent to Anthropic.
- `PUT /api/adguard/actions/profiles/{device|group}/{id}/allow`.

## [0.23.1] - 2026-11-16
### Fixed
- Per-device and per-group AdGuard Home settings now reach devices when AdGuard Home is not your
  DHCP server. NetWatch's clients listed only MAC addresses, which AdGuard Home can match only
  through its own DHCP leases; with the Home Hub handing out addresses (or an iPhone's private
  Wi-Fi address) the device fell back to the global settings, so "YouTube allowed" still showed
  "Restricted Mode has hidden comments". Each NetWatch client now also lists its devices' current
  IP while they are online, skipping an address two devices report or one of your own clients
  holds. The next sync (within about 15 seconds of updating) applies it.

## [0.23.0] - 2026-11-15
### Added
- Integration health on the System page: one row per feed NetWatch relies on (Deco, Home Hub,
  AdGuard Home, packet capture, the host network port, the nightly load test and exposure scan,
  the weekly report and the update helper) with its state (OK, starting, stopped, paused,
  polling off, not set up), last good read, successful reads in the last 24 h, the last error
  and the fix that applies: turn on polling, open the settings, run now or write the report now.
- The Overview shows one line when a feed has stopped or is paused, with a link to the card.
- A feed stopped for 30 minutes opens an information event of the new kind "Integrations"
  ("AdGuard Home has stopped working"), shown on the timeline by default and closed when the
  feed reads again. `GET /api/admin/integrations/health`.

### Changed
- The Wi-Fi coverage floor plan is off the roadmap: the Deco API reports no per-device signal,
  only the signal between Decos.

## [0.22.0] - 2026-11-15
### Added
- Speed against your plan: plan download and upload speeds and a "slower than" threshold under
  **Admin → Internet plan** (not set by default). The nightly bufferbloat test's throughput is
  shown on the Health page as a share of the plan: last result, 30-day medians and a 90-day chart
  with the plan and threshold lines. Diagnostics shows Mb/s with "% of plan".
- Three scheduled tests in a row under the threshold (70 % by default) open an information event
  "Internet slower than your plan" (new timeline filter "Speed"); the next good night closes it.
  Manual tests don't count.
- A "Speed vs plan" part in the health score (weight 10, only when plan speeds are set) and a
  speed line in the weekly report. `GET /api/speed-plan`.
- Each test stores the plan speeds and NetWatch's network port speed at the time, so a later
  plan change reads correctly and a result capped by a 1 Gb/s port is called out.

### Changed
- The load test's throughput leaves out the first second (TCP ramp-up), which read about 10 %
  low on fast lines.

## [0.21.0] - 2026-11-15
### Added
- Who's home: **Devices → People…** to add a person with a name, a colour and up to 8 devices
  (private-MAC siblings count too). Someone is home while one of their devices was seen within
  **away after** minutes (15 by default, 5 to 240); a departure is dated by the last sighting and
  an arrival is noted at once. State survives restarts without inventing arrivals.
- A "Who's home" strip on the Overview, a page per person (today's arrivals and departures and a
  7-day home/away bar), arrivals and departures on the timeline as information (new "Who's home"
  filter, never quieted or alerted), and arrivals per person in the weekly report.
- `GET/POST /api/people`, `PATCH/DELETE /api/people/{id}`, `GET /api/people/{id}/history`,
  `PUT /api/people-settings`. Migration 0012 (`people`, `people_devices`).

### Changed
- A person's devices are kept by the old-device clean-up, like named or grouped devices.
- Removing a person deletes their arrivals and departures; adding or removing someone is written
  to the security log.

## [0.20.0] - 2026-11-15
### Added
- Install NetWatch as an app: a web app manifest and icons, and **Install app** in the menu when
  the browser offers it (Chrome, Edge, Android), or Share, then Add to Home Screen instructions in
  Safari on iPhone and iPad. Hidden once installed. Installing needs HTTPS (or `localhost`).
- A small service worker keeps only the page shell, the logo and the hashed app files, so the
  installed app opens without a connection. API answers are never stored. A new version takes
  over the next time the app opens.
- "No connection to NetWatch" banner while NetWatch does not answer; the sign-in screen says the
  same when the app is opened away from home and tries again every 10 seconds by itself.

## [0.19.1] - 2026-11-15
### Fixed
- YouTube **Allowed** now also turns off Restricted Mode. Before, a device left on Default filtering
  copied AdGuard Home's global Safe Search, which includes YouTube, so comments stayed hidden
  ("Restricted Mode has hidden comments"). Existing settings are corrected on the next sync.
- Calmer rejoin and address-change alerts for phones with private Wi-Fi addresses: a device that
  asks for the address it already had is not counted, flipping back to a recent address counts
  once, a rotated private MAC settling in is ignored, and phones, tablets, laptops and
  private-address devices need 12 rejoins an hour or 6 new addresses a day (others 4 and 3). The
  alert says why it matters: "rejoined the network 5 times in an hour (it may be losing Wi-Fi)".

### Added
- CI checks every page at phone width (390 px, also with 1.3x text) against seeded data; images
  and releases wait for it.

## [0.19.0] - 2026-11-15
### Added
- Schedules for a group or a device (with AdGuard Home actions on): days, a start and an end time
  (a window may end the next morning) and what happens while it is on: internet off, YouTube off,
  apps blocked or strict filtering. Presets: School nights, Homework, Bedtime. Exceptions from
  NetWatch: +15 min, +1 hour, off until it next starts, skip today, and back on now; "Internet off
  now" for 30 minutes, 1 hour or 2 hours. Shown in the group's AdGuard Home dialog and on the
  device's page, as "Active now" on the Overview, as a list on the DNS page, and as information
  events on the timeline. Times follow `TZ`, daylight saving included. A schedule starts or ends
  within about 15 seconds; every change is confirmed and written to the security log.
- Internet off is a rule for the NetWatch client in AdGuard Home (`||*^$client='NetWatch: Kids',important`),
  added and removed by NetWatch; your own rules are never touched.
- `GET/POST/PUT/DELETE /api/adguard/actions/schedules`, `POST /schedules/{id}/extend`,
  `DELETE /schedules/{id}/exception`, `POST /schedules/now`, `DELETE /schedules/now/{id}`.

## [0.18.3] - 2026-11-15
### Fixed
- Pages no longer get wider than a phone screen. The device page's "Same device as…" list took the
  width of its longest device name; grids that only had columns on wider screens sized themselves
  to their content; and long words, form fields and button rows could not wrap. Form controls and
  labels now never outgrow their container, grids default to a column that can shrink, and long
  text wraps (tables keep whole words and scroll inside their own box). Checked on every page at
  390 px, also with a larger phone text size.
- Device names with an apostrophe or accent from mDNS showed escaped bytes ("Theresa\226\128\153s
  iPhone"). They are decoded now, and names already stored are repaired when NetWatch starts.

### Added
- `frontend/scripts/check-layout.mjs`: visits every page at 390 px and fails if one is wider than
  the screen (see Local development in the README).

## [0.18.2] - 2026-11-15
### Fixed
- The DNS page charts were spiky and broken up: each minute was its own point, and a quiet minute
  (no queries, so no response time) broke the line. Readings are now grouped into 5-minute (6 h),
  15-minute (24 h) or 1-hour (7 days) buckets, with response times weighted by the number of
  queries and the block rate taken over the whole bucket. A time with no readings at all (polling
  off, NetWatch stopped) now shows as a gap instead of a straight line across it.

## [0.18.1] - 2026-11-15
### Changed
- New Deco, Home Hub and AdGuard Home set-ups poll by default (the box starts ticked); a saved
  set-up keeps its choice.

### Added
- The DNS page shows "Saved, but not polling" with a Turn on polling and save button, and the
  Overview says when an integration is set up but not polling, so a gap in the data is noticed.
- `GET /api/admin/integrations` and `POST /api/admin/integrations/{name}/polling`.

## [0.18.0] - 2026-11-15
### Added
- YouTube and app controls per device and per group, next to filtering: YouTube Restricted Mode,
  YouTube blocked, or YouTube allowed (even when it is blocked for everyone else), and apps to
  block chosen from AdGuard Home's own list. With filtering left at Default, the devices keep
  AdGuard Home's global filtering, safe browsing, parental control and safe search (copied and
  refreshed every 5 minutes), and apps blocked for everyone stay blocked. Set them in the group
  manager (AdGuard Home button) or on a device's page; the DNS page shows each client's settings in
  words, as does the security log.

### Changed
- `PUT /api/adguard/actions/profiles/device/{id}` and `/profiles/group/{id}` take `filtering`,
  `youtube` and `services` instead of `profile`; `GET /profiles` adds `services_all`. Settings
  saved by 0.17 are read as their filtering.

## [0.17.0] - 2026-11-15
### Added
- AdGuard Home settings per device and per group (with AdGuard Home actions on): No blocking (an
  exception: nothing is blocked) or Strict (blocklists, safe browsing, parental control and safe
  search, which includes YouTube Restricted Mode). Set it for a group in Devices → Groups… or for
  one device on its page; the DNS page lists them. NetWatch keeps an AdGuard Home persistent client
  for each ("NetWatch: Kids") with every MAC it knows for the devices; a device's own setting beats
  its group's, and group changes reach AdGuard Home within about 15 seconds (everything is checked
  again every 5 minutes). Only the clients NetWatch made are changed; a device already in one of
  your own clients is left out and reported. Changes are confirmed and written to the security log.
- `GET /api/adguard/actions/profiles`, `PUT /api/adguard/actions/profiles/device/{id}` and
  `/profiles/group/{id}`.

## [0.16.0] - 2026-11-15
### Added
- Full screen map: a Full screen button (or the F key; Esc leaves) fills the screen with the map,
  with the options in a slim bar and the details panel over the map. `/map?full=1` opens straight
  into it, for a wall tablet.
- DHCP reservations through AdGuard Home, when it is the DHCP server and AdGuard Home actions are
  on: reserve a device's address from its page (IP, MAC and name filled in), change or remove
  reservations from the DNS page (also ones made in AdGuard Home, after a warning), and reserve
  the current addresses of the devices ticked on the Devices page. The address must be inside
  AdGuard Home's DHCP network, not the gateway or AdGuard Home, and not reserved for or leased to
  another device; a failed change puts the old reservation back. Every change is confirmed and
  written to the security log. Private (randomised) Wi-Fi addresses are flagged.
- `GET /api/adguard/actions/dhcp`, `POST`/`DELETE /api/adguard/actions/dhcp/reservations` and
  `POST /api/adguard/actions/dhcp/reservations/bulk`.

## [0.15.1] - 2026-11-15
### Fixed
- Allowing or blocking a domain from the DNS page failed with "unexpected filtering answer"
  when AdGuard Home had no custom rules yet (it answers `user_rules: null`). An answer with no
  `user_rules` at all is still refused, so NetWatch never writes over rules it could not read.

### Added
- The map explains why every device is shown under the Home Hub: Deco polling off, paused (with
  the reason) or waiting for the first reading, with a link to Admin.
- The Deco, Home Hub and AdGuard Home cards in Admin warn when settings are saved but polling is
  off, with a Turn on polling and save button.
- `state` (`off`, `not_polling`, `paused`, `waiting`, `ok`) in each integration's Admin status,
  and `deco` on `GET /api/map`.

## [0.15.0] - 2026-11-14
### Added
- Quiet groups. Tick Quiet on a group and alerts about its devices (address changes, rejoining,
  unusual DNS) are recorded as information with a "quiet" label instead of warnings. The
  timeline hides them unless Show quiet events is ticked. Outages, packet loss and security
  events are never quieted.
- Groups at a glance on the Devices page: one card per group (and Ungrouped) with devices online,
  traffic down and up, DNS queries and blocked share, outages and alerts, for 24 hours or 7 days.
  Click a card to filter the table to that group.
- The weekly health report has a Groups section (in the app and in the Markdown download):
  devices, traffic, DNS, outages, alerts and quiet events per group.
- `GET /api/device-groups/stats?hours=24|168`, and `include_quiet` on `GET /api/events`.

### Database
- Migration 0011 adds a `quiet` column to `device_groups`.

## [0.14.0] - 2026-11-14
### Added
- Device groups and tags. Groups have a name, a colour and an icon (for example Smart home,
  Family, Kids, or one child's devices); tags are free labels, several per device. Manage groups
  from Devices → Groups…, accept suggested groups based on the device type, assign one device on
  its page or many at once with the new selection bar, and filter the Devices page and the
  timeline by group or tag. Export and import the set-up as a file (devices matched by MAC).
- Every device on the map: each device is its own node under its Deco (or the Home Hub),
  coloured by group, status or live bandwidth, with its tags as badges. Many devices under one
  Deco are laid out in a grid with groups together. Group chips highlight a group or fold it into
  one node; recently offline devices can be shown; the current colouring, filters and folded
  groups can be saved as named views. Folded groups and dragged positions are saved in NetWatch.

### Changed
- The map no longer collects unnamed devices into an "N more devices" node.
- Devices in a group or with tags are never forgotten as stale, like named or monitored ones.

### Database
- Migration 0010 adds `device_groups`, `device_tags` and a `group_id` column on `devices`.

## [0.13.0] - 2026-11-14
### Added
- AdGuard Home actions, off by default (Admin → settings → Integrations). When turned on, the
  DNS page gets Controls to pause protection for 5 minutes to 8 hours, allow or block a domain,
  and switch the upstream DNS. Allow and Block links also appear next to the top (blocked)
  domains on the DNS page and each device's page. Every change asks for confirmation first, and
  every change or failed attempt is written to the security log.
- A paused protection is turned back on when its time is up, even after a NetWatch restart or
  with an AdGuard Home version that ignores the pause length. A pause started from NetWatch shows
  on the timeline as information instead of a warning.
- Rules added from NetWatch (`@@||domain^` to allow, `||domain^` to block) are tracked and can be
  removed again from the DNS page; your own custom rules are never changed.
- Upstream DNS switches are tested by AdGuard Home first and only applied when every server
  passes; the previous list is kept for a one-click switch back.

## [0.12.0] - 2026-11-14
### Added
- AdGuard Home feeds the device inventory. Names given to clients in AdGuard Home (by MAC, or
  by IP for the device NetWatch knows at that address) become device names and help classify
  the device. When AdGuard Home is the DHCP server (detected from `/control/dhcp/status`), its
  leases add devices with their hostnames and addresses every 5 minutes. Clients known only by
  an address NetWatch hasn't seen are listed as unknown clients instead of becoming devices.
- Device page: a DNS section with the device's queries, block rate, queries per hour, top
  domains and top blocked domains over 24 hours or 7 days.
- DNS page: top domains, top blocked domains, unknown clients, and whether AdGuard Home is the
  DHCP server.
- In-app update progress: the System page follows an update step by step from the update log
  (requested, agent started, backup, download and verify, restart, health check, done), with a
  timer, the time each step took, a bar that moves on the typical time of your recent updates but
  never runs ahead of the real step, the latest log line, and a finish that shows how long the
  update took or the step that failed and whether it rolled back. While an update runs, a thin
  bar across the top of every page and the percentage in the browser tab show its progress, and
  both keep going while NetWatch restarts.
- Anomaly alerts when a device makes far more DNS queries than usual (over 500 in an hour and
  5 times its usual hour) or has most of its queries blocked (50% or more of at least 100, when
  it usually has under 15%). A device needs 24 hours of history first; each alert waits 6 hours
  before repeating for the same device.

### Changed
- The "more than one DHCP server" alert explains the switch from the Home Hub to AdGuard Home.

### Database
- Migration 0009 adds the `adguard_clients` table (queries per client and hour). Domain names in
  it are cleared after the raw-sample retention (7 days by default); the counts are kept as long
  as the 1-minute rollups.

## [0.11.0] - 2026-11-14
### Added
- AdGuard Home integration (read-only). Under Admin → AdGuard Home, enter the address, port,
  HTTPS option, username and password of your AdGuard Home server, test and save. NetWatch reads
  its status, statistics, query log and client list every minute and records query volume, block
  rate, SERVFAIL and other errors, response time (average and 95th percentile) and per-upstream
  health. The password is encrypted, never shown again, and only sent to the address, port and
  protocol it was saved for; polling stops after 3 failed sign-ins.
- DNS timeline events: AdGuard Home unreachable, protection off, upstream resolver failing, DNS
  error spikes and response-time spikes (each with a 30 minute cooldown), and new unknown clients.
- Diagnosis: "DNS is the cause" verdicts when AdGuard Home was unreachable or its upstream
  resolvers were failing while the Home Hub stayed reachable, and a "slow DNS" cause. AdGuard
  outages and failing upstreams now open incidents even when every probe is healthy. DNS targets
  aimed at AdGuard Home get their own "AdGuard Home" layer instead of counting as public DNS.
- New DNS page: response time and block rate over 6 hours, 24 hours or 7 days, upstream health
  and the busiest clients, with warnings when AdGuard Home is down or protection is off.
- Incident report: a "DNS (AdGuard Home)" section, and "AdGuard Home stayed reachable" under
  ruled out when it did.
- README: setting up AdGuard Home on a Raspberry Pi.

### Changed
- The "must be on your LAN" error now names the device it is about (Deco, Home Hub or AdGuard
  Home) instead of always saying "the Deco".

### Database
- Migration 0008 adds the `adguard_stats` table (one row per poll, kept as long as the 1-minute
  rollups).

## [0.10.1] - 2026-10-23
### Added
- "Laptop" device type: choose it on a device's page, and devices whose name says so
  (MacBook, ThinkPad, Chromebook, ZenBook, IdeaPad, Latitude, EliteBook…) are recognised as
  laptops automatically. Desktop names (iMac, Mac mini, DESKTOP-…) stay "Computer".

## [0.10.0] - 2026-10-23
### Added
- Devices page: sort by any column (device, type, IP, MAC / vendor, last seen) by clicking its
  header; a second click reverses the order. IP addresses sort numerically, "last seen" puts
  online devices first. The chosen order is remembered in the browser.
- Overview: click a target's name on its tile to rename it in place (Enter saves, Esc cancels);
  the rest of the tile still opens its graphs.

### Changed
- Renaming a monitored device's target also renames the device, so the Overview, Targets and
  Devices pages show the same name (renaming the device already renamed the target).

## [0.9.2] - 2026-10-18
### Fixed
- Bufferbloat test failing with "could not load the line": it asked Cloudflare's speed-test
  server for 250 MB per request and streamed uploads without a length, and ignored the answer.
  Requests are now 25 MB downloads and 4 MB uploads with a length, the server's HTTP status is
  checked, and a refusal is reported as such ("speed.cloudflare.com answered HTTP 403…").
  Upload bytes are only counted once the server has accepted them.
- Ask NetWatch could leave a blank screen on phones: the chat scrolled the whole page instead of
  just the conversation. A page that fails to draw now shows the error and a Reload button
  instead of a blank screen, and a problem in the chat panel no longer takes the page down.

## [0.9.1] - 2026-10-18
### Fixed
- Requests that reach NetWatch without a `Content-Length` header (chunked, as some reverse
  proxies and HTTP/2 connections send them) were refused with "length required", which broke
  Install, Roll back and other buttons. They are now accepted, still capped at 256 KB.

## [0.9.0] - 2026-10-18
### Added
- Exposure scan (new **Exposure** page): a nightly (40 minutes after the update window, setting
  "Nightly exposure scan") and on-demand TCP check of about 30 well-known ports on every LAN
  device seen in the last hour, plus the Home Hub's port forwards, UPnP mappings and DMZ host.
  Ranked findings with advice; new "Exposure" timeline events for new open services and new port
  forwards; open services on each device page.
- Network health score (0-100) for the last 24 hours, 7 or 30 days: internet uptime, connection
  quality, latency under load, home network, fibre line and exposure, each with its detail.
  Shown on the Overview and the new **Health** page.
- Weekly health report, written every Monday morning for the previous week (Monday to Sunday),
  with the score and its change, highlights, breakdown, availability, speed tests and incidents;
  kept for two years, downloadable as Markdown, and can be written on demand.
- Ask NetWatch: a chat panel on every page that answers questions about the network with Claude,
  using read-only tools over NetWatch's own data. Off until an Anthropic API key is saved under
  Admin → Ask NetWatch (encrypted like the other credentials); Claude Opus 5.5 by default, with
  Sonnet 5.5 and Haiku 5.5 as cheaper options; 40 questions an hour at most.
- First-run setup now needs a one-time setup code (printed by `setup.sh`, also in
  `data/setup-token` and the container log), so nobody else on the LAN can create the admin
  account first.
- Sessions end after 7 days without use (new setting "Sign out when idle",
  `NETWATCH_SESSION_IDLE_HOURS`, 0 = never).

### Changed
- Opening NetWatch by a host name it doesn't know now shows which name was refused and the exact
  `NETWATCH_ALLOWED_HOSTS` line to add, instead of "Cannot reach the server: unknown host name".
- HTTP probes follow at most 5 redirects; a redirect loop is reported as `too_many_redirects`.

## [0.8.0] - 2026-10-18
### Added
- Live bandwidth per device from the Decos: "top talkers" now, over 24 hours and 7 days on the
  Overview and the map; live traffic on every map node and link (busy links thicker and
  animated); a bandwidth chart and totals on each device page. 1-minute detail for 7 days
  (new setting "Per-device traffic", 1-90 days), then hourly totals.
- Bufferbloat grade (A+ to F): latency to 1.1.1.1 idle versus while downloading and uploading at
  full speed (Cloudflare speed-test endpoints, ~30 s). Nightly 20 minutes after the update window
  (setting "Nightly bufferbloat test") and on demand from Diagnostics, with history. Packet loss
  caused by the test itself never opens an incident; a test that can't load the line is
  recorded as failed instead of graded.
- Home Hub 4000 integration (Admin → Home Hub): reads uptime, fibre receive/transmit levels and
  link state, and the internet (PPP) session every minute, with Test connection and a raw data
  explorer (credentials hidden). New "Home Hub" timeline events: restarts, fibre or internet
  link down, session reconnects, weak fibre signal. New diagnosis causes: Hub restart confirmed
  (94), Bell link down (96), Bell ended the session (88), weak fibre (72). The map's Hub node
  shows model, firmware, uptime and fibre levels; the Bell report gains a "Bell line" section
  (restarts, link-down periods, reconnects, fibre levels, latest bufferbloat grade).
- Roadmap section in the README.

### Fixed
- Deco client speeds were read as bytes per second; the Deco reports kilobytes per second.

## [0.7.2] - 2026-10-18
### Changed
- Diagnosis page: incidents that ended over an hour ago are diagnosed once and remembered;
  loss during an incident is counted in SQL; device lookups and path tests are fetched once per
  request. A 7-day view on a busy database went from ~6.6 s to ~0.8 s (first load) and ~10 ms
  per refresh after that.
- Incident report and long graphs merge the 1-minute rollups in SQL: a 30-day report ~12 s →
  ~1.7 s, a 90-day graph ~1.5 s → ~0.25 s.
- Database: dropped two timestamp-only indexes (about a third of the samples table's size, and a
  second write per probe); purge and rollups walk each target instead. Added indexes for path
  tests by event/target, devices by target/group, and open events (migration runs on start, ~1 s).
- Raw CSV/JSON export streams an hour at a time instead of loading the whole range.
- Graphs update in place instead of being rebuilt on every refresh; polling skips a tick while
  the previous request is still running, and a slow answer for an old time range no longer
  replaces a newer one.

### Fixed
- Samples written late (timeouts, retried flushes) now reach the 1-minute rollups, so long-range
  loss is no longer under-counted.
- The map's incident replay includes failures merged into the incident later.
- The Bell report's availability only counts downtime inside the report period.
- Live status could fail intermittently while new probe results arrived.

### Security
- Discovery is protected against floods of made-up hardware addresses: at most 30 new devices are
  recorded per minute (one "MAC flood" alert instead), in-memory state is bounded and pruned,
  alerts are capped per type, and the gateway-MAC alert is per gateway.
- A monitored device's ping target only follows it to addresses on the local network.
- One malformed packet or Deco record no longer discards the rest of the batch.
- The DHCP probe reuses one made-up client address instead of a new one every scan; reverse DNS
  lookups run in parallel with a short timeout; full-size frames are captured (large mDNS answers).

## [0.7.1] - 2026-10-18
### Security
- Upgrade agent and `update.sh` no longer trust anything the container can write: the
  rollback record, lock and logs moved to a root-only `.state/` folder; status files to
  `upgrade-status/`, mounted read-only; requests are opened without following symlinks; file
  work in `data/` runs in a throwaway container; rollback records are strictly validated;
  backup pruning can no longer be tricked into deleting other files; downgrades are refused.
  This closes a path from a compromised container to root on the TrueNAS host. Existing
  installs migrate automatically on the agent's next run (no manual step).
- Release images are signed in CI (Sigstore, keyless) and `update.sh` verifies the signature
  and that the release tag matches the image's source commit before deploying.
  `NETWATCH_VERIFY_IMAGES=false` switches this off.
- Deco: the stored owner password is only ever sent to the saved Deco address; names must
  resolve to LAN addresses; redirects to other hosts are refused; the Deco's sign-in key is
  pinned on first use and a changed key stops polling with a security event (re-trust it with
  Test connection); polling stops after 3 failed sign-ins (protects the owner account from
  lockout); session tokens are scrubbed from errors; Deco-supplied names are cleaned.
- Sign-in: attempts are counted when they start (parallel requests can't bypass the lockout),
  IPv6 clients are throttled per /64, password checks are limited to two at a time, the
  password-change form is throttled too, and repeated refusals are logged once per lockout.
- Requests for host names other than an IP address, `localhost` or `NETWATCH_ALLOWED_HOSTS`
  are refused (DNS rebinding); request bodies are capped at 256 KiB (except backup uploads).
- Restores: backups containing triggers or views are rejected, a compressed upload can't
  expand past the size limit or the free space, concurrent restores can't collide, and all
  sessions are signed out after a restore.
- `/metrics` always requires sign-in or the token (also when switched off without a restart);
  the API schema (`/openapi.json`) is no longer served; `/health` no longer returns error text.
- CI: actions pinned to commit SHAs, `VERSION` validated, checkout credentials not persisted.

### Fixed
- Restoring a stored backup no longer reads the whole file on the event loop.
- A failed rollback step no longer leaves NetWatch stopped; a rollback first saves the current
  database as an extra backup.

## [0.7.0] - 2026-10-17
### Added
- Network map page: interactive, automatically laid out map (Internet, Bell, Home Hub, switch,
  TrueNAS, Decos, key devices and per-Deco groups of the rest) with link latency and loss,
  wired/Wi-Fi/logical links, a details panel, and drag-to-arrange with saved positions and reset.
- Incident replay on the map: time slider and incident picker, with the likely cause and failing
  nodes highlighted; "Show on map" links from Diagnosis and event pages.
- Deco mesh integration (Admin → Deco mesh): reads the Decos' local API (owner account; use a
  manager account in the Deco app) for units, backhaul and which device is on which Deco/band.
  The password is encrypted with a key file (`data/.secret_key`) kept outside the database.
- Wi-Fi events on the timeline: Deco offline/online, backhaul changes, roaming and Wi-Fi
  disconnects longer than a minute for monitored devices.
- Diagnosis causes from the Deco data: a Deco went offline, all failing devices on one Deco,
  a monitored device dropped off Wi-Fi or roamed.
- Device pages show the Deco, band and Deco app name.

## [0.6.0] - 2026-10-17
### Added
- Admin page: all tunable settings editable in the web UI with defaults and reset; values
  override `.env`, apply immediately where possible, and a restart banner covers the rest.
- Restart NetWatch from the web UI (Docker's restart policy brings it back).
- Signed-in sessions list with sign-out of one or all other sessions.
- Configurable sign-in lockout (`NETWATCH_LOGIN_MAX_FAILURES`, `NETWATCH_LOGIN_LOCKOUT_S`).
- Security log: sign-ins, failures, lockouts, password and settings changes, backups,
  restores and restarts (also on the Timeline as "Security", hidden by default).
- Database backup and restore from the web page: create, download, delete, restore a stored or
  uploaded (`.db`/`.db.gz`) backup; validated, then applied on restart after a safety copy.

### Changed
- `/metrics` is always routed and answers 404 while disabled, so it can be switched on from
  the UI.

## [0.5.0] - 2026-10-17
### Added
- Diagnosis engine: overlapping failures are grouped into incidents and each is explained by
  ranked, explainable causes with confidence, evidence and next steps, using what failed versus
  what kept working along the network map (TrueNAS link, switch, Home Hub, Bell, internet,
  Decos, devices), NIC events, network anomalies and path tests.
- Pattern detection: failures repeating at a fixed interval or clustering at a time of day.
- Diagnosis page with a live network map (the likely cause's layer highlighted), incidents and
  patterns; the most likely cause on every outage and packet-loss event page.
- Incident report for Bell: printable (save as PDF from the browser) or Markdown download, with
  incidents, evidence, path tests, what was ruled out, per-target measurements and speed tests;
  optionally limited to incidents pointing at Bell.

## [0.4.1] - 2026-10-17
### Changed
- Active scanning is now on by default (hourly). Switch it off on the Devices page; existing
  installs that never touched the toggle pick up the new default.
- Dependabot now proposes major-version updates for GitHub Actions, grouped into one PR (still
  ignored for application dependencies and base images).

## [0.4.0] - 2026-10-17
### Added
- Passive device discovery from ARP, DHCP, mDNS and SSDP broadcasts and the host ARP cache,
  with an in-kernel packet filter so only those packets reach NetWatch.
- Opt-in active scanning: ARP sweep of the local private subnet (rate-limited, max /22), a
  DHCP server probe, SSDP and mDNS queries and reverse DNS via the gateway; scheduled or on
  demand.
- Device inventory: vendor (offline IEEE registry), names, model, advertised services,
  explainable type guess, IP history, online/last seen; merging of randomised MACs by
  hostname plus manual merge/split; rename, type override, notes; one-click monitoring with a
  ping target that follows the device's IP.
- Anomaly events: duplicate IP, more than one DHCP server, ARP storm, gateway MAC change,
  repeated DHCP rejoins, frequent IP changes, double NAT and carrier-grade NAT (from path
  tests), resolver disagreement and NXDOMAIN rewriting. "New device" events after a one-hour
  learning period.
- Devices page and device detail page; anomaly and device events on the Timeline, with an
  evidence section and a hint about the usual cause.

### Changed
- The CI image smoke test also checks raw packet capture with the kernel filter and the
  vendor registry inside the hardened container.

## [0.3.0] - 2026-10-17
### Added
- Outage detection with hysteresis (3 failures to declare down, 3 successes to recover) and a
  separate packet-loss state for intermittent loss; outage times run from first failure to
  first success.
- Event timeline (outages, packet loss, NIC link/speed/errors, NetWatch start/stop) with an
  event detail page: latency around the event, concurrent failures, NIC events, path tests.
- Active incidents and last-24-hours events on the Overview; outage and loss periods shaded
  on latency graphs.
- TrueNAS NIC telemetry from sysfs: link down/up, sub-poll flaps via carrier changes,
  speed/duplex renegotiation, error and CRC counters, per-minute counter history; watches
  the physical ports beneath a bridge, bond or VLAN.
- Path tests with mtr: scheduled for public targets, automatic when an outage starts, and on
  demand; reports the first hop of persistent loss (ignoring silent hops) and route changes.
- Throughput tests with iperf3 (LAN and WAN servers, download and upload, manual or
  scheduled).
- Diagnostics page (NIC status, path tests, throughput servers and results).

### Changed
- The container image now includes `mtr-tiny` and `iperf3`.
- `/health` also checks the incident recorder and NIC monitor.

## [0.2.0] - 2026-10-17
### Added
- Sign-in for everything: first-run admin setup, scrypt password hashes, server-side
  sessions in HttpOnly SameSite=Strict cookies, login lockout after 5 failures, CSRF header
  check, password change, and `python -m netwatch.reset_password` for recovery.
- In-app updates: System page with running/latest version, release notes, Install button,
  live update log, update history and Roll back.
- Automatic updates with a policy (off, notify, patch, minor) and a nightly maintenance window;
  bug-fix releases install automatically by default, larger releases wait for a click. Failed
  automatic updates are rolled back and not retried.
- Host-side upgrade agent (`scripts/upgrade-agent.sh`), installed by `setup.sh` as a TrueNAS
  cron job through the middleware so it survives TrueNAS upgrades.
- CI publishes a GitHub Release (notes from this changelog) for every version tag, after the
  image is pushed; the update check reads those releases.
- Optional bearer token for Prometheus scraping of `/metrics`.

### Changed
- `docker-compose.yml` mounts `./upgrade` for the upgrade agent.
- Releases are automatic: merging a PR that bumps `VERSION` makes CI publish the versioned
  image, the git tag and the GitHub Release (no manual tagging).
- Dependabot ignores major-version bumps (and Python/Node base-image minor bumps), which are
  planned deliberately instead.

## [0.1.0] - 2026-10-17
### Added
- Phase 1 scaffold: FastAPI (Python 3.12) backend, React + Vite + TypeScript frontend,
  single Docker image published to GHCR.
- Probe engine: async scheduler with per-target intervals (ICMP from 1 s), ICMP, DNS, TCP
  connect and HTTP probes, concurrency limit and jittered start.
- Storage: SQLite (WAL) with raw samples (7-day retention), 1-minute rollups (90-day
  retention), hourly purge and incremental vacuum, Alembic migrations.
- First-run defaults: auto-detected default gateway, 1.1.1.1, 8.8.8.8, and DNS resolver
  comparison (gateway vs Cloudflare vs Google).
- Dashboard: health summary, grouped live status tiles, SmokePing-style latency graphs
  (median, p5–p95 / p25–p75 smoke, loss bars) from 15 minutes to 90 days, run-test-now,
  CSV/JSON export, target management.
- Operations: `/health` endpoint and container health check, structured JSON logs, optional
  Prometheus `/metrics`, online database backup CLI.
- Deployment: host networking with only `NET_RAW` and `NET_ADMIN`, read-only root
  filesystem, `setup.sh`, and `update.sh` with pre-update database backup, health check
  and automatic rollback (`--rollback` for manual undo).
- CI: lint, tests, shellcheck and image build on every push; image publish on `main` and
  version tags.
