from __future__ import annotations

import posixpath
import shlex
from dataclasses import dataclass, field


FILES = {
    "/etc/issue": "Ubuntu 22.04.4 LTS \\n \\l\n",
    "/etc/os-release": (
        'PRETTY_NAME="Ubuntu 22.04.4 LTS"\nNAME="Ubuntu"\n'
        'VERSION_ID="22.04"\nVERSION_CODENAME=jammy\nID=ubuntu\n'
    ),
    "/etc/hostname": "web-prod-02\n",
    "/etc/passwd": (
        "root:x:0:0:root:/root:/bin/bash\n"
        "daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin\n"
        "www-data:x:33:33:www-data:/var/www:/usr/sbin/nologin\n"
        "deploy:x:1001:1001:Deploy User:/home/deploy:/bin/bash\n"
    ),
    "/home/deploy/.bash_history": "sudo systemctl restart nginx\ndocker ps\ngit pull\n",
    "/var/www/app/.env": "APP_ENV=production\nDATABASE_HOST=db.internal\n",
}

DIRECTORIES = {
    "/": ["bin", "boot", "dev", "etc", "home", "opt", "proc", "root", "run", "srv", "tmp", "usr", "var"],
    "/home/deploy": ["app", "backups", "notes.txt"],
    "/home/deploy/app": ["README.md", "docker-compose.yml", "logs"],
    "/var/www": ["app", "html"],
    "/var/www/app": ["current", "releases"],
    "/tmp": ["systemd-private-a91c", ".cache"],
    "/etc": ["hostname", "hosts", "issue", "nginx", "os-release", "passwd", "ssh"],
}


@dataclass
class DecoyShell:
    username: str
    cwd: str = "/home/deploy"
    history: list[str] = field(default_factory=list)

    @property
    def prompt(self) -> str:
        shown = "~" if self.cwd == "/home/deploy" else self.cwd
        return f"{self.username}@web-prod-02:{shown}$ "

    def execute(self, raw: str) -> tuple[str, bool]:
        command = raw.strip()
        if not command:
            return "", False
        self.history.append(command)
        try:
            args = shlex.split(command)
        except ValueError as exc:
            return f"bash: {exc}\n", False
        if not args:
            return "", False

        name = args[0]
        if name in {"exit", "logout"}:
            return "logout\n", True
        if name == "whoami":
            return f"{self.username}\n", False
        if name == "id":
            return "uid=1001(deploy) gid=1001(deploy) groups=1001(deploy),27(sudo),998(docker)\n", False
        if name == "hostname":
            return "web-prod-02\n", False
        if name == "pwd":
            return f"{self.cwd}\n", False
        if name == "uname":
            if "-a" in args:
                return "Linux web-prod-02 5.15.0-119-generic #129-Ubuntu SMP x86_64 GNU/Linux\n", False
            return "Linux\n", False
        if name == "cd":
            target = args[1] if len(args) > 1 else "/home/deploy"
            target = self._path(target)
            if target in DIRECTORIES or target in {"/home", "/var", "/var/www"}:
                self.cwd = target
                return "", False
            return f"bash: cd: {args[1] if len(args) > 1 else target}: No such file or directory\n", False
        if name in {"ls", "dir"}:
            target_arg = next((a for a in args[1:] if not a.startswith("-")), ".")
            target = self._path(target_arg)
            entries = DIRECTORIES.get(target)
            if entries is None:
                return f"ls: cannot access '{target_arg}': No such file or directory\n", False
            return "  ".join(entries) + "\n", False
        if name in {"cat", "head", "tail"}:
            if len(args) < 2:
                return "", False
            target = self._path(args[-1])
            if target in FILES:
                return FILES[target], False
            return f"{name}: {args[-1]}: No such file or directory\n", False
        if name == "history":
            return "".join(f"{i:5d}  {item}\n" for i, item in enumerate(self.history, 1)), False
        if name == "ps":
            return (
                "  PID TTY          TIME CMD\n"
                " 1842 pts/0    00:00:00 bash\n"
                " 1901 pts/0    00:00:00 ps\n"
            ), False
        if name in {"env", "printenv"}:
            return "SHELL=/bin/bash\nUSER=deploy\nHOME=/home/deploy\nLANG=C.UTF-8\nPATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n", False
        if name == "sudo":
            return "[sudo] password for deploy: Sorry, try again.\n", False
        if name in {"curl", "wget"}:
            return "curl: (6) Could not resolve host: temporary failure in name resolution\n" if name == "curl" else "wget: unable to resolve host address\n", False
        if name in {"apt", "apt-get", "yum", "dnf"}:
            return "E: Could not open lock file /var/lib/dpkg/lock-frontend - Permission denied\n", False
        if name in {"docker", "systemctl", "service"}:
            return "Cannot connect to service manager: Permission denied\n", False
        if name in {"clear", "reset"}:
            return "\x1b[2J\x1b[H", False
        if name in {"echo", "printf"}:
            return " ".join(args[1:]) + "\n", False
        if name in {"touch", "mkdir", "chmod", "chown", "rm", "mv", "cp"}:
            return "", False
        if name.startswith("./") or "/tmp/" in name:
            return f"bash: {name}: Permission denied\n", False
        return f"bash: {name}: command not found\n", False

    def _path(self, target: str) -> str:
        if target == "~":
            return "/home/deploy"
        if target.startswith("~/"):
            target = "/home/deploy/" + target[2:]
        if not target.startswith("/"):
            target = posixpath.join(self.cwd, target)
        return posixpath.normpath(target)
