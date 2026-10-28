# Development

Use Python 3.12 or later, uv, tox, charmcraft and Juju 3.6. Run commands from the
repository root. The lock is the source for reproducible Python dependencies.

```bash
uv sync --group dev
uv run tox -e lint,unit
uv run tox -e format
charmcraft pack --use-lxd
```

Charmcraft packages the Python charm. It does not compile gdnsd. The workload
comes from Ubuntu's prebuilt APT package. No source-build tools are required
inside the workload machine.

## Live tests

Integration tests use pytest and Jubilant with an explicit controller-qualified
nonproduction model. They retain the model, machine, application and test zone.
They never remove caller-owned applications or models. Inspect the tests before
running them against a retained model. A supplied existing application must have
been deployed from the local artifact by this test suite or the recorded manual
deployment. Deployment is opt-in and refuses an existing application.

```bash
CHARM_PATH="$PWD/gdnsd-vm_ubuntu@24.04-amd64.charm" \
TEST_MODEL=CONTROLLER:MODEL TEST_APPLICATION=gdnsd-vm TEST_MACHINE=0 \
GDNSD_NONPRODUCTION=1 GDNSD_DEPLOY=1 \
uv run tox -e integration
```

Omit `GDNSD_DEPLOY` to validate an existing deployment. Set `GDNSD_REFRESH=1`
to exercise refresh from the specified local artifact. Use a different artifact
revision for refresh checks. All record writes are restricted to x.dwellir.com;
production n is never written. Global configuration changes are tested only on
a fresh model without an n zone and require `GDNSD_TEST_GLOBAL=1`. Set `GDNSD_TEST_GEOIP=1` on that fresh
model to exercise URL memory, data validation and replacement with the pinned
MaxMind test fixture. This is test data, not a production GeoIP update.

See [validation](docs/validation.md) for recorded results and known limits.
