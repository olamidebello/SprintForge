# SprintForge

**Plan. Build. Test. Deliver.** A self-hosted, multi-tenant project and quality management application, branded with the SprintForge logo and artwork.

Target domain: **sprintforge.netwizzard.tech**. Source code and deployment configuration do not mean this domain is already deployed.

## Run

Python 3.11+ is the only runtime dependency. No package install or external API key is needed.

```sh
python3 app.py
```

Open http://127.0.0.1:8080. Create an account with a password of at least 12 characters. Optionally include a sample project with linked requirements, tickets, tests and executions. Each account owns a separate organization; administrators can add existing accounts to another organization by exact email.

### Platform administrator

Platform privileges are never granted automatically through public registration. On the server, run:

```sh
SPRINTFORGE_DB=/var/lib/sprintforge/sprintforge.db sudo -u sprintforge -E python3 /opt/sprintforge/manage.py create-superadmin YOUR_EMAIL --name 'Your name'
```

For local development use `python3 manage.py create-superadmin YOUR_EMAIL`. The command promotes an existing account or securely prompts for a new password. No shared or hard-coded admin password is shipped. Sign in and open **Super admin**.

## Implemented workflows

- Persistent accounts with scrypt password hashes, expiring hashed session tokens, CSRF protection and login attempt limits.
- Organization switching, organization creation, scoped membership and six roles: owner, administrator, manager, developer, tester and viewer.
- Projects, epics/stories/tasks/bugs, priorities, assignments, story points, acceptance criteria and target dates.
- Sprint planning, sprint status management, Kanban columns and status changes.
- Requirements linked to tickets; tickets linked to sprints and releases.
- Test cases with steps, expected results, categories, linked tickets and required quality gates.
- Immutable test executions with actual results, environment, evidence URLs and automatic defect creation on failure.
- Required tests must have a current passing result before linked tickets can be completed. Editing a test invalidates its previous execution for quality gates.
- Release ready/released transitions require current passing executions for required project tests and no open project defects.
- Discussion, traceability matrix, workload and delivery metrics, CSV exports and organization audit history.
- Organization administrator GUI: membership/roles, directory, workspace settings, audit export, brand downloads and password changes.
- Platform super admin GUI: tenant directory and counts, suspend/restore/rename tenants, disable/enable accounts, revoke sessions, grant/revoke super admins, public registration switch and platform audit log.
- Responsive navigation, search, project/status filters, modals and account screens.

## Permission boundaries

Tenant membership is checked server-side for all work APIs. Every linked record must belong to the same tenant and project. Super admins manage platform access through separate endpoints; this does not automatically grant access to tenant work content. Owners cannot be removed through the membership GUI. Only owners can manage organization administrators. Viewer accounts cannot mutate work. Developers/testers cannot delete records. Test executions cannot be edited or deleted. The final active super administrator is protected.

Work updates include a version to reject stale edits. Writes use a serialized SQLite transaction. Changes commit before the API publishes success. Deleting referenced records is blocked to preserve links.

## Deploy to the requested domain

Set the DNS A record for `sprintforge.netwizzard.tech` to your chosen hosting server. Remove an incorrect AAAA record if that server has no IPv6 routing. Ensure ports 80 and 443 are reachable and no other virtual host already owns the domain.

From a trusted management machine with Ansible and SSH access:

```sh
ansible-playbook -i 'SERVER_IP,' -u root deploy/deploy.yml \
  -e sprintforge_email=YOUR_CERTIFICATE_EMAIL
```

Alternatively, on the server after cloning the repository:

```sh
ansible-playbook -i localhost, -c local deploy/deploy.yml \
  -e sprintforge_email=YOUR_CERTIFICATE_EMAIL
```

This installs a dedicated systemd service on loopback port 18870, a domain-specific Nginx virtual host, and a Let's Encrypt certificate. It manages only the SprintForge virtual host, binds its exact hostname on wildcard, loopback and primary IPv4 listeners, and preserves HTTPS through a certificate-aware template. It saves and restores the previous route if Nginx validation or local identity checks fail. Nginx configuration is validated before reload. No unrelated site configuration is removed. Git refuses dirty checkout overwrites. Review your host's existing routing before running it. DNS, certificates and deployment have not been tested against your server by this repository creation.

Use the same playbook for subsequent source updates. Pin `sprintforge_ref` to a reviewed commit or release for controlled deployments. Certbot's system package supplies renewal scheduling; verify the host's renewal timer after installation. Create your super admin using the CLI above.

### Configuration

| Environment variable | Default | Purpose |
|---|---|---|
| `SPRINTFORGE_DB` | `data/sprintforge.db` | Persistent database path |
| `HOST` | `127.0.0.1` | Bind address; keep behind Nginx |
| `PORT` | `8080` | Application port |
| `SPRINTFORGE_SECURE_COOKIE` | `0` | Set `1` on HTTPS deployments |
| `SPRINTFORGE_REGISTRATION` | `1` | Initial public registration setting, overridden by GUI setting |

The supplied stdlib HTTP server is intended for a small, single-instance installation behind Nginx. It has not been load-tested for large enterprises or high concurrency. All proxy requests currently share the loopback IP for login limits, so repeated failures can temporarily throttle sign-ins across users; use an appropriately configured trusted proxy/IP strategy before a large public rollout. There is no implicit trust of client-supplied forwarding headers.

## Verify

```sh
python3 -m unittest discover -s tests -v
node --check static/app.js
python3 -m py_compile app.py manage.py deploy/backup.py
```

Integration tests exercise real HTTP requests: tenant isolation, CSRF, concurrent-edit version checks, member privilege boundaries, cross-project link validation, failed-test defect creation and retesting, stale-test release gates, super admin access and tenant suspension, disabled accounts, login throttling, comments, deletion references and password session revocation.

## Backup and restore

Use SQLite's backup API rather than copying a live WAL database:

```sh
sudo -u sprintforge python3 /opt/sprintforge/deploy/backup.py \
  /var/lib/sprintforge/sprintforge.db /var/lib/sprintforge/backup-YYYY-MM-DD.db
```

Move backups to protected off-server storage according to your retention policy. Restore with the service stopped: replace the database with a verified backup, remove obsolete WAL/SHM sidecars for that database, preserve `sprintforge` ownership and mode 0600, then start the service. Test restores before relying on backups.

## Current scope and operational limits

This is a working first release, not a claim of feature parity with every enterprise SDLC suite. Test runs record manual or external automated results; they do not execute test code. GitHub/CI synchronization, SSO/MFA, email delivery/invitations, self-service password recovery, attachment storage, billing, custom workflow builders, dependency/Gantt planning and native mobile packages are not implemented. Evidence is stored as HTTPS links. Roadmap dates are target fields, not a Gantt scheduler. No automatic production deployment is triggered by release status.

Baseline terms/privacy information is included in the UI. The installation operator must supply its verified legal identity, contact and appropriate policies before a public commercial launch. No license granting third-party redistribution rights is included.

## Artwork

`static/brand-board.webp` preserves the original generated logo/artwork composition. `static/logo.webp` and `static/hero.webp` are cropped from that board and used throughout the account screens, navigation and dashboard. Admins can download each through **Administration → Brand assets**. These are raster assets; no vector master is claimed.

## One-command server installation or update

The bootstrap script installs Git and Ansible on Debian/Ubuntu, obtains a fresh copy of this repository and runs the deployment playbook locally. It serializes deployments with a lock and keeps the application database outside the source checkout. Existing databases are backed up consistently to `/var/backups/sprintforge` before source updates. Backups are retained until the operator rotates them.

After pointing DNS at the target server, run there (replace the email):

```sh
curl -fsSLo /tmp/sprintforge-deploy.sh https://raw.githubusercontent.com/olamidebello/SprintForge/main/scripts/deploy.sh
sudo bash /tmp/sprintforge-deploy.sh YOUR_CERTIFICATE_EMAIL
```

Repeat the same commands to pull and deploy subsequent updates. The script stops on errors; it does not force-overwrite dirty application checkouts. It checks the application, Nginx routing, and public HTTPS. Existing deployments get a pre-update backup, but this is not an automatic rollback mechanism. If a deployment fails, use the reported task and service logs to diagnose it before retrying.

### Repair: domain serves Postmaster or another application

Update and rerun `scripts/deploy.sh` using the command above. The playbook now explicitly binds SprintForge on loopback and the primary server IPv4 address, because an IP-specific Nginx listener can take precedence over a wildcard listener before hostname selection. This addresses that routing scenario without changing Postmaster's virtual host. It also rejects duplicate exact SprintForge names, verifies the enabled route is actually loaded, checks application identity on both local addresses and checks public routing before certificate issuance. A missing Nginx include or conflicting exact hostname stops deployment and saves private diagnostics at `/var/log/sprintforge-nginx-diagnostic.log`; it does not automatically rewrite unrelated configuration. Confirm or correct DNS if the public routing check still fails.

## One-shot Olamidepanel domain repair

If the SprintForge domain shows Olamidepanel, Postmaster or another site, run this on the target server:

```sh
ansible-pull -U https://github.com/olamidebello/SprintForge.git -C main \
  -d /opt/sprintforge-hard-repair-source -i localhost, -c local \
  ansible/hard-repair.yml -e sprintforge_email=dobcalldemo@gmail.com
```

This runs one locked repair, updates a clean application checkout, backs up an existing database, refreshes the dedicated systemd service, inspects active Nginx configuration, removes only the exact SprintForge hostname from conflicting server-name directives, and installs one explicitly included managed route for the server's configured IPv4 and global IPv6 addresses. Other hostnames and server blocks remain in place. Configurations containing only SprintForge receive an inert `.invalid` name so their unrelated directives are retained. It verifies local and public identity, tests HTTP ACME challenge routing, obtains or reuses a valid certificate, verifies HTTPS identity and health, and enables renewal with reload. No default administrator password is installed.

Before writes, original configuration is saved privately under `/var/lib/sprintforge-hard-repair/TIMESTAMP-PID/`. If routing/TLS/public checks fail, only changed Nginx files are restored; application source, installed packages, service changes, data and certificates are retained. This is not a full host rollback. Reports are written to `/var/lib/sprintforge-hard-repair/report.json`. Review private logs before sharing them. The repair cannot fix DNS held at your registrar, firewall/provider restrictions, missing DNS ownership, or unrelated invalid Nginx configurations: those failures stop and report the stage instead of claiming success. Do not run the original generic deployment playbook after this repair; use this same repair entry point for future updates so the managed route remains consistent.

### Automatic backend-port conflict repair

The hard-repair playbook now stops only the SprintForge unit, selects a free loopback port (18870–18970, preferring its persisted selection), writes `/etc/sprintforge/backend-port`, and applies a dedicated systemd override with an explicit application command and environment. It verifies the actual backend page and JSON health before pointing Nginx at that port. It does not kill processes occupying other ports. Original changed configuration is backed up; service and socket diagnostics remain in the private repair log if checks fail. Nginx rollback restores the prior configuration only; backend/service changes are retained. Use `ansible/hard-repair.yml` for subsequent updates because the original generic deployment assumes port 18870.

## Rebuild and repair from a fresh release

```sh
ansible-pull -U https://github.com/olamidebello/SprintForge.git -C main \
  -d /opt/sprintforge-hard-repair-source -i localhost, -c local \
  ansible/rebuild-and-repair.yml -e sprintforge_email=dobcalldemo@gmail.com
```

This creates a separate fresh checkout under `/opt/sprintforge-releases`, restores service-readable permissions on tracked code/assets, checks Python compilation and the application test suite, verifies required assets, and activates the checked-out release through the dedicated SprintForge systemd override. It then performs backend-port selection, application identity checks, domain routing repair and public HTTPS verification. Existing databases remain at `/var/lib/sprintforge/sprintforge.db`; old source releases are retained. A failed rebuild does not delete old releases or application data. Nginx rollback remains limited to changed Nginx files; service activation is retained for diagnosis. External DNS/firewall failures are reported rather than silently bypassed. No JavaScript bundling step is needed: this application serves its tracked HTML, CSS and JavaScript directly.
