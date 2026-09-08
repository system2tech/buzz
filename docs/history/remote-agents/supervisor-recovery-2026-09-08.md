# Supervisor recovery verification — 2026-09-08

Dated evidence, not live server configuration. See [current supervision](../../s2-manager-supervisor.md).

## Verified 2026-09-08

- Six isolated supervisor tests passed, covering retained live managers, stopped managers, unrelated sessions, and failed or malformed status queries.
- Systemd accepted the unit; the service is enabled and the old timer disabled.
- Session `cfc9c514` and PID `85892` survived the 07:30:04 and 07:32:04 UTC checks, with no supervisor restarts.
- At 07:30:55 UTC the manager replied to Khoi's pending questions in Buzz. The reporter resumed publishing `awake` status.
- Deployed script and override hashes match the files in this checkout. A full server reboot was not performed during validation.

## Exact identity correction, 2026-09-08

The initial persistent-service fix still matched any background session in the manager directory. The corrected supervisor and reporter share the manager's full UUID. Thirteen supervisor tests include a live sibling in the same directory while the real manager is stopped or missing, first-launch retry identity, and flag-free resume. All 71 supervisor/reporter/installer tests passed.

The live manager was pinned to `cfc9c514-7134-4748-84d5-4596b53f0b44` and resumed with that same UUID. Deployment exposed Claude's copy-on-extra-resume-options behavior; the unintended copy was stopped, and the final resume command uses saved options. The prior script and reporter are backed up with the suffix `.before-session-id-20260908`. This change needs no relay restart.

At 07:53:48 UTC the scheduled supervisor check confirmed that exact UUID, with no service restarts. The relay received a fresh `awake` report for the manager at 07:52:14 UTC. All three deployed Python/shell files match the checkout by SHA-256.

## Documentation audit — 2026-09-08

Live audit confirmed the existing manager, watcher, worker supervisor and reporter were active; invoking their documented start command left the manager session intact. Listing sessions through `sudo -iu` worked. The worker template/supervisor still target the existing account, while the reporter installer writes a fixed system unit. This is evidence for single-manager recovery only, not multi-user onboarding or five-manager recovery. Unrelated failed host units were not treated as Buzz failures.
