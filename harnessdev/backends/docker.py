"""Docker backend: the sandbox primitives every other module builds on."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path


class BackendError(RuntimeError):
    pass


def _run(cmd: list[str], *, check: bool = True, timeout: float | None = None,
         input: bytes | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout, input=input)
    if check and proc.returncode != 0:
        raise BackendError(f"{' '.join(shlex.quote(c) for c in cmd[:6])} ... failed "
                           f"({proc.returncode}): {proc.stderr.decode(errors='replace')[-2000:]}")
    return proc


@dataclass
class Sandbox:
    """A running container."""

    name: str
    image: str
    workdir: str = "/workspace"

    def exec(self, command: str, *, env: dict[str, str] | None = None, user: str | None = None,
             timeout: float | None = None, check: bool = False, workdir: str | None = None,
             ) -> subprocess.CompletedProcess:
        cmd = ["docker", "exec", "-w", workdir or self.workdir]
        for key, value in (env or {}).items():
            cmd += ["-e", f"{key}={value}"]
        if user:
            cmd += ["-u", user]
        cmd += [self.name, "bash", "-lc", command]
        return _run(cmd, check=check, timeout=timeout)

    def popen(self, command: str, *, env: dict[str, str] | None = None, stdout=None, stderr=None,
              workdir: str | None = None) -> subprocess.Popen:
        cmd = ["docker", "exec", "-w", workdir or self.workdir]
        for key, value in (env or {}).items():
            cmd += ["-e", f"{key}={value}"]
        cmd += [self.name, "bash", "-lc", command]
        return subprocess.Popen(cmd, stdout=stdout, stderr=stderr)

    def copy_in(self, src: Path, dest: str) -> None:
        _run(["docker", "cp", str(src), f"{self.name}:{dest}"])

    def copy_contents(self, src_dir: Path, dest_dir: str) -> None:
        """Copy the contents of ``src_dir`` (not the directory itself) into ``dest_dir``."""
        _run(["docker", "cp", f"{src_dir}/.", f"{self.name}:{dest_dir}"])

    def copy_out(self, src: str, dest: Path) -> None:
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        _run(["docker", "cp", f"{self.name}:{src}", str(dest)])

    def archive(self, path: str, treeish: str | None = None) -> bytes:
        """Return a tar.gz of ``path``; of a git tree-ish inside it when given."""
        if treeish:
            cmd = f"git -C {shlex.quote(path)} archive --format=tar.gz {shlex.quote(treeish)}"
        else:
            cmd = f"tar -C {shlex.quote(path)} -czf - ."
        proc = _run(["docker", "exec", self.name, "bash", "-lc", cmd])
        return proc.stdout

    def pause(self) -> None:
        _run(["docker", "pause", self.name], check=False)

    def unpause(self) -> None:
        _run(["docker", "unpause", self.name], check=False)

    def remove(self) -> None:
        _run(["docker", "rm", "-f", "-v", self.name], check=False)


@dataclass
class DockerBackend:
    """Creates sandboxes and private networks on the local Docker daemon."""

    prefix: str = "hdev"
    created: list[str] = field(default_factory=list)
    networks: list[str] = field(default_factory=list)

    def image_exists(self, image: str) -> bool:
        return _run(["docker", "image", "inspect", image], check=False).returncode == 0

    def build(self, context: Path, tag: str, dockerfile: Path | None = None,
              platform: str | None = None) -> None:
        cmd = ["docker", "build", "-t", tag]
        if dockerfile:
            cmd += ["-f", str(dockerfile)]
        if platform:
            cmd += ["--platform", platform]
        cmd.append(str(context))
        proc = subprocess.run(cmd)
        if proc.returncode != 0:
            raise BackendError(f"docker build failed for {tag}")

    def create_network(self, label: str) -> str:
        name = f"{self.prefix}-{label}-{uuid.uuid4().hex[:8]}"
        _run(["docker", "network", "create", name])
        self.networks.append(name)
        return name

    def start(self, image: str, *, label: str, env: dict[str, str] | None = None,
              mounts: dict[str, str] | None = None, network: str | None = None,
              network_alias: str | None = None, privileged: bool = False,
              cpus: float | None = None, memory: str | None = None,
              workdir: str = "/workspace", command: list[str] | None = None,
              platform: str | None = None, gpus: bool = False) -> Sandbox:
        name = f"{self.prefix}-{label}-{uuid.uuid4().hex[:8]}"
        cmd = ["docker", "run", "-d", "--name", name, "-w", workdir,
               "--add-host", "host.docker.internal:host-gateway"]
        if network:
            cmd += ["--network", network]
            if network_alias:
                cmd += ["--network-alias", network_alias]
        if privileged:
            cmd.append("--privileged")
        if cpus:
            cmd += ["--cpus", str(cpus)]
        if memory:
            cmd += ["--memory", memory]
        if platform:
            cmd += ["--platform", platform]
        if gpus:
            cmd += ["--gpus", "all"]
        for key, value in (env or {}).items():
            cmd += ["-e", f"{key}={value}"]
        for host_path, container_path in (mounts or {}).items():
            cmd += ["-v", f"{host_path}:{container_path}"]
        cmd.append(image)
        cmd += command if command is not None else ["sleep", "infinity"]
        _run(cmd)
        self.created.append(name)
        return Sandbox(name=name, image=image, workdir=workdir)

    def cleanup(self) -> None:
        for name in self.created:
            _run(["docker", "rm", "-f", "-v", name], check=False)
        for name in self.networks:
            _run(["docker", "network", "rm", name], check=False)
        self.created.clear()
        self.networks.clear()


def client_proxy_settings() -> dict[str, str]:
    """Proxy settings the Docker client injects into containers
    (``proxies.default`` in the client's config.json), keyed like the file."""
    config = Path(os.environ.get("DOCKER_CONFIG", Path.home() / ".docker")) / "config.json"
    try:
        return dict(json.loads(config.read_text()).get("proxies", {}).get("default", {}))
    except (OSError, ValueError):
        return {}


def no_proxy_with(*hosts: str) -> str:
    """``hosts`` plus whatever the Docker client would already exempt."""
    inherited = client_proxy_settings().get("noProxy", "")
    return ",".join([h for h in hosts if h] + ([inherited] if inherited else []))
