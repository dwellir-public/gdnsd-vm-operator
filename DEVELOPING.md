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
CHARM_PATH="$PWD/gdnsd-vm_amd64.charm" \
TEST_MODEL=CONTROLLER:MODEL TEST_APPLICATION=gdnsd-vm TEST_MACHINE=0 \
GDNSD_NONPRODUCTION=1 GDNSD_DEPLOY=1 \
uv run tox -e integration
```

Omit `GDNSD_DEPLOY` to validate an existing deployment. Set `GDNSD_REFRESH=1`
to exercise refresh from the specified local artifact. Use a different artifact
revision for refresh checks. Record-writing checks use the lab zone defined in
`tests/integration/test_live.py`. Global configuration changes are tested only on
a fresh model and require `GDNSD_TEST_GLOBAL=1`. Set `GDNSD_TEST_GEOIP=1` on that fresh
model to exercise URL memory, data validation and replacement with the pinned
MaxMind test fixture. This is test data, not a production GeoIP update.

## Releases

The [release workflow](.github/workflows/release.yml) runs manually. It builds
Ubuntu 24.04 on amd64, uploads the charm to Charmhub's `latest/edge` channel,
and creates a GitHub release with the artifact and source commit. It uses the
repository's `CHARMHUB_TOKEN` secret as `CHARMCRAFT_AUTH` during upload.

```bash
gh workflow run release.yml --repo dwellir-public/gdnsd-vm-operator --ref main
```

A normal push runs lint and unit tests. It does not publish a Charmhub revision.
For documentation changes, push the commit without dispatching the release workflow.

## SSH helper verification

Run `uv run tox -e lint,unit`. The helper unit tests exercise protocol bounds,
conflicts, symlinks, native-validation failure, ordered bridge transitions,
idempotence, complete rollback, rollback failure, checkpoint metadata and charm
status integration. Native commands are replaced at the workload boundary;
these tests do not establish live gdnsd or restricted SSH behavior.

For manual verification on an explicitly selected retained nonproduction unit,
first build and deploy the local charm artifact through the integration workflow
above. Verify that refresh retains configuration hashes, GeoIP hash, package
version and gdnsd PID. Inspect helper ownership and permissions, then run the
fixed helper command with `{"version":1,"operation":"status"}` on stdin.
Exercise snapshot, validate and apply using a separately provisioned restricted
SSH account. Test invalid native configuration, concurrent transactions and
rollback only on the nonproduction unit. Retain caller-owned model resources.
Never run fault-injection tests on production DNS.
