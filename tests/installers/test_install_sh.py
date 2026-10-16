"""Exercise the real installer offline with disposable projects and fake CLIs.

Run from the repository root: python3 -m unittest discover -s tests/installers -v
No dependency installer or network command is executed by these tests.
"""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


INSTALLER = (
    Path(__file__).resolve().parents[2]
    / "Leon TV/Leon TV/install.sh"
)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="leon-tv-installer-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "Leon TV"
        self.root.mkdir()
        (self.root / "backend").mkdir()
        (self.root / "frontend").mkdir()
        (self.root / "backend/config.yaml.example").write_text("example: true\n")
        (self.root / "frontend/package-lock.json").write_text("{}\n")
        shutil.copyfile(INSTALLER, self.root / "install.sh")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "commands.log"
        self.env = os.environ.copy()
        for name in ("LEON_TV_SKIP_FRONTEND_BUILD", "XYQ_SKIP_FRONTEND_BUILD"):
            self.env.pop(name, None)
        self.env.update(PATH=str(self.bin), INSTALLER_TEST_LOG=str(self.log))
        for command in ("dirname", "cp", "mkdir", "cat"):
            (self.bin / command).symlink_to(shutil.which(command))
        self.fake("node", 'printf "%s\\n" "${FAKE_NODE_VERSION:-v20.9.0}"')
        self.fake("npm", 'test "$1" != "${FAKE_NPM_FAIL:-}"')
        self.fake("uv", 'test "${FAKE_UV_FAIL:-0}" != 1')
        self.fake("python3", '''
if [ "$1 $2" = "-m pip" ]; then
  test "${FAKE_PIP_FAIL:-0}" != 1
elif [ "$1 $2" = "-m venv" ]; then
  mkdir -p "$3/bin"
  printf 'export PATH="%s/bin:$PATH"\\n' "$INSTALLER_TEST_ROOT" > "$3/bin/activate"
else
  printf 'Python 3.11.0\\n'
fi
''')
        (self.bin / "python").symlink_to(self.bin / "python3")
        self.env["INSTALLER_TEST_ROOT"] = str(self.root)

    def fake(self, name, body):
        command = self.bin / name
        command.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "{name} $*" >> "$INSTALLER_TEST_LOG"\n'
            + body
            + "\n"
        )
        command.chmod(0o755)

    def run_installer(self, **overrides):
        result = subprocess.run(
            ["/bin/bash", str(self.root / "install.sh")],
            env={**self.env, **overrides},
            capture_output=True,
            text=True,
            timeout=10,
        )
        commands = self.log.read_text().splitlines() if self.log.exists() else []
        return result, commands

    def test_rejects_old_node_before_installing_dependencies(self):
        for version in ("v18.20.8", "v20.8.1"):
            with self.subTest(version=version):
                self.log.unlink(missing_ok=True)
                result, commands = self.run_installer(FAKE_NODE_VERSION=version)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("Node.js 20.9+", result.stderr)
                self.assertFalse(any(cmd.startswith(("uv ", "npm ")) for cmd in commands))

    def test_current_node_installs_and_builds(self):
        result, commands = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("uv sync", commands)
        self.assertIn("npm ci", commands)
        self.assertIn("npm run build", commands)
        self.assertIn("Leon TV installation complete", result.stdout)
        self.assertIn("npm start", result.stdout)
        self.assertTrue((self.root / "backend/config.yaml").exists())

    def test_newer_node_and_no_lockfile_use_npm_install(self):
        (self.root / "frontend/package-lock.json").unlink()
        result, commands = self.run_installer(FAKE_NODE_VERSION="v22.0.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("npm install", commands)

    def test_skip_build_keeps_legacy_alias_and_recommends_dev(self):
        for variable in ("XYQ_SKIP_FRONTEND_BUILD", "LEON_TV_SKIP_FRONTEND_BUILD"):
            with self.subTest(variable=variable):
                self.log.unlink(missing_ok=True)
                result, commands = self.run_installer(**{variable: "1"})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("npm run build", commands)
                self.assertIn("npm run dev", result.stdout)
                self.assertNotIn("npm start", result.stdout)

    def test_new_skip_flag_takes_precedence(self):
        result, commands = self.run_installer(
            LEON_TV_SKIP_FRONTEND_BUILD="0", XYQ_SKIP_FRONTEND_BUILD="1"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("npm run build", commands)

    def test_frontend_failure_stops_without_success_message(self):
        for failing_command in ("ci", "run"):
            with self.subTest(command=failing_command):
                self.log.unlink(missing_ok=True)
                result, commands = self.run_installer(FAKE_NPM_FAIL=failing_command)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertNotIn("installation complete", result.stdout)
                if failing_command == "ci":
                    self.assertNotIn("npm run build", commands)

    def test_backend_failure_stops_before_frontend(self):
        result, commands = self.run_installer(FAKE_UV_FAIL="1")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(any(cmd.startswith("npm ") for cmd in commands))
        self.assertNotIn("installation complete", result.stdout)

    def test_failed_uv_install_falls_back_to_venv(self):
        (self.bin / "uv").unlink()
        result, commands = self.run_installer(FAKE_PIP_FAIL="1")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("python3 -m pip install --user uv", commands)
        self.assertIn("python3 -m venv venv", commands)
        self.assertIn("python3 -m pip install --upgrade pip", commands)
        self.assertNotIn("installation complete", result.stdout)

    def test_unavailable_uv_uses_pip_successfully(self):
        (self.bin / "uv").unlink()
        result, commands = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("python3 -m venv venv", commands)
        self.assertIn("python3 -m pip install -r requirements.txt", commands)
        self.assertIn("npm run build", commands)


if __name__ == "__main__":
    unittest.main()
