# gdnsd-vm development

Use the installed dwellir-charm-development skill for this generic machine charm.
Keep Juju orchestration in src/charm.py and native file/service work in
src/workload.py. Run `uv run tox -e lint,unit`, pack with charmcraft's uv plugin,
and use pytest/Jubilant for nonproduction integration checks. Keep pyproject.toml
and uv.lock aligned. Apply the unslop skill to documentation.

Never add workload-upgrade behavior or source compilation. Use Ubuntu's signed
APT repositories for initial installation and preserve existing installations.
Refresh must preserve configuration, the running process and package version.

n.dwellir.com is off limits. Preserve existing n files and all production DNS.
x.dwellir.com is the retained experiment's writable test zone. Parent dwellir.com
DNS changes require its separate DNSControl repository. Tests require explicit
controller-qualified model input and must retain caller-owned models and apps.
