# Charm development

Keep Juju orchestration in src/charm.py and native file/service work in
src/workload.py. Run `uv run tox -e lint,unit`, pack with charmcraft's uv plugin,
and use pytest/Jubilant for nonproduction integration checks. Keep pyproject.toml
and uv.lock aligned. Keep documentation links portable.

Never add workload-upgrade behavior or source compilation. Use Ubuntu's signed
APT repositories for initial installation and preserve existing installations.
Refresh must preserve configuration, the running process and package version.

Tests require an explicit controller-qualified nonproduction model and must
retain caller-owned models, applications and machines. Inspect live tests before
running them. Never change production DNS records or delegation during testing.
