# gdnsd-vm development

Use the installed dwellir-charm-development skill for this generic machine charm.
Keep Juju orchestration in src/charm.py and native file/service work in
src/workload.py. Run `uv run tox -e lint,unit`, pack with charmcraft's uv plugin,
and use pytest/Jubilant for nonproduction integration checks. Keep pyproject.toml
and uv.lock aligned. Apply the unslop skill to documentation.

Never add workload-upgrade behavior or source compilation. Use Ubuntu's signed
APT repositories for initial installation and preserve existing installations.
Refresh must preserve configuration, the running process and package version.

Production n.dwellir.com is off limits. Its delegation, glue, records and source
GSLB configuration must remain unchanged. Direct-only n test copies exist on
the four authorized hyperborea gdnsd models. Update those copies only when
explicitly requested, then restore the charm n-zone guard. Never register or
delegate production n to these test servers.
x.dwellir.com is the retained experiment's writable test zone. Parent dwellir.com
DNS changes require its separate DNSControl repository. Tests require explicit
controller-qualified model input and must retain caller-owned models and apps.
