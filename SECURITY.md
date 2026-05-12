# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| `1.5.x` (current) | ✅ Security fixes |
| `1.0.x` (v1) | ❌ Use `1.5.x` |
| `0.x` (v0) | ❌ Use `1.5.x` |

## Threat model

MissionDebug v1.5 is designed for **single-robot, local-network use**.
The current scope assumes:

- The agent runs on the robot itself.
- The backend serves the UI on the same local network as the engineer's
  laptop.
- Network-level trust — there is no authentication. Putting the UI on
  the public internet is **out of scope** for v1.5 and is documented
  in [`v1.5-SPEC.md`](./v1.5-SPEC.md).

If you deploy MissionDebug behind a reverse proxy with auth (nginx +
basic auth, Authelia, Cloudflare Tunnel, etc.), that is on you.

## Reporting a vulnerability

**Please do not file public GitHub issues for security reports.**

Instead, email the maintainer with:

- A description of the issue
- Steps to reproduce
- A proof-of-concept if possible
- Your suggested fix (optional)

Email: open a private security advisory via GitHub at
<https://github.com/mukul-07/missiondebug/security/advisories/new>, or
reach the maintainer through the email on their GitHub profile.

You can expect:

- An acknowledgement within 7 days
- A status update within 30 days (or sooner for critical issues)
- A fix and coordinated disclosure for confirmed vulnerabilities

## Scope

In scope:

- Remote code execution via the agent's HTTP endpoints
- Authentication / authorization bypass (where any exists)
- Path traversal in the MCAP file server
- Arbitrary code execution via crafted YAML configs
- Resource exhaustion attacks against any service

Out of scope:

- Anything requiring an attacker already on the local network with
  shell access to the robot (we trust the network)
- Vulnerabilities in user-supplied ROS message types or in the
  user's own detector rules
- Issues in third-party dependencies that have already been disclosed
  upstream (we'll bump versions, but please report to the upstream
  project for credit)
