__version__ = "v2.3.3=beta"

if __name__ == "__main__":
    print("Runner online.")
    import os
    import shlex
    import shutil
    import socket
    import subprocess
    import platform
    from pathlib import Path
    from dotenv import load_dotenv

    def run_cmd(cmd, check=True):
        try:
            res = subprocess.run(
                cmd,
                check=check,
                shell=False,
                text=True,
                capture_output=True
            )
            stdout = res.stdout.strip() if res.stdout else ""
            stderr = res.stderr.strip() if res.stderr else ""
            return {
                "ok": res.returncode == 0,
                "code": res.returncode,
                "stdout": stdout,
                "stderr": stderr,
            }
        except subprocess.CalledProcessError as e:
            return {
                "ok": False,
                "code": e.returncode,
                "stdout": e.stdout.strip() if e.stdout else "",
                "stderr": e.stderr.strip() if e.stderr else str(e),
            }
        except Exception as e:
            return {
                "ok": False,
                "code": -1,
                "stdout": "",
                "stderr": str(e),
            }

    def print_result(title, result):
        print(f"\n--- {title} ---")
        print(f"OK: {result['ok']}")
        print(f"Exit Code: {result['code']}")
        if result["stdout"]:
            print("STDOUT:")
            print(result["stdout"])
        if result["stderr"]:
            print("STDERR:")
            print(result["stderr"])

    def file_exists(path):
        return Path(path).exists()

    def is_port_in_use(host, port):
        try:
            with socket.create_connection((host, int(port)), timeout=1):
                return True
        except Exception:
            return False

    def find_nginx_configs():
        candidates = [
            "/etc/nginx/nginx.conf",
            "/etc/nginx/sites-enabled",
            "/etc/nginx/sites-available",
            "/usr/local/etc/nginx/nginx.conf",
        ]

        found = []
        for path in candidates:
            if file_exists(path):
                found.append(path)
        return found

    def find_gunicorn_configs():
        candidates = [
            "./gunicorn.conf.py",
            "./gunicorn_config.py",
            "/etc/gunicorn.conf.py",
            "/etc/gunicorn/config.py",
        ]

        found = []
        for path in candidates:
            if file_exists(path):
                found.append(path)
        return found

    def start_gunicorn():
        gunicorn_bin = shutil.which("gunicorn")
        if not gunicorn_bin:
            return {
                "ok": False,
                "code": -1,
                "stdout": "",
                "stderr": "gunicorn is not installed or not in PATH.",
            }

        gunicorn_app = os.getenv("ZIUX_GUNICORN_APP", "app:app")
        gunicorn_bind = os.getenv("ZIUX_GUNICORN_BIND", "127.0.0.1:8000")
        gunicorn_workers = os.getenv("ZIUX_GUNICORN_WORKERS", "2")
        gunicorn_conf = os.getenv("ZIUX_GUNICORN_CONF", "").strip()

        host, port = gunicorn_bind.split(":")
        if is_port_in_use(host, port):
            return {
                "ok": False,
                "code": -1,
                "stdout": "",
                "stderr": f"Gunicorn bind port {gunicorn_bind} is already in use.",
            }

        cmd = [
            gunicorn_bin,
            "-b", gunicorn_bind,
            "-w", gunicorn_workers,
        ]

        if gunicorn_conf:
            if file_exists(gunicorn_conf):
                cmd.extend(["-c", gunicorn_conf])
            else:
                return {
                    "ok": False,
                    "code": -1,
                    "stdout": "",
                    "stderr": f"Specified gunicorn config does not exist: {gunicorn_conf}",
                }

        cmd.append(gunicorn_app)

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            return {
                "ok": True,
                "code": 0,
                "stdout": f"Started gunicorn with PID {proc.pid}\nCommand: {' '.join(cmd)}",
                "stderr": "",
                "process": proc,
            }
        except Exception as e:
            return {
                "ok": False,
                "code": -1,
                "stdout": "",
                "stderr": f"Failed to start gunicorn: {e}",
            }

    def start_nginx():
        nginx_bin = shutil.which("nginx")
        if not nginx_bin:
            return {
                "ok": False,
                "code": -1,
                "stdout": "",
                "stderr": "nginx is not installed or not in PATH.",
            }

        return run_cmd(["sudo", nginx_bin])

    load_dotenv()

    whitelist_mods_temp = os.environ.get("ZIUX_AUTH_MODS", "")
    whitelist_mods = [ip.strip() for ip in whitelist_mods_temp.split(",") if ip.strip()]

    hostname = platform.system()
    release = platform.release()

    print(f"""
 _______  ___   __   __  __   __          _______  ______   __   __ 
|       ||   | |  | |  ||  |_|  |        |       ||      | |  | |  |
|____   ||   | |  | |  ||       |        |    ___||  _    ||  | |  |
 ____|  ||   | |  |_|  ||       |        |   |___ | | |   ||  |_|  |
| ______||   | |       | |     |  ___    |    ___|| |_|   ||       |
| |_____ |   | |       ||   _   ||_  |   |   |___ |       ||       |
|_______||___| |_______||__| |__|  |_|   |_______||______| |_______|

Ziux. 2026
:-)   Version: {__version__}

[!]   Authenticated Admin IPS: {whitelist_mods}
[!]   Host: {hostname}_{release}
""")

    print("[*] Checking nginx config / gunicorn configs...")

    nginx_configs = find_nginx_configs()
    gunicorn_configs = find_gunicorn_configs()

    if nginx_configs:
        print("[+] Found nginx config paths:")
        for path in nginx_configs:
            print(f"    - {path}")
    else:
        print("[-] No common nginx config paths found.")

    if gunicorn_configs:
        print("[+] Found gunicorn config paths:")
        for path in gunicorn_configs:
            print(f"    - {path}")
    else:
        print("[-] No common gunicorn config paths found.")

    print("\n[*] Running nginx config test...")
    nginx_bin = shutil.which("nginx")
    if not nginx_bin:
        print("[-] nginx not found in PATH.")
    else:
        nginx_test = run_cmd(["sudo", nginx_bin, "-t"], check=False)
        print_result("nginx -t", nginx_test)

        if not nginx_test["ok"]:
            print("\n[-] nginx config test failed. Not starting services.")
            raise SystemExit(1)

    print("\n[*] Starting gunicorn...")
    gunicorn_start = start_gunicorn()
    print_result("Start Gunicorn", gunicorn_start)

    if not gunicorn_start["ok"]:
        print("\n[-] Gunicorn failed to start. Not starting nginx.")
        raise SystemExit(1)

    print("\n[*] Starting nginx...")
    nginx_start = start_nginx()
    print_result("Start nginx", nginx_start)

    if not nginx_start["ok"]:
        print("\n[-] nginx failed to start.")
        raise SystemExit(1)

    print("\n[+] Runner completed successfully.")
