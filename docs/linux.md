# Linux desktop build

The Linux desktop package is currently an **x86_64 preview**. CI builds it on
GitHub's Ubuntu runner as a portable `.tar.gz`; it is not currently packaged as
`.deb`, `.rpm`, or AppImage. The CI smoke test launches the Tauri window under
Xvfb and Openbox, checks that the window appears and that its local map server
responds, and uploads a screenshot. This verifies startup on the CI image, not
compatibility with every Linux distribution, desktop environment, or graphics
driver.

## Download and run

1. Download `HebrewMaps-Linux-<version>.tar.gz` from the
   [latest GitHub release](https://github.com/rishumit/hebrew-maps/releases/latest).
   Linux archives are attached to version releases after the Linux workflow is
   merged. For an unreleased branch build, download the `HebrewMaps-Linux-*`
   artifact from the
   [Linux Desktop workflow](https://github.com/rishumit/hebrew-maps/actions/workflows/linux.yml).
2. Install the WebKitGTK and GTK runtime libraries. On Ubuntu/Debian:

   ```bash
   sudo apt update
   sudo apt install libwebkit2gtk-4.1-0 libgtk-3-0 \
     libayatana-appindicator3-1 librsvg2-2
   ```

3. Extract the archive and launch the app:

   ```bash
   tar -xzf HebrewMaps-Linux-<version>.tar.gz
   cd HebrewMaps-Linux-<version>
   ./run-hebrew-maps.sh
   ```

The package includes the application and demo data. On first launch, use
**Menu → Updates → Update all** to download the current Israel map data. Data
updates, including offline ZIP updates, work on Linux through the app's update
page.

## Updates

The application self-updater currently supports installed Windows builds only.
On Linux, download and extract a newer release manually. Stop the app, extract
the new archive into a new directory, then copy the old installation's `data/`
contents into the new package's `data/` directory before launching it. Keep the
old directory until the new version starts successfully.

## Build and CI

The [Linux Desktop workflow](../.github/workflows/linux.yml) runs on pushes to
`main`, pull requests to `main`, version tags (`v*.*.*`), and manual dispatch.
It builds the Python server with PyInstaller, builds the Tauri desktop binary,
assembles the portable archive, smoke-tests the server and GUI, and uploads the
package and GUI screenshot as workflow artifacts. On a version tag, the Linux
archive is added to the release created by the Windows Installer workflow.

The hosted build uses Ubuntu and installs GTK/WebKitGTK development packages,
Rust, Python 3.12, PyInstaller, and the icon-generation dependencies. Consult
the workflow for the exact build commands and package list.

## Known limitations

- Only x86_64 Linux is built and exercised in CI.
- The portable archive relies on system WebKitGTK/GTK libraries.
- The GUI test is automated and headless; it does not validate interactive
  behavior across real desktop environments or hardware.
- Application updates are manual on Linux; data updates remain available in
  the app.
